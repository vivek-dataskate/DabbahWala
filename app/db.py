import logging
from contextlib import contextmanager

import psycopg2
from fastapi import HTTPException
from psycopg2.extras import RealDictCursor
from psycopg2.pool import SimpleConnectionPool

from app.config import DATABASE_URL

logger = logging.getLogger(__name__)

_pool = None
_pool_conn_ids: set = set()  # track connections from pool by id()


def _get_pool():
    global _pool
    if _pool is None or _pool.closed:
        logger.info("Initializing PostgreSQL connection pool (min=1 max=10)")
        try:
            _pool = SimpleConnectionPool(1, 10, DATABASE_URL)
            logger.info("Connection pool ready")
        except Exception as e:
            logger.error("Failed to initialize connection pool: %s", e, exc_info=True)
            raise
    return _pool


def get_connection():
    try:
        pool = _get_pool()
        conn = pool.getconn()
        _pool_conn_ids.add(id(conn))
        logger.debug("Acquired connection id=%s from pool", id(conn))
    except Exception as pool_err:
        # Fallback: direct connection if pool fails
        logger.warning(
            "Pool.getconn() failed (%s) — falling back to direct psycopg2 connection",
            pool_err,
        )
        try:
            conn = psycopg2.connect(DATABASE_URL)
            logger.info("Fallback direct connection established conn_id=%s", id(conn))
        except Exception as direct_err:
            logger.error(
                "Direct connection also failed: %s", direct_err, exc_info=True
            )
            raise
    return conn


def _return_connection(conn):
    conn_id = id(conn)
    if conn_id in _pool_conn_ids:
        _pool_conn_ids.discard(conn_id)
        try:
            _get_pool().putconn(conn)
            logger.debug("Connection id=%s returned to pool", conn_id)
            return
        except Exception as e:
            logger.warning(
                "Failed to return conn_id=%s to pool (%s) — closing directly",
                conn_id,
                e,
            )
    conn.close()
    logger.debug("Connection id=%s closed directly", conn_id)


@contextmanager
def get_cursor(commit=True):
    conn = get_connection()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        yield cur
        if commit:
            conn.commit()
            logger.debug("Transaction committed on conn_id=%s", id(conn))
    except Exception as e:
        if not isinstance(e, HTTPException):
            logger.error(
                "DB error on conn_id=%s — rolling back: %s", id(conn), e, exc_info=True
            )
        conn.rollback()
        raise
    finally:
        cur.close()
        _return_connection(conn)
