#!/usr/bin/env python3
"""
Split bulk_load.sql into chunked SQL files in data/sql/ with ~1000 INSERT
statements per file. Each file is self-contained with SET search_path,
BEGIN, and COMMIT.
"""
import os
import re

INPUT = os.path.join(os.path.dirname(__file__), "bulk_load.sql")
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "sql")

CHUNK_SIZE = 1000

HEADER = "SET search_path TO dabbahwala;\n\nBEGIN;\n\n"
FOOTER = "\nCOMMIT;\n"


def split():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Read all lines
    with open(INPUT) as f:
        lines = f.readlines()

    # Parse sections: find comment headers and collect INSERT lines per section
    sections = []  # list of (label, [insert_lines])
    current_label = None
    current_lines = []

    for line in lines:
        stripped = line.strip()
        # Detect section header comments like "-- Menu items (125 rows)"
        m = re.match(r'^-- (Menu items|Contacts|Orders|Order items|Events)', stripped)
        if m:
            if current_label and current_lines:
                sections.append((current_label, current_lines))
            current_label = m.group(1)
            current_lines = []
            continue
        # Collect INSERT lines
        if stripped.startswith("INSERT "):
            current_lines.append(line)

    # Don't forget the last section
    if current_label and current_lines:
        sections.append((current_label, current_lines))

    # Map section labels to filename prefixes
    prefix_map = {
        "Menu items": "01_menu_items",
        "Contacts": "02_contacts",
        "Orders": "03_orders",
        "Order items": "04_order_items",
        "Events": "05_events",
    }

    file_index = 0
    manifest = []

    for label, inserts in sections:
        prefix = prefix_map.get(label, label.lower().replace(" ", "_"))
        total = len(inserts)
        chunk_num = 0

        for start in range(0, total, CHUNK_SIZE):
            chunk_num += 1
            chunk = inserts[start : start + CHUNK_SIZE]
            file_index += 1

            fname = f"{prefix}_part{chunk_num:02d}.sql"
            fpath = os.path.join(OUTPUT_DIR, fname)

            with open(fpath, "w") as f:
                f.write(f"-- {label} part {chunk_num} ({len(chunk)} rows, {start+1}-{start+len(chunk)} of {total})\n")
                f.write(f"-- Run order: {file_index}\n\n")
                f.write(HEADER)
                for sql_line in chunk:
                    f.write(sql_line)
                f.write(FOOTER)

            size_kb = os.path.getsize(fpath) / 1024
            manifest.append((fname, len(chunk), f"{size_kb:.1f} KB"))
            print(f"  {fname:40s}  {len(chunk):5d} rows  {size_kb:7.1f} KB")

    # Write a manifest / README
    with open(os.path.join(OUTPUT_DIR, "README.md"), "w") as f:
        f.write("# DabbahWala SQL Data Load Files\n\n")
        f.write("Run these files **in order** against the Postgres database.\n\n")
        f.write("Each file is a self-contained transaction (BEGIN/COMMIT).\n\n")
        f.write("```bash\n")
        f.write("# Run all in order:\n")
        f.write("for f in data/sql/0*.sql; do psql $DATABASE_URL < \"$f\"; done\n")
        f.write("```\n\n")
        f.write("| # | File | Rows | Size |\n")
        f.write("|---|------|------|------|\n")
        for i, (fname, rows, size) in enumerate(manifest, 1):
            f.write(f"| {i} | `{fname}` | {rows} | {size} |\n")
        f.write(f"\n**Total: {sum(r for _, r, _ in manifest)} rows across {len(manifest)} files**\n")

    print(f"\n  Generated {len(manifest)} SQL files in {OUTPUT_DIR}")
    print(f"  Total rows: {sum(r for _, r, _ in manifest)}")


if __name__ == "__main__":
    split()
