from contextlib import contextmanager

import psycopg2
from psycopg2.extras import RealDictCursor

from app.config import DATABASE_URL

_pool = None


def get_connection():
    conn = psycopg2.connect(DATABASE_URL)
    # Set search_path so all queries resolve to the dabbahwala schema
    with conn.cursor() as cur:
        cur.execute("SET search_path TO dabbahwala")
    conn.commit()
    return conn


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
