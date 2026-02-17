from contextlib import contextmanager

import psycopg2
from psycopg2.extras import RealDictCursor

from app.config import DATABASE_URL

_pool = None


def get_connection():
    return psycopg2.connect(DATABASE_URL)


@contextmanager
def get_cursor(commit=True):
    conn = get_connection()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        yield cur
        if commit:
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()
