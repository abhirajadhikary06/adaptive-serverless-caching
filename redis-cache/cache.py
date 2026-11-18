import time
import json
import logging
from typing import Any, Tuple, Optional, Dict
import redis

logger = logging.getLogger(__name__)


class UpstashRedisCache:
    """
    Shared L2 Redis cache using native Redis TCP protocol with TLS (rediss://).
    Adheres strictly to Section 5 (E3): graceful fallback, separate latency measurement,
    and timeout isolation.
    """

    def __init__(
        self,
        url: str,
        timeout: float = 2.0,
        default_ttl: int = 60,
    ):
        self.url = (url or "").strip()
        self.timeout = timeout
        self.default_ttl = default_ttl

        # Operational metrics counters
        self.hits_total = 0
        self.misses_total = 0
        self.errors_total = 0
        self.fallback_total = 0

        self.client: Optional[redis.Redis] = None
        if self.is_configured():
            try:
                self.client = redis.from_url(
                    self.url,
                    socket_timeout=self.timeout,
                    socket_connect_timeout=self.timeout,
                    decode_responses=True,
                )
            except Exception as e:
                logger.error(f"Failed to initialize Redis TCP client: {e}")

    def is_configured(self) -> bool:
        return bool(self.url and (self.url.startswith("redis://") or self.url.startswith("rediss://")))

    def ping(self) -> Tuple[bool, float]:
        """Checks Redis health via native PING."""
        if not self.is_configured() or not self.client:
            return False, 0.0

        start = time.perf_counter()
        try:
            res = self.client.ping()
            duration_ms = (time.perf_counter() - start) * 1000.0
            return bool(res), duration_ms
        except Exception as e:
            duration_ms = (time.perf_counter() - start) * 1000.0
            logger.warning(f"Redis TCP ping failed: {e}")
            return False, duration_ms

    def get(self, key: str) -> Tuple[Optional[Any], bool, float]:
        """
        Retrieves an item from Redis via TCP.
        Returns: (deserialized_value, is_hit, duration_ms)
        Gracefully falls back to None on timeout or connection error.
        """
        if not self.is_configured() or not self.client:
            self.fallback_total += 1
            return None, False, 0.0

        start = time.perf_counter()
        try:
            raw_val = self.client.get(key)
            duration_ms = (time.perf_counter() - start) * 1000.0

            if raw_val is not None:
                self.hits_total += 1
                try:
                    return json.loads(raw_val), True, duration_ms
                except (json.JSONDecodeError, TypeError):
                    return raw_val, True, duration_ms

            self.misses_total += 1
            return None, False, duration_ms

        except Exception as e:
            duration_ms = (time.perf_counter() - start) * 1000.0
            self.errors_total += 1
            self.fallback_total += 1
            logger.warning(f"Redis GET failed (falling back to DB): {e}")
            return None, False, duration_ms

    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> Tuple[bool, float]:
        """Sets a key in Redis with TTL via TCP."""
        if not self.is_configured() or not self.client:
            return False, 0.0

        effective_ttl = ttl if ttl is not None else self.default_ttl
        serialized = json.dumps(value)
        start = time.perf_counter()

        try:
            ok = bool(self.client.set(key, serialized, ex=effective_ttl))
            duration_ms = (time.perf_counter() - start) * 1000.0
            return ok, duration_ms
        except Exception as e:
            duration_ms = (time.perf_counter() - start) * 1000.0
            self.errors_total += 1
            logger.warning(f"Redis SET failed: {e}")
            return False, duration_ms

    def delete(self, key: str) -> Tuple[bool, float]:
        """Evicts a key from Redis via TCP."""
        if not self.is_configured() or not self.client:
            return False, 0.0

        start = time.perf_counter()
        try:
            deleted = self.client.delete(key)
            duration_ms = (time.perf_counter() - start) * 1000.0
            return bool(deleted > 0), duration_ms
        except Exception as e:
            duration_ms = (time.perf_counter() - start) * 1000.0
            self.errors_total += 1
            logger.warning(f"Redis DEL failed: {e}")
            return False, duration_ms

    def stats(self) -> Dict[str, Any]:
        """Returns L2 Redis operational stats."""
        total_lookups = self.hits_total + self.misses_total
        hit_ratio = round(self.hits_total / total_lookups, 4) if total_lookups > 0 else 0.0
        return {
            "configured": self.is_configured(),
            "protocol": "tcp_tls",
            "ttl_seconds": self.default_ttl,
            "timeout_seconds": self.timeout,
            "hits_total": self.hits_total,
            "misses_total": self.misses_total,
            "hit_ratio": hit_ratio,
            "errors_total": self.errors_total,
            "fallback_total": self.fallback_total,
        }
