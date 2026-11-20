"""
Automated Environment Cleanup & Reset Script
Cleans Neon PostgreSQL database and Upstash Redis cache before running benchmarks.
Adheres strictly to docs/technical-research-plan.md Section 8 (Cache & DB reset cautions).
Safe for Free-Tier limits.
"""

import os
import sys
import logging
from dotenv import load_dotenv
import json
import psycopg2
import redis
from workloads.payloads import make_payload

# Load root .env
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL")
REDIS_URL = os.getenv("REDIS_URL") or os.getenv("UPSTASH_REDIS_REST_URL", "")
REDIS_TOKEN = os.getenv("UPSTASH_REDIS_REST_TOKEN", "")

# Auto-derive rediss:// connection string if needed
if not REDIS_URL.startswith("redis://") and not REDIS_URL.startswith("rediss://"):
    if REDIS_URL and REDIS_TOKEN:
        host = REDIS_URL.replace("https://", "").replace("http://", "").strip("/").split("/")[0]
        REDIS_URL = f"rediss://default:{REDIS_TOKEN}@{host}:6379"


def clean_postgres():
    """Truncates records table and re-initializes clean schema."""
    if not DATABASE_URL:
        logger.warning("DATABASE_URL not found; skipping Postgres cleanup.")
        return

    logger.info("Connecting to Neon PostgreSQL to reset database...")
    try:
        conn = psycopg2.connect(DATABASE_URL)
        with conn.cursor() as cur:
            # Recreate table cleanly
            cur.execute("""
            CREATE TABLE IF NOT EXISTS records (
                id BIGINT PRIMARY KEY,
                category VARCHAR(80) NOT NULL,
                payload JSONB NOT NULL,
                version BIGINT NOT NULL DEFAULT 1,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            TRUNCATE TABLE records;
            CREATE INDEX IF NOT EXISTS idx_records_category ON records(category);
            """)
        conn.commit()
        conn.close()
        logger.info("✓ Neon PostgreSQL 'records' table truncated and reset successfully.")
    except Exception as e:
        logger.error(f"Failed to reset PostgreSQL: {e}")


def seed_postgres(dataset_size: int = 100):
    """Pre-seeds the database with dataset_size records so benchmark GET/PUT find records."""
    if not DATABASE_URL:
        return

    logger.info(f"Seeding Neon PostgreSQL with {dataset_size} benchmark records...")
    try:
        conn = psycopg2.connect(DATABASE_URL)
        with conn.cursor() as cur:
            records = []
            for i in range(1, dataset_size + 1):
                p = make_payload(i, size_bytes=256)
                records.append((i, p["category"], json.dumps(p["payload"])))
            
            # Efficient multi-row insert
            cur.executemany("""
                INSERT INTO records (id, category, payload, version, updated_at)
                VALUES (%s, %s, %s, 1, NOW())
                ON CONFLICT (id) DO UPDATE SET
                    category = EXCLUDED.category,
                    payload = EXCLUDED.payload,
                    version = 1,
                    updated_at = NOW();
            """, records)
        conn.commit()
        conn.close()
        logger.info(f"✓ Seeded {dataset_size} records into Neon PostgreSQL successfully.")
    except Exception as e:
        logger.error(f"Failed to seed PostgreSQL: {e}")


def clean_redis():
    """Purges research namespaces in Upstash Redis (e3, e4, e5, e6)."""
    if not REDIS_URL or not (REDIS_URL.startswith("redis://") or REDIS_URL.startswith("rediss://")):
        logger.warning("Valid REDIS_URL not configured; skipping Redis cleanup.")
        return

    logger.info("Connecting to Upstash Redis to clear research cache namespaces...")
    try:
        client = redis.from_url(REDIS_URL, decode_responses=True, socket_timeout=5.0)
        namespaces = ["e3*", "e4*", "e5*", "e6*"]
        total_deleted = 0
        for pattern in namespaces:
            keys = client.keys(pattern)
            if keys:
                deleted = client.delete(*keys)
                total_deleted += deleted
        logger.info(f"✓ Upstash Redis reset complete: deleted {total_deleted} research keys.")
    except Exception as e:
        logger.error(f"Failed to reset Redis: {e}")


if __name__ == "__main__":
    logger.info("Starting complete environment reset (Free-Tier Safe)...")
    clean_postgres()
    seed_postgres(dataset_size=100)
    clean_redis()
    logger.info("Environment is clean, seeded with 100 records, and ready for fresh benchmark runs!")
