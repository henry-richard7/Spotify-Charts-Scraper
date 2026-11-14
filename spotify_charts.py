import asyncio
from copy import deepcopy
from time import monotonic
from httpx import AsyncClient, Client
from urllib.parse import urlparse, parse_qs
from os import path
from http.server import BaseHTTPRequestHandler, HTTPServer
import click
from Countries import Countries
from Settings import SpotifySettings

spotify_settings = SpotifySettings()

client_id = spotify_settings.client_id
client_secret = spotify_settings.client_secret
redirect_uri = spotify_settings.redirect_url
redirect_port = spotify_settings.redirect_port

redirect_url = f"{redirect_uri}:{redirect_port}"
CHART_CACHE_TTL = 300
CHART_REQUEST_INTERVAL = 2.0
CHART_RATE_LIMIT_MAX_COOLDOWN = 120.0
_chart_cache: dict[str, tuple[float, dict]] = {}
_chart_inflight: dict[str, asyncio.Task] = {}
_chart_inflight_lock = asyncio.Lock()
_chart_request_lock = asyncio.Lock()
_next_chart_request_at = 0.0


async def _wait_for_chart_request_slot() -> None:
    global _next_chart_request_at
    async with _chart_request_lock:
        now = monotonic()
        delay = max(0.0, _next_chart_request_at - now)
        _next_chart_request_at = max(now, _next_chart_request_at) + CHART_REQUEST_INTERVAL
    if delay:
        await asyncio.sleep(delay)


async def _defer_chart_requests(delay: float) -> None:
    global _next_chart_request_at
    async with _chart_request_lock:
        _next_chart_request_at = max(_next_chart_request_at, monotonic() + delay)


class AuthRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        html_body = """
            <html> 
            <head>
                <title> Spotify OAuth </title> 
            </head>
            <body>
            <h1>Logged In please close this page!.</h1>
            </body>
            </html>
        """.strip()
        self.send_response(200, "OK")
        self.send_header("Content-type", "html")
        self.end_headers()

        self.wfile.write(bytes(html_body, "UTF-8"))
        auth_code_path = self.path

        parsed_url = urlparse(auth_code_path)
        code = parse_qs(parsed_url.query)["code"]

        token_url = "https://accounts.spotify.com/api/token"
        body = {
            "code": code,
            "redirect_uri": redirect_url,
            "grant_type": "authorization_code",
        }

        response = (
            Client().post(token_url, data=body, auth=(client_id, client_secret)).json()
        )

        refresh_token = response["refresh_token"]

        open(".refresh_token", "w").write(refresh_token)
        print("[*] Stored Refresh Token.")


def get_refresh_token(server_class=HTTPServer, handler_class=AuthRequestHandler):
    server_address = ("", redirect_port)
    httpd = server_class(server_address, handler_class)
    url = f"https://accounts.spotify.com/authorize?response_type=code&client_id={client_id}&redirect_uri={redirect_url}"
    click.launch(url)
    httpd.handle_request()


class SpotifyCharts:
    def __init__(self, access_token: str | None = None) -> None:
        if access_token:
            self.headers = {"Authorization": f"Bearer {access_token}"}
            return

        if not client_id or not client_secret or not redirect_uri or not redirect_port:
            raise RuntimeError("Set Spotify client credentials and redirect settings before using the local OAuth flow.")

        if not path.exists(".refresh_token"):
            print("[!] Refresh Token Not Found. Login To Spotify!")
            get_refresh_token()

        with open(".refresh_token", "r", encoding="utf-8") as token_file:
            stored_refresh_token = token_file.read().strip()

        refresh_token_url = "https://accounts.spotify.com/api/token"
        data = {"grant_type": "refresh_token", "refresh_token": stored_refresh_token}

        with Client(timeout=15) as client:
            response = client.post(refresh_token_url, auth=(client_id, client_secret), data=data)
            response.raise_for_status()
            token_data = response.json()

        self.headers = {"Authorization": f"Bearer {token_data['access_token']}"}

    def get_countries(self) -> Countries:
        """
        :returns -> Enum Countries
        """

        return Countries

    async def _fetch_chart(self, url: str) -> dict:
        cached = _chart_cache.get(url)
        if cached and cached[0] > monotonic():
            return deepcopy(cached[1])

        async with _chart_inflight_lock:
            task = _chart_inflight.get(url)
            if task is None:
                task = asyncio.create_task(self._fetch_chart_uncached(url))
                _chart_inflight[url] = task
        try:
            return deepcopy(await asyncio.shield(task))
        finally:
            if task.done():
                async with _chart_inflight_lock:
                    if _chart_inflight.get(url) is task:
                        _chart_inflight.pop(url, None)

    async def _fetch_chart_uncached(self, url: str) -> dict:
        async with AsyncClient(timeout=15) as client:
            for attempt in range(3):
                await _wait_for_chart_request_slot()
                response = await client.get(url, headers=self.headers)
                if response.status_code not in {429, 500, 502, 503, 504}:
                    response.raise_for_status()
                    payload = response.json()
                    break

                retry_after = response.headers.get("Retry-After")
                try:
                    delay = max(float(retry_after), 0.25)
                except (TypeError, ValueError):
                    delay = 0.5 * (2**attempt)

                if response.status_code == 429:
                    await _defer_chart_requests(min(delay, CHART_RATE_LIMIT_MAX_COOLDOWN))
                if attempt == 2:
                    response.raise_for_status()
                await asyncio.sleep(delay)
        if not isinstance(payload, dict):
            raise ValueError("Spotify returned an unexpected chart response.")

        now = monotonic()
        _chart_cache[url] = (now + CHART_CACHE_TTL, deepcopy(payload))
        for cached_url, (expires_at, _) in list(_chart_cache.items()):
            if expires_at <= now:
                del _chart_cache[cached_url]
        return payload

    async def search_entities(self, query: str, entity_type: str = "all", limit: int = 10) -> list[dict]:
        if entity_type not in {"all", "track", "artist"}:
            raise ValueError("Choose songs, artists, or both to search.")

        search_types = "track,artist" if entity_type == "all" else entity_type
        async with AsyncClient(timeout=15) as client:
            response = await client.get(
                "https://api.spotify.com/v1/search",
                params={"q": query, "type": search_types, "limit": limit},
                headers=self.headers,
            )
            response.raise_for_status()
            payload = response.json()

        if not isinstance(payload, dict):
            raise ValueError("Spotify returned an unexpected search response.")

        results: list[dict] = []
        if entity_type in {"all", "track"}:
            tracks = (payload.get("tracks") or {}).get("items") or []
            for item in tracks:
                if not isinstance(item, dict) or not isinstance(item.get("uri"), str):
                    continue
                artists = item.get("artists") or []
                artist_names = [artist.get("name", "") for artist in artists if isinstance(artist, dict)]
                images = ((item.get("album") or {}).get("images") or [])
                results.append({
                    "entityType": "track",
                    "entityUri": item["uri"],
                    "entityName": item.get("name") or "Untitled",
                    "artistNames": ", ".join(name for name in artist_names if name),
                    "imageUrl": images[0].get("url") if images and isinstance(images[0], dict) else None,
                    "spotifyUrl": (item.get("external_urls") or {}).get("spotify"),
                    "popularity": int(item.get("popularity") or 0),
                })

        if entity_type in {"all", "artist"}:
            artists = (payload.get("artists") or {}).get("items") or []
            for item in artists:
                if not isinstance(item, dict) or not isinstance(item.get("uri"), str):
                    continue
                images = item.get("images") or []
                results.append({
                    "entityType": "artist",
                    "entityUri": item["uri"],
                    "entityName": item.get("name") or "Untitled",
                    "artistNames": "",
                    "imageUrl": images[0].get("url") if images and isinstance(images[0], dict) else None,
                    "spotifyUrl": (item.get("external_urls") or {}).get("spotify"),
                    "popularity": int(item.get("popularity") or 0),
                })

        return sorted(results, key=lambda item: item["popularity"], reverse=True)[:limit]

    async def daily_top_songs(self, country_code: str, date_: str = "latest") -> dict:
        """
        :param country_code: Country code of the country eg. IN -> India
        :param date_: To get data for a specific date, By default its Latest. eg. 2022-12-24

        :return: a list of daily top 200 songs from spotify charts.
        """
        url = f"https://charts-spotify-com-service.spotify.com/auth/v0/charts/regional-{country_code.lower()}-daily/{date_}"

        return await self._fetch_chart(url)

    async def daily_top_artists(self, country_code: str, date_: str = "latest") -> dict:
        """
        :param country_code: Country code of the country eg. IN -> India
        :param date_: To get data for a specific date, By default its Latest. eg. 2022-12-24

        :return: a list of daily top 200 artists from spotify charts.
        """
        url = f"https://charts-spotify-com-service.spotify.com/auth/v0/charts/artist-{country_code.lower()}-daily/{date_}"

        return await self._fetch_chart(url)

    async def daily_viral_songs(self, country_code: str, date_: str = "latest") -> dict:
        """
        :param country_code: Country code of the country eg. IN -> India
        :param date_: To get data for a specific date, By default its Latest. eg. 2022-12-24

        :return: a list of daily viral 200 songs from spotify charts.
        """

        url = f"https://charts-spotify-com-service.spotify.com/auth/v0/charts/viral-{country_code.lower()}-daily/{date_}"

        return await self._fetch_chart(url)

    async def weekly_top_songs(self, country_code: str, date_="latest") -> dict:
        """
        :param country_code: Country code of the country eg. IN -> India
        :param date_: Week Start Date.

        :return: a list of weekly top 200 songs from spotify charts.
        """
        url = f"https://charts-spotify-com-service.spotify.com/auth/v0/charts/regional-{country_code.lower()}-weekly/{date_}"

        return await self._fetch_chart(url)

    async def weekly_top_artists(self, country_code: str, date_="latest") -> dict:
        """
        :param country_code: Country code of the country eg. IN -> India
        :param date_: Week Start Date.

        :return: a list of daily top 200 artists from spotify charts.
        """

        url = f"https://charts-spotify-com-service.spotify.com/auth/v0/charts/artist-{country_code.lower()}-weekly/{date_}"

        return await self._fetch_chart(url)

    async def daily_top_albums(self, country_code: str, date_: str = "latest") -> dict:
        url = f"https://charts-spotify-com-service.spotify.com/auth/v0/charts/album-{country_code.lower()}-daily/{date_}"
        return await self._fetch_chart(url)

    async def weekly_top_albums(self, country_code: str, date_="latest") -> dict:
        """
        :param country_code: Country code of the country eg. IN -> India
        :param date_: To get data for a specific date, By default its Latest. eg. 2022-12-24

        :return: a list of weekly top 200 albums from spotify charts.
        """

        url = f"https://charts-spotify-com-service.spotify.com/auth/v0/charts/album-{country_code.lower()}-weekly/{date_}"

        return await self._fetch_chart(url)
