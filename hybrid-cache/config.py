import os
from dotenv import load_dotenv

# Load root .env if running locally
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))


class Config:
    CONFIG_ID = "E4"
    CONFIG_NAME = "hybrid-cache"
    APP_ENV = os.getenv("APP_ENV", "development")
    HOST = os.getenv("HOST", "0.0.0.0")
    PORT = int(os.getenv("PORT", 5000))

    # PostgreSQL Database URL
    DATABASE_URL = os.getenv("DATABASE_URL")
    DB_POOL_MIN = int(os.getenv("DB_POOL_MIN", 1))
    DB_POOL_MAX = int(os.getenv("DB_POOL_MAX", 10))

    # L1 Process-Local Memory Cache Settings
    L1_CAPACITY = int(os.getenv("L1_CAPACITY", 1000))
    L1_TTL_SECONDS = int(os.getenv("L1_TTL_SECONDS", 30))

    # L2 Shared Redis Cache Settings
    REDIS_URL = os.getenv("REDIS_URL") or os.getenv("UPSTASH_REDIS_REST_URL", "")
    REDIS_TIMEOUT_SECONDS = float(os.getenv("REDIS_TIMEOUT_SECONDS", 2.0))
    L2_TTL_SECONDS = int(os.getenv("L2_TTL_SECONDS", 60))

    # Key Namespace
    CACHE_NAMESPACE = os.getenv("CACHE_NAMESPACE", "e4")