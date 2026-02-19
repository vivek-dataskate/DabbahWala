-- 028_menu_item_aliases.sql
-- Adds alias lookup table so CSV dish names resolve to canonical master menu items.
-- Also adds new legitimate menu items and classifies them properly.

SET search_path TO dabbahwala;

-- ─────────────────────────────────────────────────────
-- 1. Alias table: maps alternate CSV names → canonical item_name
-- ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS menu_item_aliases (
    id              BIGSERIAL PRIMARY KEY,
    alias           TEXT UNIQUE NOT NULL,       -- name as it appears in CSV
    canonical_name  TEXT NOT NULL,              -- must match menu_items.item_name
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_alias_lookup ON menu_item_aliases (alias);

-- ─────────────────────────────────────────────────────
-- 2. Add new legitimate menu items (not in the original 125-item master)
-- ─────────────────────────────────────────────────────
INSERT INTO menu_items (item_name, category, is_veg, avg_price) VALUES
    ('Aloo Capsicum',                         'curry',    true,   12.00),
    ('Fish Pulusu',                           'curry',    false,  12.00),
    ('Khatti Dal',                            'curry',    true,   10.00),
    ('Carrot Cabbage Fried Rice',             'sides',    true,   10.00),
    ('Paneer Puff',                           'snack',    true,    8.00),
    ('Shrikhand',                             'dessert',  true,    5.99),
    ('Sunnundalu (Urad Dal Laddu)',           'dessert',  true,    6.99),
    ('Toori Toor Dal (Beerakaya Pappu)',      'curry',    true,   10.00),
    ('White Rice',                            'sides',    true,    4.00),
    ('Roti - 5 Pcs',                          'roti',     true,    3.00),
    ('Roti - 10 Pcs',                         'roti',     true,    5.00),
    ('Samosa - 3 Pcs',                        'snack',    true,    8.99)
ON CONFLICT (item_name) DO UPDATE SET
    category = EXCLUDED.category,
    is_veg   = EXCLUDED.is_veg,
    avg_price = EXCLUDED.avg_price;

-- ─────────────────────────────────────────────────────
-- 3. Populate aliases: CSV name → canonical master name
-- ─────────────────────────────────────────────────────
-- Thali naming: CSV uses "Single's Thali - Veg", master uses "Single - Veg Thali Box"
INSERT INTO menu_item_aliases (alias, canonical_name) VALUES
    ('Single''s Thali - Veg',         'Single - Veg Thali Box'),
    ('Single''s Thali - Non Veg',     'Single - Non-Veg Thali Box'),
    ('Singles Thali - Veg',           'Single - Veg Thali Box'),
    ('Singles Thali - Non Veg',       'Single - Non-Veg Thali Box'),
    ('Couples Thali - Veg',          'Couple''s - Veg Thali Box'),
    ('Couples Thali - Non Veg',      'Couple''s - Non-Veg Thali Box'),
    ('Couple''s Thali - Veg',        'Couple''s - Veg Thali Box'),
    ('Couple''s Thali - Non Veg',    'Couple''s - Non-Veg Thali Box'),
    ('Family Thali - Veg',           'Family - Veg Thali Box'),
    ('Family Thali - Non Veg',       'Family - Non-Veg Thali Box'),
    ('Family Thali - Non-Veg',       'Family - Non-Veg Thali Box'),
    -- Case / spacing variants
    ('Butter Chicken',               'Butter chicken'),
    ('butter chicken',               'Butter chicken'),
    ('Mutter paneer',                'Mutter Paneer'),
    ('mutter paneer',                'Mutter Paneer'),
    ('PineApple Kesari',             'Pineapple Kesari'),
    ('Pineapple kesari',             'Pineapple Kesari'),
    -- Tandoori Chicken vs Tandoori Chicken - 4pcs (standalone portion)
    ('Tandoori Chicken',             'Tandoori Chicken - 4pcs'),
    ('tandoori chicken',             'Tandoori Chicken - 4pcs'),
    -- White Rice → separate item (not Organic White Rice which is basmati)
    ('white rice',                   'White Rice'),
    -- Roti naming
    ('Roti - 10 pcs',               'Roti - 10 Pcs'),
    ('Roti - 5 pcs',                'Roti - 5 Pcs'),
    ('Roti - 5pcs',                 'Roti - 5 Pcs'),
    ('Roti - 10pcs',                'Roti - 10 Pcs'),
    -- Samosa naming
    ('Samosa - 3 pcs',              'Samosa - 3 Pcs'),
    ('Samosa - 3pcs',               'Samosa - 3 Pcs'),
    -- Case variants for new items
    ('Aloo capsicum',               'Aloo Capsicum'),
    ('aloo capsicum',               'Aloo Capsicum'),
    ('Fish pulusu',                 'Fish Pulusu'),
    ('fish pulusu',                 'Fish Pulusu'),
    ('Khatti dal',                  'Khatti Dal'),
    ('khatti dal',                  'Khatti Dal'),
    ('Paneer puff',                 'Paneer Puff'),
    ('paneer puff',                 'Paneer Puff'),
    ('Shrikhand',                   'Shrikhand'),
    ('shrikhand',                   'Shrikhand'),
    ('Sunnundalu ( Urad Dal Laddu)',  'Sunnundalu (Urad Dal Laddu)'),
    ('Toori toor dal ( Beerakaya pappu)', 'Toori Toor Dal (Beerakaya Pappu)'),
    ('Carrot cabbage fried rice',   'Carrot Cabbage Fried Rice'),
    ('white Rice',                  'White Rice')
ON CONFLICT (alias) DO NOTHING;
