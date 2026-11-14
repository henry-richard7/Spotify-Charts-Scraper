from __future__ import annotations

import asyncio
import json
import secrets
import time
from contextlib import asynccontextmanager
from datetime import date as calendar_date, timedelta
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlencode, urlsplit

import httpx
from fastapi import Cookie, FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, Field
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from chart_history import (
    ChartHistoryStorageError,
    close_history_pool,
    entry_entity_uri,
    get_history_pool,
    prepare_chart_history,
    run_history_backfill_batch,
    snapshot_values,
)
from Countries import Countries
from Settings import SpotifySettings
from spotify_charts import SpotifyCharts


@asynccontextmanager
async def lifespan(_: FastAPI):
    global refresh_token
    try:
        pool = await get_history_pool()
        row = await pool.fetchrow(
            "SELECT refresh_token FROM public.spotify_oauth_tokens WHERE singleton = true"
        )
        refresh_token = row["refresh_token"] if row else None
    except Exception as error:
        print(f"[v0] Spotify authorization could not be restored: {type(error).__name__}")
    yield
    await close_history_pool()


app = FastAPI(title="Spotify Charts", docs_url=None, redoc_url=None, lifespan=lifespan)
settings = SpotifySettings()
refresh_token: str | None = None
access_token: str | None = None
access_token_expires_at = 0.0
token_refresh_lock = asyncio.Lock()


async def store_spotify_refresh_token(token: str) -> None:
    pool = await get_history_pool()
    await pool.execute(
        """
        INSERT INTO public.spotify_oauth_tokens (singleton, refresh_token, updated_at)
        VALUES (true, $1, now())
        ON CONFLICT (singleton) DO UPDATE
        SET refresh_token = EXCLUDED.refresh_token, updated_at = now()
        """,
        token,
    )


async def remove_spotify_refresh_token() -> None:
    pool = await get_history_pool()
    await pool.execute("DELETE FROM public.spotify_oauth_tokens WHERE singleton = true")

HISTORY_CHART_TYPES = {
    "daily_top_songs": "track",
    "weekly_top_songs": "track",
    "daily_top_artists": "artist",
    "weekly_top_artists": "artist",
    "daily_top_albums": "album",
    "weekly_top_albums": "album",
}
FOCUSED_ARTISTS = (
    {"uri": "spotify:artist:06HL4z0CvFAxyc27GXpf02", "name": "Taylor Swift"},
    {"uri": "spotify:artist:4UIOuc84ExWojcUzFGtbW8", "name": "Felix"},
)
FOCUSED_ARTIST_URIS = [artist["uri"] for artist in FOCUSED_ARTISTS]
FOCUSED_ARTIST_NAMES = [artist["name"] for artist in FOCUSED_ARTISTS]
FOCUSED_ARTIST_NAME = "Taylor Swift and Felix"
FOCUSED_TRACK_URI = "spotify:track:15fpZUMOQD9ONTjxITFXFT"
FOCUSED_CHART_CACHE_TTL = 180
CHART_PAYLOAD_TTL_SECONDS = 300
CHART_REFRESH_LEASE_SECONDS = 120
focused_chart_cache: dict[tuple[str, str, str], tuple[float, dict[str, Any]]] = {}
focused_chart_cache_lock = asyncio.Lock()
COUNTRY_NAMES = {country.value: country.name.replace("_", " ").title() for country in Countries}


def chart_delivery(payload: dict[str, Any], fetched_at: Any, status: str) -> dict[str, Any]:
    delivered = dict(payload)
    age = max(0, int(time.time() - fetched_at.timestamp())) if hasattr(fetched_at, "timestamp") else 0
    delivered["delivery"] = {
        "fetchedAt": fetched_at.isoformat() if hasattr(fetched_at, "isoformat") else str(fetched_at),
        "ageSeconds": age,
        "isStale": age >= CHART_PAYLOAD_TTL_SECONDS,
        "refreshStatus": status,
    }
    return delivered


async def read_chart_cache(chart_type: str, country: str, requested_date: str):
    pool = await get_history_pool()
    return await pool.fetchrow(
        """SELECT payload, chart_date, fetched_at FROM public.spotify_chart_payload_cache
           WHERE chart_type = $1 AND country_code = $2 AND requested_date = $3""",
        chart_type, country, requested_date,
    )


async def save_chart_cache(chart_type: str, country: str, requested_date: str, payload: dict[str, Any]) -> Any:
    chart_date_value = (payload.get("displayChart") or {}).get("date")
    try:
        chart_date_value = calendar_date.fromisoformat(str(chart_date_value)[:10]) if chart_date_value else None
    except ValueError:
        chart_date_value = None
    pool = await get_history_pool()
    return await pool.fetchval(
        """INSERT INTO public.spotify_chart_payload_cache
             (chart_type, country_code, requested_date, payload, chart_date, fetched_at)
           VALUES ($1, $2, $3, $4::jsonb, $5, now())
           ON CONFLICT (chart_type, country_code, requested_date) DO UPDATE
             SET payload = EXCLUDED.payload, chart_date = EXCLUDED.chart_date, fetched_at = EXCLUDED.fetched_at
           RETURNING fetched_at""",
        chart_type, country, requested_date, json.dumps(payload, separators=(",", ":")), chart_date_value,
    )


async def enqueue_chart_refresh(chart_type: str, country: str, requested_date: str) -> None:
    pool = await get_history_pool()
    await pool.execute(
        """INSERT INTO public.spotify_chart_refresh_jobs (chart_type, country_code, requested_date)
           VALUES ($1, $2, $3) ON CONFLICT (chart_type, country_code, requested_date) DO UPDATE
           SET status = CASE WHEN public.spotify_chart_refresh_jobs.status = 'complete'
                                  OR (public.spotify_chart_refresh_jobs.status = 'failed' AND public.spotify_chart_refresh_jobs.next_attempt_at <= now())
                             THEN 'pending' ELSE public.spotify_chart_refresh_jobs.status END,
               next_attempt_at = CASE WHEN public.spotify_chart_refresh_jobs.status = 'complete'
                                  OR (public.spotify_chart_refresh_jobs.status = 'failed' AND public.spotify_chart_refresh_jobs.next_attempt_at <= now())
                                      THEN now() ELSE public.spotify_chart_refresh_jobs.next_attempt_at END,
               updated_at = now()""",
        chart_type, country, requested_date,
    )


class ChartHistoryBackfillRequest(BaseModel):
    chart_type: str
    country: str
    target_date: calendar_date
    start_date: calendar_date
    entity_uris: list[str] = Field(default_factory=list, max_length=200)


class SelectionInput(BaseModel):
    entity_type: Literal["track", "artist"]
    entity_uri: str = Field(min_length=16, max_length=128)
    entity_name: str = Field(min_length=1, max_length=300)
    artist_names: str = Field(default="", max_length=500)
    image_url: str | None = Field(default=None, max_length=2048)


CHART_METHODS = {
    "daily_top_songs": "daily_top_songs",
    "daily_top_artists": "daily_top_artists",
    "daily_viral_songs": "daily_viral_songs",
    "daily_top_albums": "daily_top_albums",
    "weekly_top_songs": "weekly_top_songs",
    "weekly_top_artists": "weekly_top_artists",
    "weekly_top_albums": "weekly_top_albums",
}


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    if request.url.scheme == "https":
        response.headers["Strict-Transport-Security"] = "max-age=63072000"
    return response


def callback_url(request: Request) -> str:
    return settings.redirect_url or str(request.url_for("oauth_callback"))


def missing_configuration() -> list[str]:
    missing = []
    if not settings.client_id:
        missing.append("SPOTIFY_CLIENT_ID")
    if not settings.client_secret:
        missing.append("SPOTIFY_CLIENT_SECRET")
    return missing


def require_spotify_connection() -> None:
    if not refresh_token:
        raise HTTPException(status_code=401, detail="Connect Spotify before loading charts and history.")


async def get_spotify_scraper() -> SpotifyCharts:
    global refresh_token, access_token, access_token_expires_at

    async with token_refresh_lock:
        if access_token and time.monotonic() < access_token_expires_at - 60:
            return SpotifyCharts(access_token=access_token)
        try:
            pool = await get_history_pool()
            async with pool.acquire() as connection:
                async with connection.transaction():
                    row = await connection.fetchrow(
                        "SELECT refresh_token FROM public.spotify_oauth_tokens WHERE singleton = true FOR UPDATE"
                    )
                    if not row:
                        refresh_token = None
                        raise HTTPException(status_code=401, detail="Connect Spotify before loading charts.")
                    current_refresh_token = row["refresh_token"]
                    async with httpx.AsyncClient(timeout=15) as client:
                        response = await client.post(
                            "https://accounts.spotify.com/api/token",
                            data={"grant_type": "refresh_token", "refresh_token": current_refresh_token},
                            auth=(settings.client_id or "", settings.client_secret or ""),
                        )
                        response.raise_for_status()
                        token_data = response.json()
                    next_refresh_token = token_data.get("refresh_token", current_refresh_token)
                    if not isinstance(next_refresh_token, str) or not next_refresh_token:
                        raise ValueError("Spotify returned an invalid refresh token.")
                    if next_refresh_token != current_refresh_token:
                        await connection.execute(
                            """
                            UPDATE public.spotify_oauth_tokens
                            SET refresh_token = $1, updated_at = now()
                            WHERE singleton = true
                            """,
                            next_refresh_token,
                        )
                    access_token = token_data["access_token"]
                    refresh_token = next_refresh_token
                    access_token_expires_at = time.monotonic() + max(60, int(token_data.get("expires_in", 3600)))
        except HTTPException:
            raise
        except (KeyError, TypeError, ValueError) as error:
            raise HTTPException(status_code=502, detail="Spotify returned an unexpected token response.") from error
        except httpx.HTTPError as error:
            raise HTTPException(status_code=502, detail="Spotify could not refresh authorization. Reconnect and try again.") from error
        except Exception as error:
            raise HTTPException(status_code=503, detail="Spotify authorization storage is temporarily unavailable.") from error

    return SpotifyCharts(access_token=access_token)


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    page = Path(__file__).with_name("index.html").read_text(encoding="utf-8")
    return HTMLResponse(page)


@app.get("/selected-pages.js", include_in_schema=False)
async def selected_pages_script() -> FileResponse:
    return FileResponse(Path(__file__).with_name("selected_pages.js"), media_type="application/javascript")


@app.get("/api/status")
async def status(request: Request) -> dict[str, Any]:
    missing = missing_configuration()
    return {
        "configured": not missing,
        "missing": missing,
        "connected": refresh_token is not None,
        "callbackUrl": callback_url(request),
    }


@app.get("/api/countries")
async def countries() -> list[dict[str, str]]:
    return [
        {"code": country.value, "name": country.name.replace("_", " ").title()}
        for country in Countries
    ]


async def read_selected_entities(entity_type: str | None = None) -> list[dict[str, Any]]:
    try:
        pool = await get_history_pool()
        if entity_type:
            rows = await pool.fetch(
                """
                SELECT entity_uri, entity_type, entity_name, artist_names, image_url
                FROM public.spotify_chart_selections
                WHERE entity_type = $1
                ORDER BY entity_name ASC
                """,
                entity_type,
            )
        else:
            rows = await pool.fetch(
                """
                SELECT entity_uri, entity_type, entity_name, artist_names, image_url
                FROM public.spotify_chart_selections
                ORDER BY entity_type ASC, entity_name ASC
                """
            )
        return [dict(row) for row in rows]
    except Exception as error:
        raise HTTPException(status_code=503, detail="Selected entities are temporarily unavailable.") from error


def is_valid_entity_uri(uri: str, entity_type: str | None = None) -> bool:
    parts = uri.split(":")
    return (
        len(parts) == 3
        and parts[0] == "spotify"
        and parts[1] in {"track", "artist", "album"}
        and (entity_type is None or parts[1] == entity_type)
        and bool(parts[2])
        and all(character.isascii() and character.isalnum() for character in parts[2])
    )


def selection_response(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "entityUri": row["entity_uri"],
        "entityType": row["entity_type"],
        "entityName": row["entity_name"],
        "artistNames": row["artist_names"],
        "imageUrl": row["image_url"],
    }


@app.get("/api/selections")
async def list_selections() -> list[dict[str, Any]]:
    require_spotify_connection()
    return [selection_response(row) for row in await read_selected_entities()]


@app.post("/api/selections")
async def save_selection(selection: SelectionInput) -> dict[str, Any]:
    require_spotify_connection()
    if not is_valid_entity_uri(selection.entity_uri, selection.entity_type):
        raise HTTPException(status_code=400, detail="Choose a valid Spotify song or artist.")
    if selection.image_url:
        try:
            image = urlsplit(selection.image_url)
        except ValueError as error:
            raise HTTPException(status_code=400, detail="Spotify artwork URL is invalid.") from error
        if image.scheme != "https" or image.hostname != "i.scdn.co":
            raise HTTPException(status_code=400, detail="Spotify artwork must use a trusted Spotify image URL.")

    try:
        pool = await get_history_pool()
        row = await pool.fetchrow(
            """
            INSERT INTO public.spotify_chart_selections
                (entity_uri, entity_type, entity_name, artist_names, image_url)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (entity_uri) DO UPDATE SET
                entity_type = EXCLUDED.entity_type,
                entity_name = EXCLUDED.entity_name,
                artist_names = EXCLUDED.artist_names,
                image_url = EXCLUDED.image_url
            RETURNING entity_uri, entity_type, entity_name, artist_names, image_url
            """,
            selection.entity_uri,
            selection.entity_type,
            selection.entity_name.strip(),
            selection.artist_names.strip(),
            selection.image_url,
        )
        return selection_response(dict(row))
    except Exception as error:
        raise HTTPException(status_code=503, detail="The selection could not be saved right now.") from error


@app.delete("/api/selections/{entity_uri:path}")
async def remove_selection(entity_uri: str) -> dict[str, bool]:
    require_spotify_connection()
    if not is_valid_entity_uri(entity_uri):
        raise HTTPException(status_code=400, detail="Choose a valid Spotify song or artist.")
    try:
        pool = await get_history_pool()
        result = await pool.execute(
            "DELETE FROM public.spotify_chart_selections WHERE entity_uri = $1",
            entity_uri,
        )
        return {"deleted": result.endswith("1")}
    except Exception as error:
        raise HTTPException(status_code=503, detail="The selection could not be removed right now.") from error


@app.get("/api/search")
async def search_entities(
    query: str = Query(..., min_length=2, max_length=80),
    entity_type: Literal["all", "track", "artist"] = Query("all"),
    limit: int = Query(10, ge=1, le=10),
) -> dict[str, Any]:
    scraper = await get_spotify_scraper()
    try:
        items = await scraper.search_entities(query.strip(), entity_type, limit)
        return {"items": items}
    except httpx.HTTPStatusError as error:
        if error.response.status_code == 429:
            raise HTTPException(status_code=429, detail="Spotify search is rate-limited. Try again shortly.") from error
        raise HTTPException(status_code=502, detail="Spotify could not complete this search. Try again shortly.") from error
    except (httpx.HTTPError, ValueError) as error:
        raise HTTPException(status_code=502, detail="Spotify could not complete this search. Try again shortly.") from error


@app.get("/api/selected/live")
async def selected_live(
    country: str = Query("GLOBAL", min_length=2, max_length=6),
    cadence: Literal["daily", "weekly"] = Query("daily"),
) -> dict[str, Any]:
    require_spotify_connection()
    country = country.upper()
    if country not in {item.value for item in Countries}:
        raise HTTPException(status_code=400, detail="Choose a country from the list.")

    scraper = await get_spotify_scraper()
    response: dict[str, Any] = {"country": country, "cadence": cadence, "songs": [], "artists": [], "history": {}}
    for entity_type in ("track", "artist"):
        chart_type = f"{cadence}_top_{'songs' if entity_type == 'track' else 'artists'}"
        try:
            payload = await getattr(scraper, CHART_METHODS[chart_type])(country, "latest")
        except httpx.HTTPStatusError as error:
            if error.response.status_code == 429:
                raise HTTPException(status_code=429, detail="Spotify rate-limited this chart. Try again shortly.") from error
            if error.response.status_code in {401, 403}:
                raise HTTPException(status_code=502, detail="Spotify rejected chart access. Reconnect Spotify and try again.") from error
            raise HTTPException(
                status_code=502,
                detail=f"Spotify could not return this chart (HTTP {error.response.status_code}). Try again shortly.",
            ) from error
        except (httpx.HTTPError, ValueError) as error:
            raise HTTPException(status_code=502, detail="Spotify could not return this chart. Try again shortly.") from error

        entries = payload.get("entries") if isinstance(payload, dict) else None
        if not isinstance(entries, list):
            raise HTTPException(status_code=502, detail="Spotify returned an unsupported chart format.")
        try:
            response["history"][entity_type] = await prepare_chart_history(
                chart_type, country, "latest", payload, None
            )
        except ChartHistoryStorageError:
            response["history"][entity_type] = {"status": "unavailable", "reason": "History storage is unavailable."}

        output_key = "songs" if entity_type == "track" else "artists"
        chart_date = (payload.get("displayChart") or {}).get("date", "Latest")
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            uri = entry_entity_uri(entry)
            values = snapshot_values(entry)
            if not uri or not values:
                continue
            entry_type, name, artists, streams, rank = values
            stats = entry.get("chartEntryData") or {}
            metric = stats.get("rankingMetric") or {}
            response[output_key].append({
                "entityUri": uri,
                "entityType": entry_type,
                "entityName": name,
                "artistNames": artists,
                "imageUrl": None,
                "inChart": True,
                "chartRank": stats.get("currentRank") or rank,
                "previousRank": stats.get("previousRank"),
                "peakRank": stats.get("peakRank"),
                "metricType": metric.get("type"),
                "metricValue": metric.get("value") if metric.get("value") is not None else streams,
                "chartDate": chart_date,
            })
    return response


@app.get("/api/selected/history")
async def selected_history(
    chart_type: Literal["daily_top_songs", "weekly_top_songs", "daily_top_artists", "weekly_top_artists"],
    country: str = Query("GLOBAL", min_length=2, max_length=6),
    entity_uri: str | None = Query(None, min_length=16, max_length=128),
    range: str = Query("90", min_length=1, max_length=10),
) -> dict[str, Any]:
    require_spotify_connection()
    country = country.upper()
    if country not in {item.value for item in Countries}:
        raise HTTPException(status_code=400, detail="Choose a country from the list.")
    if range == "all":
        start_date = None
    else:
        try:
            days = int(range)
        except ValueError as error:
            raise HTTPException(status_code=400, detail="Choose a valid history range.") from error
        if days < 1 or days > 3650:
            raise HTTPException(status_code=400, detail="Choose a valid history range.")
        start_date = calendar_date.today() - timedelta(days=days)

    entity_type = HISTORY_CHART_TYPES[chart_type]
    if entity_uri and not is_valid_entity_uri(entity_uri, entity_type):
        raise HTTPException(status_code=400, detail="Choose a valid song or artist from this chart.")

    try:
        pool = await get_history_pool()
        if entity_uri:
            rows = await pool.fetch(
                """
                SELECT track_uri, entity_type, track_name, artist_names, chart_date, chart_rank, stream_count
                FROM public.spotify_chart_snapshots
                WHERE chart_type = $1 AND country_code = $2 AND entity_type = $3
                  AND track_uri = $4 AND ($5::date IS NULL OR chart_date >= $5::date)
                ORDER BY chart_date ASC
                """,
                chart_type,
                country,
                entity_type,
                entity_uri,
                start_date,
            )
        else:
            rows = await pool.fetch(
                """
                SELECT track_uri, entity_type, track_name, artist_names, chart_date, chart_rank, stream_count
                FROM (
                    SELECT DISTINCT ON (track_uri)
                        track_uri, entity_type, track_name, artist_names, chart_date, chart_rank, stream_count
                    FROM public.spotify_chart_snapshots
                    WHERE chart_type = $1 AND country_code = $2 AND entity_type = $3
                      AND ($4::date IS NULL OR chart_date >= $4::date)
                    ORDER BY track_uri, chart_date DESC
                ) latest
                ORDER BY chart_rank ASC NULLS LAST, track_name ASC
                """,
                chart_type,
                country,
                entity_type,
                start_date,
            )
    except Exception as error:
        raise HTTPException(status_code=503, detail="Chart history is temporarily unavailable.") from error

    points = [
        {
            "entityUri": row["track_uri"],
            "entityType": row["entity_type"],
            "entityName": row["track_name"],
            "artistNames": row["artist_names"],
            "chartDate": row["chart_date"].isoformat(),
            "chartRank": row["chart_rank"],
            "streamCount": row["stream_count"],
        }
        for row in rows
    ]
    entity = None
    if entity_uri and rows:
        row = rows[0]
        entity = {
            "entityUri": row["track_uri"],
            "entityType": row["entity_type"],
            "entityName": row["track_name"],
            "artistNames": row["artist_names"],
            "imageUrl": None,
        }
    return {"chartType": chart_type, "country": country, "entity": entity, "points": points}


def focused_artist_matches(entry: dict[str, Any], entity_type: str) -> bool:
    metadata = entry.get({"track": "trackMetadata", "artist": "artistMetadata", "album": "albumMetadata"}[entity_type]) or {}
    if entity_type == "artist":
        artist_uri = metadata.get("artistUri") or entry_entity_uri(entry)
        artist_name = str(metadata.get("artistName") or "").casefold()
        return artist_uri in FOCUSED_ARTIST_URIS or artist_name in {name.casefold() for name in FOCUSED_ARTIST_NAMES}

    if entity_type == "track" and entry_entity_uri(entry) == FOCUSED_TRACK_URI:
        return True

    artists = metadata.get("artists") or []
    return any(
        isinstance(artist, dict)
        and (
            artist.get("spotifyUri") in FOCUSED_ARTIST_URIS
            or artist.get("uri") in FOCUSED_ARTIST_URIS
            or str(artist.get("name", "")).casefold() in {name.casefold() for name in FOCUSED_ARTIST_NAMES}
        )
        for artist in artists
    )


def focused_chart_item(entry: dict[str, Any], country_code: str, chart_date: str) -> dict[str, Any] | None:
    values = snapshot_values(entry)
    if not values:
        return None

    entity_type, name, artists, streams, rank = values
    metadata = entry.get({"track": "trackMetadata", "artist": "artistMetadata", "album": "albumMetadata"}[entity_type]) or {}
    stats = entry.get("chartEntryData") or {}
    metric = stats.get("rankingMetric") or {}
    return {
        "entityUri": entry_entity_uri(entry),
        "entityType": entity_type,
        "entityName": name,
        "artistNames": artists,
        "imageUrl": metadata.get("displayImageUri"),
        "regionCode": country_code,
        "regionName": COUNTRY_NAMES.get(country_code, country_code),
        "chartRank": stats.get("currentRank") or rank,
        "previousRank": stats.get("previousRank"),
        "peakRank": stats.get("peakRank"),
        "consecutiveAppearancesOnChart": stats.get("consecutiveAppearancesOnChart"),
        "appearancesOnChart": stats.get("appearancesOnChart"),
        "entryDate": stats.get("entryDate"),
        "metricType": metric.get("type"),
        "metricValue": metric.get("value") if metric.get("value") is not None else streams,
        "chartDate": chart_date,
    }


focused_chart_scan_tasks: dict[tuple[str, str, str], asyncio.Task] = {}


def pending_focused_scan(cadence: str, entity_type: str) -> dict[str, Any]:
    chart_type = f"{cadence}_top_{ {'track': 'songs', 'artist': 'artists', 'album': 'albums'}[entity_type] }"
    return {
        "artist": {"entityName": FOCUSED_ARTIST_NAME},
        "cadence": cadence,
        "entityType": entity_type,
        "chartType": chart_type,
        "marketScope": "all",
        "marketsTotal": len(Countries),
        "marketsScanned": 0,
        "matchingRegions": 0,
        "failedRegions": [],
        "historySavedRegions": 0,
        "historyStorageAvailable": None,
        "releases": [],
        "regions": [],
        "scanInProgress": True,
    }


async def load_focused_snapshot(cadence: str, entity_type: str) -> dict[str, Any] | None:
    pool = await get_history_pool()
    row = await pool.fetchrow(
        """
        SELECT payload, fetched_at
        FROM public.spotify_focused_chart_snapshots
        WHERE cadence = $1 AND entity_type = $2 AND market_scope = 'all'
          AND snapshot_date = CURRENT_DATE
        """,
        cadence,
        entity_type,
    )
    if row is None:
        return None

    payload = row["payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)
    if not isinstance(payload, dict) or payload.get("snapshotVersion") != 2:
        return None

    cached_payload = dict(payload)
    cached_payload["snapshotReused"] = True
    cached_payload["snapshotFetchedAt"] = row["fetched_at"].isoformat()
    return cached_payload


async def save_focused_snapshot(cadence: str, entity_type: str, payload: dict[str, Any]) -> None:
    pool = await get_history_pool()
    await pool.execute(
        """
        INSERT INTO public.spotify_focused_chart_snapshots
            (cadence, entity_type, market_scope, snapshot_date, payload, fetched_at)
        VALUES ($1, $2, 'all', CURRENT_DATE, $3::jsonb, now())
        ON CONFLICT (cadence, entity_type, market_scope, snapshot_date)
        DO UPDATE SET payload = EXCLUDED.payload, fetched_at = EXCLUDED.fetched_at
        """,
        cadence,
        entity_type,
        json.dumps(payload, separators=(",", ":")),
    )


async def run_focused_scan_and_cache(cache_key: tuple[str, str, str], cadence: str, entity_type: str, scope: str) -> None:
    try:
        response = await scan_focused_live(cadence, entity_type, scope)
        if scope == "all":
            pool = await get_history_pool()
            if response["marketsScanned"] > 0 and not response["failedRegions"]:
                try:
                    response["snapshotVersion"] = 2
                    await save_focused_snapshot(cadence, entity_type, response)
                    await pool.execute("UPDATE public.spotify_focused_scan_jobs SET status='complete', lease_owner=NULL, lease_expires_at=NULL, last_error=NULL, updated_at=now() WHERE cadence=$1 AND entity_type=$2", cadence, entity_type)
                except Exception as error:
                    print(f"[v0] Focused chart snapshot could not be saved: {type(error).__name__}")
                    response["historyStorageAvailable"] = False
            else:
                await pool.execute("UPDATE public.spotify_focused_scan_jobs SET status='failed', lease_owner=NULL, lease_expires_at=NULL, last_error='one_or_more_markets_failed', updated_at=now() WHERE cadence=$1 AND entity_type=$2", cadence, entity_type)
    except Exception as error:
        response = pending_focused_scan(cadence, entity_type)
        response.update({
            "scanInProgress": False,
            "scanError": "Spotify could not load the regional charts. Try again shortly.",
            "failedRegions": [country.name.replace("_", " ").title() for country in Countries],
        })
        if scope == "all":
            try:
                pool = await get_history_pool()
                await pool.execute("UPDATE public.spotify_focused_scan_jobs SET status='failed', lease_owner=NULL, lease_expires_at=NULL, last_error=$3, updated_at=now() WHERE cadence=$1 AND entity_type=$2", cadence, entity_type, type(error).__name__[:80])
            except Exception:
                pass
    async with focused_chart_cache_lock:
        focused_chart_cache[cache_key] = (time.monotonic(), response)
        focused_chart_scan_tasks.pop(cache_key, None)


@app.get("/api/focused/live")
async def focused_live(
    cadence: Literal["daily", "weekly"] = Query("daily"),
    entity_type: Literal["track", "artist", "album"] = Query("track"),
    scope: Literal["global", "all"] = Query("global"),
    refresh: bool = Query(False),
) -> dict[str, Any]:
    chart_type = f"{cadence}_top_{ {'track': 'songs', 'artist': 'artists', 'album': 'albums'}[entity_type] }"
    cache_key = (cadence, entity_type, scope)
    async with focused_chart_cache_lock:
        cached = focused_chart_cache.get(cache_key)
        cache_ttl = 15 if cached and cached[1].get("scanError") else FOCUSED_CHART_CACHE_TTL
        if not refresh and cached and time.monotonic() - cached[0] < cache_ttl:
            return cached[1]
        if scope == "all" and refresh:
            focused_chart_cache.pop(cache_key, None)

    if scope == "all" and not refresh:
        async with focused_chart_cache_lock:
            active_scan = focused_chart_scan_tasks.get(cache_key)
            if active_scan is not None and not active_scan.done():
                return pending_focused_scan(cadence, entity_type)
        try:
            persisted = await load_focused_snapshot(cadence, entity_type)
        except Exception as error:
            print(f"[v0] Focused chart snapshot could not be loaded: {type(error).__name__}")
            persisted = None
        if persisted is not None:
            async with focused_chart_cache_lock:
                focused_chart_cache[cache_key] = (time.monotonic(), persisted)
            return persisted

    if scope == "all":
        pool = await get_history_pool()
        await pool.execute(
            """INSERT INTO public.spotify_focused_scan_jobs (cadence, entity_type, status)
               VALUES ($1, $2, 'pending') ON CONFLICT (cadence, entity_type) DO UPDATE
               SET status=CASE WHEN public.spotify_focused_scan_jobs.status IN ('complete','failed')
                               THEN 'pending' ELSE public.spotify_focused_scan_jobs.status END,
                   updated_at=now()""",
            cadence, entity_type,
        )
        async with focused_chart_cache_lock:
            task = focused_chart_scan_tasks.get(cache_key)
            if task is None or task.done():
                task = asyncio.create_task(run_focused_scan_and_cache(cache_key, cadence, entity_type, scope))
                focused_chart_scan_tasks[cache_key] = task
        return pending_focused_scan(cadence, entity_type)

    response = await scan_focused_live(cadence, entity_type, scope)
    async with focused_chart_cache_lock:
        focused_chart_cache[cache_key] = (time.monotonic(), response)
    return response


async def update_focused_scan_checkpoint(cadence: str, entity_type: str, country_code: str, payload: dict[str, Any] | None) -> None:
    pool = await get_history_pool()
    if payload is None:
        await pool.execute(
            """INSERT INTO public.spotify_focused_scan_jobs (cadence, entity_type, failed_markets)
               VALUES ($1, $2, jsonb_build_array(jsonb_build_object('country', $3, 'error', 'fetch_failed')))
               ON CONFLICT (cadence, entity_type) DO UPDATE SET
                 failed_markets = (SELECT COALESCE(jsonb_agg(item), '[]'::jsonb) FROM
                   (SELECT value AS item FROM jsonb_array_elements(public.spotify_focused_scan_jobs.failed_markets)
                    WHERE value->>'country' <> $3 UNION ALL SELECT jsonb_build_object('country',$3,'error','fetch_failed')) failures),
                 updated_at=now()""",
            cadence, entity_type, country_code,
        )
        return
    await pool.execute(
        """INSERT INTO public.spotify_focused_scan_jobs
             (cadence, entity_type, completed_markets, failed_markets, partial_payload, status)
           VALUES ($1, $2, ARRAY[$3]::text[], '[]'::jsonb, jsonb_build_object($3, $4::jsonb), 'running')
           ON CONFLICT (cadence, entity_type) DO UPDATE SET
             completed_markets = array_append(array_remove(public.spotify_focused_scan_jobs.completed_markets, $3), $3),
             failed_markets = (SELECT COALESCE(jsonb_agg(value), '[]'::jsonb) FROM
               jsonb_array_elements(public.spotify_focused_scan_jobs.failed_markets) WHERE value->>'country' <> $3),
             partial_payload = jsonb_set(public.spotify_focused_scan_jobs.partial_payload, ARRAY[$3], $4::jsonb, true),
             status='running', updated_at=now()""",
        cadence, entity_type, country_code, json.dumps(payload, separators=(",", ":")),
    )


async def scan_focused_live(cadence: str, entity_type: str, scope: str) -> dict[str, Any]:
    chart_type = f"{cadence}_top_{ {'track': 'songs', 'artist': 'artists', 'album': 'albums'}[entity_type] }"
    scraper = await get_spotify_scraper()
    method = getattr(scraper, CHART_METHODS[chart_type])
    markets = [Countries.GLOBAL] if scope == "global" else list(Countries)
    checkpoint = None
    if scope == "all":
        pool = await get_history_pool()
        checkpoint = await pool.fetchrow(
            "SELECT completed_markets, failed_markets, partial_payload FROM public.spotify_focused_scan_jobs WHERE cadence=$1 AND entity_type=$2",
            cadence, entity_type,
        )
    completed = set(checkpoint["completed_markets"] or []) if checkpoint else set()
    partial = checkpoint["partial_payload"] or {} if checkpoint else {}
    if isinstance(partial, str):
        partial = json.loads(partial)
    semaphore = asyncio.Semaphore(2)
    history_saved_regions = 0
    history_storage_available = True

    async def fetch_market(country) -> tuple[Any, dict[str, Any] | None, bool]:
        nonlocal history_storage_available
        if country.value in completed and isinstance(partial.get(country.value), dict):
            saved = False
            try:
                history = await prepare_chart_history(chart_type, country.value, "latest", partial[country.value], None)
                saved = bool(history.get("supported"))
            except (ChartHistoryStorageError, HTTPException):
                history_storage_available = False
            return country, partial[country.value], saved
        async with semaphore:
            try:
                payload = await method(country.value, "latest")
            except (httpx.HTTPError, ValueError):
                if scope == "all":
                    await update_focused_scan_checkpoint(cadence, entity_type, country.value, None)
                return country, None, False

            entries = payload.get("entries") if isinstance(payload, dict) else None
            if not isinstance(entries, list):
                if scope == "all":
                    await update_focused_scan_checkpoint(cadence, entity_type, country.value, None)
                return country, None, False
            if scope == "all":
                await update_focused_scan_checkpoint(cadence, entity_type, country.value, payload)
            saved = False
            try:
                history = await prepare_chart_history(chart_type, country.value, "latest", payload, None)
                saved = bool(history.get("supported"))
            except (ChartHistoryStorageError, HTTPException):
                history_storage_available = False
            return country, payload, saved

    results = await asyncio.gather(*(fetch_market(country) for country in markets))
    releases: dict[str, dict[str, Any]] = {}
    regions: list[dict[str, Any]] = []
    failed_regions: list[str] = []

    for country, payload, saved in results:
        if payload is None:
            failed_regions.append(COUNTRY_NAMES.get(country.value, country.value))
            continue
        if saved:
            history_saved_regions += 1

        display_chart = payload.get("displayChart") or {}
        chart_date = str(display_chart.get("date") or "Latest")
        region_items = []
        for entry in payload.get("entries") or []:
            if not isinstance(entry, dict) or not focused_artist_matches(entry, entity_type):
                continue
            item = focused_chart_item(entry, country.value, chart_date)
            if not item or not item["entityUri"]:
                continue
            region_items.append(item)
            release = releases.setdefault(item["entityUri"], {
                "entityUri": item["entityUri"],
                "entityType": item["entityType"],
                "entityName": item["entityName"],
                "artistNames": item["artistNames"],
                "imageUrl": item["imageUrl"],
                "regions": [],
            })
            release["regions"].append({key: value for key, value in item.items() if key not in {"entityUri", "entityType", "entityName", "artistNames", "imageUrl"}})

        if region_items:
            region_items.sort(key=lambda item: item["chartRank"] or 10_000)
            regions.append({
                "regionCode": country.value,
                "regionName": COUNTRY_NAMES.get(country.value, country.value),
                "chartDate": chart_date,
                "entries": region_items,
            })

    if not regions and failed_regions:
        message = "Spotify could not load the Global chart. Try again shortly." if scope == "global" else "Spotify could not load any regional charts. Try again shortly."
        raise HTTPException(status_code=502, detail=message)

    regions.sort(key=lambda region: (region["regionCode"] != "GLOBAL", region["regionName"]))
    release_items = list(releases.values())
    release_items.sort(key=lambda release: (-len(release["regions"]), release["entityName"].casefold()))
    return {
        "artist": {"entityName": FOCUSED_ARTIST_NAME},
        "cadence": cadence,
        "entityType": entity_type,
        "chartType": chart_type,
        "marketScope": scope,
        "marketsTotal": len(markets),
        "marketsScanned": len(markets) - len(failed_regions),
        "matchingRegions": len(regions),
        "failedRegions": failed_regions,
        "historySavedRegions": history_saved_regions,
        "historyStorageAvailable": history_storage_available,
        "releases": release_items,
        "regions": regions,
        "scanInProgress": False,
    }


def history_start_date(range_value: str) -> calendar_date | None:
    if range_value == "all":
        return None
    try:
        days = int(range_value)
    except ValueError as error:
        raise HTTPException(status_code=400, detail="Choose a valid history range.") from error
    if days < 1 or days > 3650:
        raise HTTPException(status_code=400, detail="Choose a valid history range.")
    return calendar_date.today() - timedelta(days=days)


def focused_history_filter_sql() -> str:
    return """(($2 = 'artist' AND track_uri = ANY($5::text[])) OR ($2 IN ('track', 'album') AND (track_uri = $6 OR string_to_array(COALESCE(artist_names, ''), ', ') && $7::text[])))"""


def history_point(row) -> dict[str, Any]:
    country_code = row["country_code"]
    return {
        "entityUri": row["track_uri"],
        "entityType": row["entity_type"],
        "entityName": row["track_name"],
        "artistNames": row["artist_names"],
        "regionCode": country_code,
        "regionName": COUNTRY_NAMES.get(country_code, country_code),
        "chartDate": row["chart_date"].isoformat(),
        "chartRank": row["chart_rank"],
        "previousRank": row["previous_rank"],
        "peakRank": row["peak_rank"],
        "consecutiveAppearancesOnChart": row["consecutive_appearances"],
        "appearancesOnChart": row["appearances"],
        "entryDate": row["entry_date"].isoformat() if row["entry_date"] else None,
        "streamCount": row["stream_count"],
    }


def group_history_points(points: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    releases: dict[str, dict[str, Any]] = {}
    regions: dict[str, dict[str, Any]] = {}
    for point in points:
        release = releases.setdefault(point["entityUri"], {
            "entityUri": point["entityUri"],
            "entityType": point["entityType"],
            "entityName": point["entityName"],
            "artistNames": point["artistNames"],
            "imageUrl": None,
            "regions": [],
        })
        release["regions"].append(point)
        region = regions.setdefault(point["regionCode"], {
            "regionCode": point["regionCode"],
            "regionName": point["regionName"],
            "entries": [],
        })
        region["entries"].append(point)

    release_items = list(releases.values())
    for release in release_items:
        release["regions"].sort(key=lambda point: (point["regionCode"] != "GLOBAL", point["regionName"]))
    release_items.sort(key=lambda release: (-len(release["regions"]), release["entityName"].casefold()))
    region_items = list(regions.values())
    for region in region_items:
        region["entries"].sort(key=lambda point: point["chartRank"] if point["chartRank"] is not None else 10_000)
    region_items.sort(key=lambda region: (region["regionCode"] != "GLOBAL", region["regionName"]))
    return release_items, region_items


@app.get("/api/focused/history")
async def focused_history(
    cadence: Literal["daily", "weekly"] = Query("daily"),
    entity_type: Literal["track", "artist", "album"] = Query("track"),
    range: str = Query("90", min_length=1, max_length=10),
) -> dict[str, Any]:
    chart_type = f"{cadence}_top_{ {'track': 'songs', 'artist': 'artists', 'album': 'albums'}[entity_type] }"
    start_date = history_start_date(range)
    market_codes = [country.value for country in Countries]
    try:
        pool = await get_history_pool()
        rows = await pool.fetch(
            f"""
            SELECT DISTINCT ON (country_code, track_uri)
                track_uri, entity_type, track_name, artist_names, country_code, chart_date,
                chart_rank, previous_rank, peak_rank, consecutive_appearances, appearances,
                entry_date, stream_count
            FROM public.spotify_chart_snapshots
            WHERE chart_type = $1 AND entity_type = $2 AND country_code = ANY($3::text[])
              AND ($4::date IS NULL OR chart_date >= $4::date)
              AND {focused_history_filter_sql()}
            ORDER BY country_code, track_uri, chart_date DESC, fetched_at DESC
            """,
            chart_type,
            entity_type,
            market_codes,
            start_date,
            FOCUSED_ARTIST_URIS,
            FOCUSED_TRACK_URI,
            FOCUSED_ARTIST_NAMES,
        )
    except Exception as error:
        raise HTTPException(status_code=503, detail="Focused chart history is temporarily unavailable.") from error

    points = [history_point(row) for row in rows]
    releases, regions = group_history_points(points)
    return {
        "artist": {"entityName": FOCUSED_ARTIST_NAME},
        "cadence": cadence,
        "entityType": entity_type,
        "chartType": chart_type,
        "range": range,
        "snapshotRows": len(points),
        "releases": releases,
        "regions": regions,
    }


@app.get("/api/focused/history/trend")
async def focused_history_trend(
    entity_uri: str = Query(..., min_length=16, max_length=128),
    cadence: Literal["daily", "weekly"] = Query("daily"),
    entity_type: Literal["track", "artist", "album"] = Query("track"),
    range: str = Query("90", min_length=1, max_length=10),
) -> dict[str, Any]:
    if not is_valid_entity_uri(entity_uri, entity_type):
        raise HTTPException(status_code=400, detail="Choose a valid chart release.")
    if entity_type == "artist" and entity_uri not in FOCUSED_ARTIST_URIS:
        raise HTTPException(status_code=404, detail="That artist is outside the focused chart selection.")

    chart_type = f"{cadence}_top_{ {'track': 'songs', 'artist': 'artists', 'album': 'albums'}[entity_type] }"
    start_date = history_start_date(range)
    market_codes = [country.value for country in Countries]
    try:
        pool = await get_history_pool()
        rows = await pool.fetch(
            """
            SELECT track_uri, entity_type, track_name, artist_names, country_code, chart_date, chart_rank, stream_count
            FROM public.spotify_chart_snapshots
            WHERE chart_type = $1 AND entity_type = $2 AND track_uri = $3
              AND country_code = ANY($4::text[]) AND ($5::date IS NULL OR chart_date >= $5::date)
              AND (($2 = 'artist' AND $3 = ANY($6::text[])) OR ($2 IN ('track', 'album') AND ($3 = $7 OR string_to_array(COALESCE(artist_names, ''), ', ') && $8::text[])))
            ORDER BY country_code ASC, chart_date ASC
            """,
            chart_type,
            entity_type,
            entity_uri,
            market_codes,
            start_date,
            FOCUSED_ARTIST_URIS,
            FOCUSED_TRACK_URI,
            FOCUSED_ARTIST_NAMES,
        )
    except Exception as error:
        raise HTTPException(status_code=503, detail="Focused chart history is temporarily unavailable.") from error

    points = [history_point(row) for row in rows]
    entity = None if not points else {key: points[0][key] for key in ("entityUri", "entityType", "entityName", "artistNames")}
    return {
        "artist": {"entityName": FOCUSED_ARTIST_NAME},
        "entity": entity,
        "cadence": cadence,
        "entityType": entity_type,
        "chartType": chart_type,
        "range": range,
        "points": points,
    }


@app.get("/auth/login")
async def login(request: Request):
    if missing_configuration():
        raise HTTPException(status_code=503, detail="Spotify app credentials are not configured.")

    state = secrets.token_urlsafe(32)
    query = urlencode(
        {
            "response_type": "code",
            "client_id": settings.client_id,
            "redirect_uri": callback_url(request),
            "state": state,
        }
    )
    response = RedirectResponse(f"https://accounts.spotify.com/authorize?{query}", status_code=302)
    response.set_cookie(
        "spotify_oauth_state",
        state,
        max_age=600,
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="lax",
        path="/oauth/callback",
    )
    return response


@app.get("/oauth/callback", name="oauth_callback")
async def oauth_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    oauth_state: str | None = Cookie(default=None, alias="spotify_oauth_state"),
):
    global refresh_token, access_token, access_token_expires_at

    if error or not code or not state or not oauth_state or not secrets.compare_digest(state, oauth_state):
        response = RedirectResponse("/?auth=error", status_code=303)
        response.delete_cookie("spotify_oauth_state", path="/oauth/callback")
        return response

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                "https://accounts.spotify.com/api/token",
                data={
                    "code": code,
                    "redirect_uri": callback_url(request),
                    "grant_type": "authorization_code",
                },
                auth=(settings.client_id or "", settings.client_secret or ""),
            )
            response.raise_for_status()
            token_data = response.json()
        new_refresh_token = token_data.get("refresh_token")
        if not isinstance(new_refresh_token, str) or not new_refresh_token:
            response = RedirectResponse("/?auth=error", status_code=303)
            response.delete_cookie("spotify_oauth_state", path="/oauth/callback")
            return response
        await store_spotify_refresh_token(new_refresh_token)
        refresh_token = new_refresh_token
        access_token = None
        access_token_expires_at = 0.0
    except Exception:
        response = RedirectResponse("/?auth=error", status_code=303)
        response.delete_cookie("spotify_oauth_state", path="/oauth/callback")
        return response

    response = RedirectResponse("/?auth=connected", status_code=303)
    response.delete_cookie("spotify_oauth_state", path="/oauth/callback")
    return response


@app.post("/api/disconnect")
async def disconnect() -> dict[str, bool]:
    global refresh_token, access_token, access_token_expires_at
    try:
        await remove_spotify_refresh_token()
    except Exception as error:
        raise HTTPException(status_code=503, detail="Spotify authorization could not be removed from storage.") from error
    refresh_token = None
    access_token = None
    access_token_expires_at = 0.0
    return {"connected": False}


async def decorate_chart(raw_payload: dict[str, Any], chart_type: str, country: str, requested_date: str) -> dict[str, Any]:
    result = json.loads(json.dumps(raw_payload))
    if chart_type not in HISTORY_CHART_TYPES:
        result["chartHistory"] = {
            "supported": False, "complete": False, "status": "unsupported",
            "reason": "History is available for daily and weekly Top Songs, Top Artists, and Top Albums charts.",
        }
        return result
    try:
        history_info = await prepare_chart_history(chart_type, country, requested_date, result, None)
        history_info["metric"] = "rank" if HISTORY_CHART_TYPES[chart_type] == "artist" else "streams"
        result["chartHistory"] = history_info
    except (ChartHistoryStorageError, HTTPException) as error:
        if isinstance(error, HTTPException) and error.status_code != 503:
            raise
        result["chartHistory"] = {
            "supported": False, "complete": False, "status": "unavailable",
            "reason": "Historical storage is unavailable; snapshots could not be saved.",
        }
    return result


@app.get("/api/charts")
async def chart(
    chart_type: str = Query(..., alias="type"),
    country: str = Query("GLOBAL", min_length=2, max_length=6),
    date: str = Query("latest", min_length=1, max_length=10),
) -> dict[str, Any]:
    if chart_type not in CHART_METHODS:
        raise HTTPException(status_code=400, detail="Choose a supported chart type.")
    normalized_country = country.upper()
    if normalized_country not in {item.value for item in Countries}:
        raise HTTPException(status_code=400, detail="Choose a country from the list.")
    if date != "latest":
        try:
            if calendar_date.fromisoformat(date).isoformat() != date:
                raise ValueError
        except ValueError as error:
            raise HTTPException(status_code=400, detail="Date must be latest or YYYY-MM-DD.") from error

    require_spotify_connection()
    cached_row = None
    try:
        cached_row = await read_chart_cache(chart_type, normalized_country, date)
    except Exception as error:
        print(f"[v0] Chart cache read unavailable: {type(error).__name__}")
    if cached_row is not None:
        raw = cached_row["payload"]
        try:
            if isinstance(raw, str):
                raw = json.loads(raw)
        except (TypeError, ValueError):
            raw = None
        if isinstance(raw, dict) and isinstance(raw.get("entries"), list):
            age_seconds = max(0, int(time.time() - cached_row["fetched_at"].timestamp()))
            stale = age_seconds >= CHART_PAYLOAD_TTL_SECONDS
            job_status = "fresh"
            if stale:
                try:
                    await enqueue_chart_refresh(chart_type, normalized_country, date)
                    job_status = "pending"
                except Exception as error:
                    print(f"[v0] Chart refresh queue unavailable: {type(error).__name__}")
                    job_status = "unavailable"
            decorated = await decorate_chart(raw, chart_type, normalized_country, date)
            decorated["delivery"] = {
                "fetchedAt": cached_row["fetched_at"].isoformat(), "ageSeconds": age_seconds,
                "isStale": stale, "refreshStatus": job_status,
            }
            return decorated

    scraper = await get_spotify_scraper()
    try:
        raw_result = await getattr(scraper, CHART_METHODS[chart_type])(normalized_country, date)
    except httpx.HTTPStatusError as error:
        if error.response.status_code == 429:
            raise HTTPException(status_code=429, detail="Spotify rate-limited this chart. Try again shortly.") from error
        if error.response.status_code in {401, 403}:
            raise HTTPException(status_code=502, detail="Spotify rejected chart access. Reconnect Spotify and try again.") from error
        raise HTTPException(status_code=502, detail=f"Spotify could not return this chart (HTTP {error.response.status_code}). Try again shortly.") from error
    except (httpx.HTTPError, ValueError) as error:
        raise HTTPException(status_code=502, detail="Spotify could not return this chart. Try again shortly.") from error
    if not isinstance(raw_result, dict) or not isinstance(raw_result.get("entries"), list):
        raise HTTPException(status_code=502, detail="Spotify returned an unsupported chart format.")

    fetched_at = None
    try:
        fetched_at = await save_chart_cache(chart_type, normalized_country, date, raw_result)
    except Exception as error:
        print(f"[v0] Chart cache write unavailable: {type(error).__name__}")
    result = await decorate_chart(raw_result, chart_type, normalized_country, date)
    result["delivery"] = {
        "fetchedAt": fetched_at.isoformat() if fetched_at else None,
        "ageSeconds": 0, "isStale": False, "refreshStatus": "fresh" if fetched_at else "uncached",
    }
    return result


@app.post("/api/charts/refresh")
async def refresh_chart(
    chart_type: str = Query(..., alias="type"),
    country: str = Query("GLOBAL", min_length=2, max_length=6),
    date: str = Query("latest", min_length=1, max_length=10),
) -> dict[str, Any]:
    if chart_type not in CHART_METHODS or country.upper() not in {item.value for item in Countries}:
        raise HTTPException(status_code=400, detail="Choose a supported chart and country.")
    normalized_country = country.upper()
    if date != "latest":
        try:
            if calendar_date.fromisoformat(date).isoformat() != date:
                raise ValueError
        except ValueError as error:
            raise HTTPException(status_code=400, detail="Date must be latest or YYYY-MM-DD.") from error
    require_spotify_connection()
    pool = await get_history_pool()
    owner = secrets.token_urlsafe(18)
    job = await pool.fetchrow(
        """UPDATE public.spotify_chart_refresh_jobs SET status='running', lease_owner=$4,
             lease_expires_at=now()+($5 * interval '1 second'), attempt_count=attempt_count+1,
             updated_at=now() WHERE chart_type=$1 AND country_code=$2 AND requested_date=$3
             AND next_attempt_at <= now() AND (status IN ('pending','failed') OR
                 (status='running' AND lease_expires_at < now())) RETURNING attempt_count""",
        chart_type, normalized_country, date, owner, CHART_REFRESH_LEASE_SECONDS,
    )
    if not job:
        current = await pool.fetchrow(
            "SELECT status, next_attempt_at FROM public.spotify_chart_refresh_jobs WHERE chart_type=$1 AND country_code=$2 AND requested_date=$3",
            chart_type, normalized_country, date,
        )
        return {"status": current["status"] if current else "pending"}
    try:
        scraper = await get_spotify_scraper()
        payload = await getattr(scraper, CHART_METHODS[chart_type])(normalized_country, date)
        if not isinstance(payload, dict) or not isinstance(payload.get("entries"), list):
            raise ValueError("Spotify returned an unsupported chart format.")
        fetched_at = await save_chart_cache(chart_type, normalized_country, date, payload)
        await pool.execute(
            """UPDATE public.spotify_chart_refresh_jobs SET status='complete', lease_owner=NULL,
                 lease_expires_at=NULL, last_error=NULL, updated_at=now()
               WHERE chart_type=$1 AND country_code=$2 AND requested_date=$3 AND lease_owner=$4""",
            chart_type, normalized_country, date, owner,
        )
        return {"status": "complete", "fetchedAt": fetched_at.isoformat()}
    except Exception as error:
        delay = min(300, 5 * (2 ** min(int(job["attempt_count"]) - 1, 6)))
        await pool.execute(
            """UPDATE public.spotify_chart_refresh_jobs SET status='failed', lease_owner=NULL,
                 lease_expires_at=NULL, next_attempt_at=now()+($5 * interval '1 second'),
                 last_error=$6, updated_at=now()
               WHERE chart_type=$1 AND country_code=$2 AND requested_date=$3 AND lease_owner=$4""",
            chart_type, normalized_country, date, owner, delay, type(error).__name__[:80],
        )
        return {"status": "failed", "retryAfterSeconds": delay, "error": "Refresh failed; the saved chart is unchanged."}


@app.post("/api/chart-history/backfill")
async def chart_history_backfill(request: ChartHistoryBackfillRequest) -> dict[str, Any]:
    chart_type = request.chart_type
    country = request.country.upper()
    require_spotify_connection()
    if chart_type not in HISTORY_CHART_TYPES:
        raise HTTPException(status_code=400, detail="Choose a supported songs or artists chart to backfill history.")
    if country not in {item.value for item in Countries}:
        raise HTTPException(status_code=400, detail="Choose a country from the list.")

    entity_uris = list(dict.fromkeys(request.entity_uris))
    if any(not is_valid_entity_uri(uri, HISTORY_CHART_TYPES[chart_type]) for uri in entity_uris):
        raise HTTPException(status_code=400, detail="The chart entity list is invalid.")

    try:
        return await run_history_backfill_batch(
            chart_type,
            country,
            request.target_date,
            request.start_date,
            entity_uris,
            get_spotify_scraper,
        )
    except ChartHistoryStorageError as error:
        raise HTTPException(status_code=503, detail="Historical storage is unavailable.") from error
