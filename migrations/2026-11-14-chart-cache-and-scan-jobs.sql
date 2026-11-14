-- Additive chart payload cache and durable refresh/checkpoint state.
-- Existing history rows and normalized snapshot semantics are preserved.
CREATE TABLE IF NOT EXISTS public.spotify_chart_payload_cache (
    chart_type text NOT NULL CHECK (chart_type = ANY (ARRAY['daily_top_songs','daily_top_artists','daily_viral_songs','daily_top_albums','weekly_top_songs','weekly_top_artists','weekly_top_albums'])),
    country_code text NOT NULL,
    requested_date text NOT NULL CHECK (requested_date = 'latest' OR requested_date ~ '^\d{4}-\d{2}-\d{2}$'),
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object' AND jsonb_typeof(payload->'entries') = 'array'),
    chart_date date,
    fetched_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (chart_type, country_code, requested_date)
);
CREATE INDEX IF NOT EXISTS spotify_chart_payload_cache_fetched_at_idx
    ON public.spotify_chart_payload_cache (fetched_at);

CREATE TABLE IF NOT EXISTS public.spotify_chart_refresh_jobs (
    chart_type text NOT NULL CHECK (chart_type = ANY (ARRAY['daily_top_songs','daily_top_artists','daily_viral_songs','daily_top_albums','weekly_top_songs','weekly_top_artists','weekly_top_albums'])),
    country_code text NOT NULL,
    requested_date text NOT NULL CHECK (requested_date = 'latest' OR requested_date ~ '^\d{4}-\d{2}-\d{2}$'),
    status text NOT NULL DEFAULT 'pending' CHECK (status = ANY (ARRAY['pending','running','complete','failed'])),
    attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    next_attempt_at timestamptz NOT NULL DEFAULT now(),
    lease_owner text,
    lease_expires_at timestamptz,
    last_error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (chart_type, country_code, requested_date)
);

CREATE TABLE IF NOT EXISTS public.spotify_focused_scan_jobs (
    cadence text NOT NULL CHECK (cadence = ANY (ARRAY['daily','weekly'])),
    entity_type text NOT NULL CHECK (entity_type = ANY (ARRAY['track','artist','album'])),
    status text NOT NULL DEFAULT 'pending' CHECK (status = ANY (ARRAY['pending','running','complete','failed'])),
    completed_markets text[] NOT NULL DEFAULT ARRAY[]::text[],
    failed_markets jsonb NOT NULL DEFAULT '[]'::jsonb,
    partial_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    lease_owner text,
    lease_expires_at timestamptz,
    attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    last_error text,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (cadence, entity_type)
);

ALTER TABLE public.spotify_chart_snapshots
    DROP CONSTRAINT IF EXISTS spotify_chart_snapshots_chart_type_check,
    DROP CONSTRAINT IF EXISTS spotify_chart_snapshots_entity_type_check,
    ADD CONSTRAINT spotify_chart_snapshots_chart_type_check CHECK (chart_type = ANY (ARRAY['daily_top_songs','weekly_top_songs','daily_top_artists','weekly_top_artists','daily_top_albums','weekly_top_albums'])),
    ADD CONSTRAINT spotify_chart_snapshots_entity_type_check CHECK (entity_type = ANY (ARRAY['track','artist','album']));
ALTER TABLE public.spotify_chart_backfills
    DROP CONSTRAINT IF EXISTS spotify_chart_backfills_chart_type_check,
    ADD CONSTRAINT spotify_chart_backfills_chart_type_check CHECK (chart_type = ANY (ARRAY['daily_top_songs','weekly_top_songs','daily_top_artists','weekly_top_artists','daily_top_albums','weekly_top_albums']));
