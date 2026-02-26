-- 061_menu_catalog.sql
-- Replace the old per-week menu tables with a single per-item menu_catalog.
--
-- Dropped:
--   weekly_menu         — per-week item schedule (replaced by catalog active flag)
--   menu_sync_log       — scraper audit log (replaced by menu_catalog_history)
--   menu_item_aliases   — CSV alias table (alias resolution now uses ILIKE on menu_catalog)
--   menu_items          — old analytics catalog (replaced by menu_catalog)
--
-- Created:
--   menu_catalog         — one row per item, permanent record; Airtable is source of truth
--   menu_catalog_history — audit trail for every price/status change

SET search_path TO dabbahwala;

-- ── Drop old tables ─────────────────────────────────────────────────────────

DROP TABLE IF EXISTS weekly_menu CASCADE;
DROP TABLE IF EXISTS menu_sync_log CASCADE;
DROP TABLE IF EXISTS menu_item_aliases CASCADE;

-- Remove FK from order_items → menu_items (keep column as bare BIGINT for historical data)
ALTER TABLE order_items
    DROP CONSTRAINT IF EXISTS order_items_menu_item_id_fkey;

DROP TABLE IF EXISTS menu_items CASCADE;

-- ── Per-item catalog ─────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS menu_catalog (
    id                  BIGSERIAL PRIMARY KEY,
    item_name           TEXT UNIQUE NOT NULL,
    category            TEXT,
    is_veg              BOOLEAN,
    description         TEXT,
    image_url           TEXT,
    price               NUMERIC(8,2),
    active              BOOLEAN NOT NULL DEFAULT TRUE,   -- false = discarded (deleted from Airtable)
    added_date          DATE,
    discarded_date      DATE,                            -- set automatically when deleted from Airtable
    airtable_record_id  TEXT,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_menu_catalog_airtable_id
    ON menu_catalog (airtable_record_id)
    WHERE airtable_record_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_menu_catalog_active ON menu_catalog (active);
CREATE INDEX IF NOT EXISTS idx_menu_catalog_name   ON menu_catalog (item_name);

-- ── Change history ────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS menu_catalog_history (
    id              BIGSERIAL PRIMARY KEY,
    menu_catalog_id BIGINT NOT NULL REFERENCES menu_catalog(id),
    item_name       TEXT NOT NULL,
    change_type     TEXT NOT NULL,   -- 'added' | 'price_change' | 'discarded' | 'field_update'
    field_changed   TEXT,
    old_value       TEXT,
    new_value       TEXT,
    changed_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    source          TEXT DEFAULT 'airtable_sync'
);

CREATE INDEX IF NOT EXISTS idx_menu_history_item
    ON menu_catalog_history (menu_catalog_id, changed_at DESC);

-- ── Update stored procedure to use menu_catalog ───────────────────────────────

CREATE OR REPLACE FUNCTION get_top_menu_items(p_days INT DEFAULT 30, p_limit INT DEFAULT 15)
RETURNS JSON AS $$
DECLARE result JSON;
BEGIN
    SELECT json_agg(row_to_json(t)) INTO result FROM (
        SELECT
            oi.item_name,
            mc.category,
            mc.is_veg,
            SUM(oi.quantity)                       AS total_qty,
            COUNT(DISTINCT oi.order_id)            AS order_count,
            ROUND(AVG(oi.unit_price)::numeric, 2)  AS avg_price,
            ROUND(SUM(oi.line_total)::numeric, 2)  AS total_revenue
        FROM order_items oi
        JOIN orders o ON o.id = oi.order_id
        LEFT JOIN menu_catalog mc ON mc.item_name = oi.item_name
        WHERE o.order_date >= CURRENT_DATE - p_days
        GROUP BY oi.item_name, mc.category, mc.is_veg
        ORDER BY total_qty DESC
        LIMIT p_limit
    ) t;
    RETURN COALESCE(result, '[]'::json);
END;
$$ LANGUAGE plpgsql;
