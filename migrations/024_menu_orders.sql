-- 024_menu_orders.sql
-- Menu items catalog + orders + order line items for analytics

SET search_path TO dabbahwala;

-- Menu items catalog
CREATE TABLE IF NOT EXISTS menu_items (
    id              BIGSERIAL PRIMARY KEY,
    item_name       TEXT UNIQUE NOT NULL,
    category        TEXT,               -- 'thali', 'biryani', 'curry', 'roti', 'sides', 'beverages', 'dessert', 'combo'
    is_veg          BOOLEAN,
    avg_price       NUMERIC(8,2) DEFAULT 0,
    is_active       BOOLEAN NOT NULL DEFAULT true,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Orders table (one row per order)
CREATE TABLE IF NOT EXISTS orders (
    id                  BIGSERIAL PRIMARY KEY,
    contact_id          BIGINT REFERENCES contacts(id),
    order_id_external   TEXT,
    order_date          DATE NOT NULL,
    source              TEXT NOT NULL DEFAULT 'Website',   -- 'Website' or 'Food Delivery Apps'
    app_platform        TEXT,                               -- 'DoorDash', 'Uber Eats', 'Grubhub', null for website
    total_amount        NUMERIC(10,2) DEFAULT 0,
    order_type          TEXT,                               -- 'SUBSCRIPTION' or 'DABBHAH'
    delivery_slot       TEXT,
    customer_name_raw   TEXT,                               -- original name from data (for app order matching)
    metadata            JSONB DEFAULT '{}',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Order line items (one row per item per order)
CREATE TABLE IF NOT EXISTS order_items (
    id              BIGSERIAL PRIMARY KEY,
    order_id        BIGINT NOT NULL REFERENCES orders(id),
    menu_item_id    BIGINT REFERENCES menu_items(id),
    item_name       TEXT NOT NULL,
    quantity        INT NOT NULL DEFAULT 1,
    unit_price      NUMERIC(8,2) DEFAULT 0,
    line_total      NUMERIC(8,2) DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Add address + subscription fields to contacts
ALTER TABLE contacts ADD COLUMN IF NOT EXISTS address TEXT;
ALTER TABLE contacts ADD COLUMN IF NOT EXISTS subscription_type TEXT;  -- 'SUBSCRIPTION', 'DABBHAH', 'BOTH'
ALTER TABLE contacts ADD COLUMN IF NOT EXISTS primary_source TEXT DEFAULT 'Website';  -- 'Website', 'Food Delivery Apps', 'BOTH'
ALTER TABLE contacts ADD COLUMN IF NOT EXISTS merged_from_app BOOLEAN DEFAULT false;  -- true if fuzzy-matched from app

-- Indexes
CREATE INDEX IF NOT EXISTS idx_orders_contact ON orders (contact_id, order_date DESC);
CREATE INDEX IF NOT EXISTS idx_orders_date ON orders (order_date DESC);
CREATE INDEX IF NOT EXISTS idx_orders_source ON orders (source);
CREATE INDEX IF NOT EXISTS idx_order_items_order ON order_items (order_id);
CREATE INDEX IF NOT EXISTS idx_order_items_menu ON order_items (menu_item_id);
CREATE INDEX IF NOT EXISTS idx_menu_items_category ON menu_items (category);
