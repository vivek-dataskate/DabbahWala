-- 06_fix_menu_item_duplicates.sql
-- Fixes menu items that were created without metadata from CSV uploads,
-- and re-points order_items to the correct canonical menu_item_id.
--
-- Run AFTER 028_menu_item_aliases.sql migration.

SET search_path TO dabbahwala;

BEGIN;

-- ─────────────────────────────────────────────────────
-- 1. Fix order_items that reference orphan/duplicate menu items
--    Re-map them to the canonical item using the alias table
-- ─────────────────────────────────────────────────────

-- Update order_items.item_name and menu_item_id for aliased items
UPDATE order_items oi SET
    item_name    = a.canonical_name,
    menu_item_id = (SELECT id FROM menu_items WHERE item_name = a.canonical_name LIMIT 1)
FROM menu_item_aliases a
WHERE oi.item_name = a.alias
  AND oi.item_name != a.canonical_name;

-- ─────────────────────────────────────────────────────
-- 2. Fix "Butter Chicken " (trailing space) → "Butter chicken"
-- ─────────────────────────────────────────────────────

UPDATE order_items SET
    item_name    = 'Butter chicken',
    menu_item_id = (SELECT id FROM menu_items WHERE item_name = 'Butter chicken' LIMIT 1)
WHERE trim(item_name) = 'Butter Chicken'
  AND item_name != 'Butter chicken';

-- ─────────────────────────────────────────────────────
-- 3. Backfill metadata on menu items that were auto-created without category/price
-- ─────────────────────────────────────────────────────

-- Set avg_price from actual order data for items missing it
UPDATE menu_items mi SET
    avg_price = sub.avg_p
FROM (
    SELECT menu_item_id, round(avg(unit_price), 2) as avg_p
    FROM order_items
    WHERE menu_item_id IS NOT NULL AND unit_price > 0
    GROUP BY menu_item_id
) sub
WHERE mi.id = sub.menu_item_id
  AND (mi.avg_price IS NULL OR mi.avg_price = 0);

-- ─────────────────────────────────────────────────────
-- 4. Delete orphan menu_items that are now fully aliased
--    (only if no order_items still reference them)
-- ─────────────────────────────────────────────────────

DELETE FROM menu_items
WHERE item_name IN (SELECT alias FROM menu_item_aliases)
  AND item_name NOT IN (SELECT canonical_name FROM menu_item_aliases)
  AND id NOT IN (SELECT DISTINCT menu_item_id FROM order_items WHERE menu_item_id IS NOT NULL);

COMMIT;
