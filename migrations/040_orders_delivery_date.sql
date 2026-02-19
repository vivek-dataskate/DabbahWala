-- 040_orders_delivery_date.sql
-- Add explicit delivery_date column to orders.
-- order_date = when the order was placed (or subscription start date)
-- delivery_date = the actual date food is/was delivered
-- For daily CSV uploads, delivery_date = the date in the CSV row.
-- Backfilled from order_date for all existing rows.

ALTER TABLE orders ADD COLUMN IF NOT EXISTS delivery_date DATE;

-- Backfill: assume delivery_date = order_date for all existing records
UPDATE orders SET delivery_date = order_date WHERE delivery_date IS NULL;

CREATE INDEX IF NOT EXISTS idx_orders_delivery_date ON orders (delivery_date DESC);
