from contextlib import contextmanager

import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2.pool import SimpleConnectionPool

from app.config import DATABASE_URL

_pool = None


def _get_pool():
    global _pool
    if _pool is None or _pool.closed:
        _pool = SimpleConnectionPool(1, 10, DATABASE_URL)
    return _pool


def get_connection():
    try:
        pool = _get_pool()
        conn = pool.getconn()
    except Exception:
        # Fallback: direct connection if pool fails
        conn = psycopg2.connect(DATABASE_URL)
        conn._from_pool = False
    else:
        conn._from_pool = True
    # Set search_path so all queries resolve to the dabbahwala schema
    with conn.cursor() as cur:
        cur.execute("SET search_path TO dabbahwala")
    conn.commit()
    return conn


def _return_connection(conn):
    if getattr(conn, '_from_pool', False):
        try:
            _get_pool().putconn(conn)
            return
        except Exception:
            pass
    conn.close()


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
        _return_connection(conn)
