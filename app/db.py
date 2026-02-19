from contextlib import contextmanager

import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2.pool import SimpleConnectionPool

from app.config import DATABASE_URL

_pool = None
_pool_conn_ids: set = set()  # track connections from pool by id()


def _get_pool():
    global _pool
    if _pool is None or _pool.closed:
        _pool = SimpleConnectionPool(1, 10, DATABASE_URL)
    return _pool


def get_connection():
    try:
        pool = _get_pool()
        conn = pool.getconn()
        _pool_conn_ids.add(id(conn))
    except Exception:
        # Fallback: direct connection if pool fails
        conn = psycopg2.connect(DATABASE_URL)
    # Set search_path so all queries resolve to the dabbahwala schema
    with conn.cursor() as cur:
        cur.execute("SET search_path TO dabbahwala")
    conn.commit()
    return conn


def _return_connection(conn):
    conn_id = id(conn)
    if conn_id in _pool_conn_ids:
        _pool_conn_ids.discard(conn_id)
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
