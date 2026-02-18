"""
import_contacts.py — Bulk import contacts from CSV into Postgres

Usage:
    python scripts/import_contacts.py contacts.csv

CSV format:
    email,phone,first_name,last_name
    john@example.com,+15551234567,John,Doe
"""

import csv
import os
import sys

import psycopg2

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://localhost:5432/dabbahwala")

# Render uses postgres:// but psycopg2 needs postgresql://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)


def import_contacts(csv_path: str):
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    cur.execute("SET search_path TO dabbahwala")

    imported = 0
    skipped = 0

    with open(csv_path, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            email = row.get("email", "").strip()
            if not email:
                skipped += 1
                continue

            try:
                cur.execute(
                    """
                    INSERT INTO contacts (email, phone, first_name, last_name)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (email) DO NOTHING
                    """,
                    (
                        email,
                        row.get("phone", "").strip() or None,
                        row.get("first_name", "").strip() or None,
                        row.get("last_name", "").strip() or None,
                    ),
                )
                if cur.rowcount > 0:
                    imported += 1
                else:
                    skipped += 1
            except Exception as e:
                print(f"Error importing {email}: {e}")
                conn.rollback()
                cur.execute("SET search_path TO dabbahwala")
                skipped += 1
                continue

    conn.commit()
    cur.close()
    conn.close()

    print(f"Imported: {imported}, Skipped (duplicate/empty): {skipped}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python scripts/import_contacts.py <csv_file>")
        sys.exit(1)

    import_contacts(sys.argv[1])
