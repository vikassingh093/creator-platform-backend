"""
Database module with SQLAlchemy Connection Pooling.
Uses raw SQL (no ORM) — drop-in replacement for old pymysql version.
All execute_query() calls across the project work WITHOUT any changes.
"""

from sqlalchemy import create_engine, event
from sqlalchemy.pool import QueuePool
from app.config import (
    DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME,
    DB_POOL_SIZE, DB_MAX_OVERFLOW, DB_POOL_TIMEOUT,
    DB_POOL_RECYCLE, DB_ECHO_SQL, SLOW_QUERY_THRESHOLD, DEBUG
)
from contextlib import contextmanager
import pymysql.cursors
import logging
import time

logger = logging.getLogger("app.database")

# ── Build connection URL ─────────────────────────────────────
DATABASE_URL = (
    f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
    f"?charset=utf8mb4"
)

# ── Create engine with pool ──────────────────────────────────
engine = create_engine(
    DATABASE_URL,
    poolclass=QueuePool,
    pool_size=DB_POOL_SIZE,
    max_overflow=DB_MAX_OVERFLOW,
    pool_timeout=DB_POOL_TIMEOUT,
    pool_recycle=DB_POOL_RECYCLE,
    pool_pre_ping=True,
    echo=DB_ECHO_SQL,
    isolation_level="READ COMMITTED",
)

# ── Pool monitoring ──────────────────────────────────────────
_pool_stats = {"checkouts": 0, "checkins": 0, "errors": 0}

@event.listens_for(engine, "checkout")
def on_checkout(dbapi_conn, connection_record, connection_proxy):
    _pool_stats["checkouts"] += 1

@event.listens_for(engine, "checkin")
def on_checkin(dbapi_conn, connection_record):
    _pool_stats["checkins"] += 1


def get_pool_status():
    """Get current pool status — used by /health endpoint."""
    pool = engine.pool
    return {
        "pool_size": pool.size(),
        "checked_in": pool.checkedin(),
        "checked_out": pool.checkedout(),
        "overflow": pool.overflow(),
        "total_checkouts": _pool_stats["checkouts"],
        "total_checkins": _pool_stats["checkins"],
        "total_errors": _pool_stats["errors"],
    }


# ── Context manager (backward compatible) ────────────────────
@contextmanager
def get_db():
    """
    Get a raw DBAPI connection from the pool.
    Auto commits on success, rollbacks on error, returns to pool on close.
    """
    conn = engine.raw_connection()
    try:
        yield conn
        conn.commit()
    except Exception as e:
        conn.rollback()
        _pool_stats["errors"] += 1
        logger.error(f"DB Error: {e}", exc_info=DEBUG)
        raise
    finally:
        conn.close()  # Returns to pool, does NOT destroy


# ── Main query executor (SAME API as before + row_count) ─────
def execute_query(
    query: str,
    params: tuple = None,
    fetch_one: bool = False,
    fetch_all: bool = False,
    last_row_id: bool = False,
    row_count: bool = False
):
    """
    Execute a raw SQL query using a pooled connection.
    API is 100% identical to the old version — no changes needed anywhere.

    Args:
        query: SQL query string
        params: Query parameters tuple
        fetch_one: Return single row as dict
        fetch_all: Return all rows as list of dicts
        last_row_id: Return last inserted row ID
        row_count: Return number of rows affected (for UPDATE/DELETE)

    Returns:
        Depends on flags. Default returns True on success.
    """
    start_time = time.time()

    with get_db() as conn:
        cursor = conn.cursor(pymysql.cursors.DictCursor)
        try:
            cursor.execute(query, params)

            if fetch_one:
                result = cursor.fetchone()
            elif fetch_all:
                result = cursor.fetchall()
            elif last_row_id:
                result = cursor.lastrowid
            elif row_count:
                result = cursor.rowcount
            else:
                result = True

            # ── Slow query logging ───────────────────────────
            elapsed = time.time() - start_time
            if elapsed > SLOW_QUERY_THRESHOLD:
                short_query = query.strip().replace("\n", " ")[:200]
                logger.warning(
                    f"🐢 SLOW QUERY ({elapsed:.2f}s): {short_query} | params={params}"
                )

            return result

        finally:
            cursor.close()


def execute_many(query: str, params_list: list):
    """Execute bulk insert/update — same API as before."""
    with get_db() as conn:
        cursor = conn.cursor()
        try:
            cursor.executemany(query, params_list)
            return cursor.rowcount
        finally:
            cursor.close()


# ── Startup log ──────────────────────────────────────────────
logger.info(
    f"✅ DB Pool initialized | size={DB_POOL_SIZE} max_overflow={DB_MAX_OVERFLOW} "
    f"pre_ping=True recycle={DB_POOL_RECYCLE}s | {DB_HOST}:{DB_PORT}/{DB_NAME}"
)