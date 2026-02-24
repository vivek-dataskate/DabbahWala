-- 054_weekly_menu_schedule.sql
-- Stores weekly menu items scraped from dabbahwala.com
-- Keyed by week_start date so we can track what was on the menu each week.

SET search_path TO dabbahwala;

CREATE TABLE IF NOT EXISTS weekly_menu_schedule (
    id          BIGSERIAL PRIMARY KEY,
    week_start  DATE NOT NULL,          -- Monday (or start date) of the delivery week
    item_name   TEXT NOT NULL,
    category    TEXT,                   -- 'veg', 'non-veg', 'dessert', 'sides', etc. as shown on site
    is_veg      BOOLEAN,
    description TEXT,                   -- any subtitle/description shown on the site
    image_url   TEXT,                   -- product image URL if scraped
    scraped_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    metadata    JSONB DEFAULT '{}'
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_weekly_menu_week_item
    ON weekly_menu_schedule (week_start, item_name);

CREATE INDEX IF NOT EXISTS idx_weekly_menu_week
    ON weekly_menu_schedule (week_start DESC);
