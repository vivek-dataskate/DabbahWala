-- Add customer delivery notes to orders
ALTER TABLE orders ADD COLUMN IF NOT EXISTS notes TEXT;
