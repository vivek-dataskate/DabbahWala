# DabbahWala SQL Data Load Files

Run these files **in order** against the Postgres database.

Each file is a self-contained transaction (BEGIN/COMMIT).

```bash
# Run all in order:
for f in data/sql/0*.sql; do psql $DATABASE_URL < "$f"; done
```

| # | File | Rows | Size |
|---|------|------|------|
| 1 | `01_menu_items_part01.sql` | 125 | 29.6 KB |
| 2 | `02_contacts_part01.sql` | 1000 | 844.2 KB |
| 3 | `02_contacts_part02.sql` | 1000 | 828.6 KB |
| 4 | `02_contacts_part03.sql` | 1000 | 799.9 KB |
| 5 | `02_contacts_part04.sql` | 188 | 154.0 KB |
| 6 | `03_orders_part01.sql` | 1000 | 328.4 KB |
| 7 | `03_orders_part02.sql` | 1000 | 326.5 KB |
| 8 | `03_orders_part03.sql` | 1000 | 330.3 KB |
| 9 | `03_orders_part04.sql` | 1000 | 329.2 KB |
| 10 | `03_orders_part05.sql` | 1000 | 330.3 KB |
| 11 | `03_orders_part06.sql` | 1000 | 329.4 KB |
| 12 | `03_orders_part07.sql` | 1000 | 331.2 KB |
| 13 | `03_orders_part08.sql` | 1000 | 329.4 KB |
| 14 | `03_orders_part09.sql` | 1000 | 332.0 KB |
| 15 | `03_orders_part10.sql` | 1000 | 329.8 KB |
| 16 | `03_orders_part11.sql` | 1000 | 331.5 KB |
| 17 | `03_orders_part12.sql` | 1000 | 330.7 KB |
| 18 | `03_orders_part13.sql` | 1000 | 330.6 KB |
| 19 | `03_orders_part14.sql` | 1000 | 330.4 KB |
| 20 | `03_orders_part15.sql` | 1000 | 330.0 KB |
| 21 | `03_orders_part16.sql` | 1000 | 330.5 KB |
| 22 | `03_orders_part17.sql` | 1000 | 329.7 KB |
| 23 | `03_orders_part18.sql` | 1000 | 322.8 KB |
| 24 | `03_orders_part19.sql` | 493 | 154.2 KB |
| 25 | `04_order_items_part01.sql` | 1000 | 322.1 KB |
| 26 | `04_order_items_part02.sql` | 716 | 232.3 KB |
| 27 | `05_events_part01.sql` | 1000 | 382.9 KB |
| 28 | `05_events_part02.sql` | 1000 | 380.9 KB |
| 29 | `05_events_part03.sql` | 1000 | 384.6 KB |
| 30 | `05_events_part04.sql` | 1000 | 384.2 KB |
| 31 | `05_events_part05.sql` | 1000 | 385.3 KB |
| 32 | `05_events_part06.sql` | 1000 | 383.9 KB |
| 33 | `05_events_part07.sql` | 1000 | 386.1 KB |
| 34 | `05_events_part08.sql` | 1000 | 384.0 KB |
| 35 | `05_events_part09.sql` | 1000 | 386.3 KB |
| 36 | `05_events_part10.sql` | 1000 | 384.0 KB |
| 37 | `05_events_part11.sql` | 1000 | 385.7 KB |
| 38 | `05_events_part12.sql` | 1000 | 385.0 KB |
| 39 | `05_events_part13.sql` | 1000 | 384.5 KB |
| 40 | `05_events_part14.sql` | 1000 | 384.6 KB |
| 41 | `05_events_part15.sql` | 1000 | 383.5 KB |
| 42 | `05_events_part16.sql` | 1000 | 384.8 KB |
| 43 | `05_events_part17.sql` | 1000 | 383.3 KB |
| 44 | `05_events_part18.sql` | 1000 | 386.3 KB |
| 45 | `05_events_part19.sql` | 493 | 191.7 KB |

**Total: 42015 rows across 45 files**
