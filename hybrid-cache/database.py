import time
import json
import logging
from contextlib import contextmanager
from typing import Optional, Tuple, Dict, Any
import psycopg2
from psycopg2 import pool
from psycopg2.extras import RealDictCursor
from config import Config

logger = logging.getLogger(__name__)

_pool: Optional[pool.ThreadedConnectionPool] = None


def get_pool() -> pool.ThreadedConnectionPool:
    global _pool
    if _pool is None:
        if not Config.DATABASE_URL:
            raise ValueError("DATABASE_URL is not configured.")
        _pool = pool.ThreadedConnectionPool(
            minconn=Config.DB_POOL_MIN,
            maxconn=Config.DB_POOL_MAX,
            dsn=Config.DATABASE_URL,
        )
    return _pool


@contextmanager
def get_db_connection():
    db_pool = get_pool()
    conn = db_pool.getconn()
    try:
        yield conn
    finally:
        db_pool.putconn(conn)


def init_db():
    """Initializes the records table according to Section 4.2 data model."""
    create_table_sql = """
    CREATE TABLE IF NOT EXISTS records (
        id BIGINT PRIMARY KEY,
        category VARCHAR(80) NOT NULL,
        payload JSONB NOT NULL,
        version BIGINT NOT NULL DEFAULT 1,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    CREATE INDEX IF NOT EXISTS idx_records_category ON records(category);
    """
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(create_table_sql)
        conn.commit()
    logger.info("Database initialized successfully.")


def db_get_record(record_id: int) -> Tuple[Optional[Dict[str, Any]], float]:
    """Fetches a record by ID directly from PostgreSQL and measures query latency."""
    query = """
    SELECT id, category, payload, version, updated_at
    FROM records
    WHERE id = %s;
    """
    start_time = time.perf_counter()
    with get_db_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, (record_id,))
            row = cur.fetchone()
    duration_ms = (time.perf_counter() - start_time) * 1000.0

    if row:
        row = dict(row)
        if isinstance(row.get("updated_at"), (str,)):
            pass
        elif row.get("updated_at"):
            row["updated_at"] = row["updated_at"].isoformat()
        return row, duration_ms
    return None, duration_ms


def db_create_record(record_id: int, category: str, payload: dict) -> Tuple[Dict[str, Any], float]:
    """Inserts a new record into PostgreSQL."""
    query = """
    INSERT INTO records (id, category, payload, version, updated_at)
    VALUES (%s, %s, %s, 1, NOW())
    RETURNING id, category, payload, version, updated_at;
    """
    start_time = time.perf_counter()
    with get_db_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, (record_id, category, json.dumps(payload)))
            row = cur.fetchone()
        conn.commit()
    duration_ms = (time.perf_counter() - start_time) * 1000.0

    result = dict(row)
    if result.get("updated_at"):
        result["updated_at"] = result["updated_at"].isoformat()
    return result, duration_ms


def db_update_record(record_id: int, category: str, payload: dict) -> Tuple[Optional[Dict[str, Any]], float]:
    """Updates a record in PostgreSQL, incrementing version and updated_at."""
    query = """
    UPDATE records
    SET category = %s,
        payload = %s,
        version = version + 1,
        updated_at = NOW()
    WHERE id = %s
    RETURNING id, category, payload, version, updated_at;
    """
    start_time = time.perf_counter()
    with get_db_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, (category, json.dumps(payload), record_id))
            row = cur.fetchone()
        conn.commit()
    duration_ms = (time.perf_counter() - start_time) * 1000.0

    if row:
        result = dict(row)
        if result.get("updated_at"):
            result["updated_at"] = result["updated_at"].isoformat()
        return result, duration_ms
    return None, duration_ms


def db_delete_record(record_id: int) -> Tuple[bool, float]:
    """Deletes a record from PostgreSQL."""
    query = "DELETE FROM records WHERE id = %s;"
    start_time = time.perf_counter()
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (record_id,))
            deleted = cur.rowcount > 0
        conn.commit()
    duration_ms = (time.perf_counter() - start_time) * 1000.0
    return deleted, duration_ms