-- 041_orders_delivery_date_fix.sql
-- Ensure delivery_date column exists on dabbahwala.orders.
-- Uses fully-qualified schema.table to avoid search_path issues.

ALTER TABLE dabbahwala.orders ADD COLUMN IF NOT EXISTS delivery_date DATE;

UPDATE dabbahwala.orders SET delivery_date = order_date WHERE delivery_date IS NULL;

CREATE INDEX IF NOT EXISTS idx_orders_delivery_date ON dabbahwala.orders (delivery_date DESC);
