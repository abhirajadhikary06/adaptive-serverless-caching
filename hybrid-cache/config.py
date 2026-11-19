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

    # L2 Shared Redis Cache Settings (supports REDIS_URL or auto-derivation from UPSTASH_REDIS_REST_*)
    _raw_redis = os.getenv("REDIS_URL", "").strip()
    _rest_url = os.getenv("UPSTASH_REDIS_REST_URL", "").strip()
    _rest_token = os.getenv("UPSTASH_REDIS_REST_TOKEN", "").strip()

    if _raw_redis:
        REDIS_URL = _raw_redis
    elif _rest_url.startswith("rediss://") or _rest_url.startswith("redis://"):
        REDIS_URL = _rest_url
    elif _rest_url and _rest_token:
        _host = _rest_url.replace("https://", "").replace("http://", "").strip("/").split("/")[0]
        REDIS_URL = f"rediss://default:{_rest_token}@{_host}:6379"
    else:
        REDIS_URL = _rest_url

    REDIS_TIMEOUT_SECONDS = float(os.getenv("REDIS_TIMEOUT_SECONDS", 2.0))
    L2_TTL_SECONDS = int(os.getenv("L2_TTL_SECONDS", 60))

    # Key Namespace
    CACHE_NAMESPACE = os.getenv("CACHE_NAMESPACE", "e4")