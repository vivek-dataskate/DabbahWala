-- 054_menu_sync.sql
-- Weekly menu sync infrastructure:
--   weekly_menu        — which items are on the menu for a given week
--   menu_sync_log      — audit log of each scrape/sync attempt

SET search_path TO dabbahwala;

-- Track which items appear each week (may differ from the full catalog)
CREATE TABLE IF NOT EXISTS weekly_menu (
    id              BIGSERIAL PRIMARY KEY,
    week_start      DATE NOT NULL,              -- Monday of the week
    menu_item_id    BIGINT REFERENCES menu_items(id),
    item_name       TEXT NOT NULL,              -- denormalised for speed
    category        TEXT,
    is_veg          BOOLEAN,
    price           NUMERIC(8,2),
    is_featured     BOOLEAN NOT NULL DEFAULT false,
    display_order   INT NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (week_start, item_name)
);

CREATE INDEX IF NOT EXISTS idx_weekly_menu_week ON weekly_menu (week_start DESC);
CREATE INDEX IF NOT EXISTS idx_weekly_menu_item  ON weekly_menu (menu_item_id);

-- Audit every sync attempt (success or failure)
CREATE TABLE IF NOT EXISTS menu_sync_log (
    id              BIGSERIAL PRIMARY KEY,
    synced_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    week_start      DATE,
    source          TEXT NOT NULL DEFAULT 'scraper',  -- 'scraper' | 'manual' | 'api'
    items_found     INT NOT NULL DEFAULT 0,
    items_upserted  INT NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'ok',       -- 'ok' | 'error' | 'partial'
    error_msg       TEXT,
    raw_snapshot    JSONB DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_menu_sync_log_at ON menu_sync_log (synced_at DESC);
