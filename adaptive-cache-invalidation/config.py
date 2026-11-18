import os
from dotenv import load_dotenv

# Load root .env if running locally
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))


class Config:
    CONFIG_ID = "E6"
    CONFIG_NAME = "adaptive-cache-invalidation"
    APP_ENV = os.getenv("APP_ENV", "development")
    HOST = os.getenv("HOST", "0.0.0.0")
    PORT = int(os.getenv("PORT", 5000))

    # PostgreSQL Database URL
    DATABASE_URL = os.getenv("DATABASE_URL")
    DB_POOL_MIN = int(os.getenv("DB_POOL_MIN", 1))
    DB_POOL_MAX = int(os.getenv("DB_POOL_MAX", 10))

    # L1 & L2 Settings
    L1_CAPACITY = int(os.getenv("L1_CAPACITY", 1000))
    L1_TTL_SECONDS = int(os.getenv("L1_TTL_SECONDS", 30))
    REDIS_URL = os.getenv("REDIS_URL") or os.getenv("UPSTASH_REDIS_REST_URL", "")
    REDIS_TIMEOUT_SECONDS = float(os.getenv("REDIS_TIMEOUT_SECONDS", 2.0))
    L2_TTL_SECONDS = int(os.getenv("L2_TTL_SECONDS", 60))
    CACHE_NAMESPACE = os.getenv("CACHE_NAMESPACE", "e6")

    # Adaptive Policy Parameters
    ADMISSION_THRESHOLD = int(os.getenv("ADMISSION_THRESHOLD", 2))
    ADMISSION_WINDOW_SECONDS = int(os.getenv("ADMISSION_WINDOW_SECONDS", 60))
    ADAPTIVE_TTL_MIN_SECONDS = int(os.getenv("ADAPTIVE_TTL_MIN_SECONDS", 5))
    ADAPTIVE_TTL_MAX_SECONDS = int(os.getenv("ADAPTIVE_TTL_MAX_SECONDS", 300))

    # Invalidation Strategy (Section 5 E6: version, pubsub, or hybrid)
    INVALIDATION_STRATEGY = os.getenv("INVALIDATION_STRATEGY", "version")