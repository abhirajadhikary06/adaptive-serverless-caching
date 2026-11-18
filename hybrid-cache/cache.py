import time
import json
import logging
import threading
from collections import OrderedDict
from typing import Any, Tuple, Optional, Dict
import redis
from config import Config

logger = logging.getLogger(__name__)


class LRUMemoryCache:
    """Thread-safe bounded in-memory LRU cache with TTL expiration (L1)."""

    def __init__(self, capacity: int = 1000, default_ttl: int = 30):
        self.capacity = max(1, capacity)
        self.default_ttl = max(1, default_ttl)
        self._cache: OrderedDict[str, Tuple[Any, float]] = OrderedDict()
        self._lock = threading.Lock()

        self.hits_total = 0
        self.misses_total = 0
        self.evictions_total = 0
        self.expirations_total = 0

    def get(self, key: str) -> Tuple[Optional[Any], bool, float]:
        start = time.perf_counter()
        now = time.monotonic()
        with self._lock:
            if key not in self._cache:
                self.misses_total += 1
                duration_ms = (time.perf_counter() - start) * 1000.0
                return None, False, duration_ms

            val, expiry = self._cache[key]
            if now > expiry:
                del self._cache[key]
                self.expirations_total += 1
                self.misses_total += 1
                duration_ms = (time.perf_counter() - start) * 1000.0
                return None, False, duration_ms

            self._cache.move_to_end(key)
            self.hits_total += 1
            duration_ms = (time.perf_counter() - start) * 1000.0
            return val, True, duration_ms

    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> float:
        start = time.perf_counter()
        effective_ttl = ttl if ttl is not None else self.default_ttl
        expiry = time.monotonic() + effective_ttl

        with self._lock:
            if key in self._cache:
                self._cache[key] = (value, expiry)
                self._cache.move_to_end(key)
            else:
                if len(self._cache) >= self.capacity:
                    self._cache.popitem(last=False)
                    self.evictions_total += 1
                self._cache[key] = (value, expiry)

        return (time.perf_counter() - start) * 1000.0

    def delete(self, key: str) -> float:
        start = time.perf_counter()
        with self._lock:
            if key in self._cache:
                del self._cache[key]
        return (time.perf_counter() - start) * 1000.0

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            total = self.hits_total + self.misses_total
            ratio = round(self.hits_total / total, 4) if total > 0 else 0.0
            return {
                "capacity": self.capacity,
                "entry_count": len(self._cache),
                "ttl_seconds": self.default_ttl,
                "hits_total": self.hits_total,
                "misses_total": self.misses_total,
                "hit_ratio": ratio,
                "evictions_total": self.evictions_total,
                "expirations_total": self.expirations_total,
            }


class RedisCache:
    """Shared L2 Redis cache using native TLS TCP protocol."""

    def __init__(self, url: str, timeout: float = 2.0, default_ttl: int = 60):
        self.url = (url or "").strip()
        self.timeout = timeout
        self.default_ttl = default_ttl

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
        if not self.is_configured() or not self.client:
            return False, 0.0
        start = time.perf_counter()
        try:
            res = self.client.ping()
            return bool(res), (time.perf_counter() - start) * 1000.0
        except Exception as e:
            logger.warning(f"Redis ping failed: {e}")
            return False, (time.perf_counter() - start) * 1000.0

    def get(self, key: str) -> Tuple[Optional[Any], bool, float]:
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
            logger.warning(f"Redis L2 GET failed: {e}")
            return None, False, duration_ms

    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> Tuple[bool, float]:
        if not self.is_configured() or not self.client:
            return False, 0.0

        effective_ttl = ttl if ttl is not None else self.default_ttl
        serialized = json.dumps(value)
        start = time.perf_counter()
        try:
            ok = bool(self.client.set(key, serialized, ex=effective_ttl))
            return ok, (time.perf_counter() - start) * 1000.0
        except Exception as e:
            self.errors_total += 1
            logger.warning(f"Redis L2 SET failed: {e}")
            return False, (time.perf_counter() - start) * 1000.0

    def delete(self, key: str) -> Tuple[bool, float]:
        if not self.is_configured() or not self.client:
            return False, 0.0
        start = time.perf_counter()
        try:
            deleted = self.client.delete(key)
            return bool(deleted > 0), (time.perf_counter() - start) * 1000.0
        except Exception as e:
            self.errors_total += 1
            logger.warning(f"Redis L2 DEL failed: {e}")
            return False, (time.perf_counter() - start) * 1000.0

    def stats(self) -> Dict[str, Any]:
        total = self.hits_total + self.misses_total
        ratio = round(self.hits_total / total, 4) if total > 0 else 0.0
        return {
            "configured": self.is_configured(),
            "protocol": "tcp_tls",
            "ttl_seconds": self.default_ttl,
            "timeout_seconds": self.timeout,
            "hits_total": self.hits_total,
            "misses_total": self.misses_total,
            "hit_ratio": ratio,
            "errors_total": self.errors_total,
            "fallback_total": self.fallback_total,
        }


class HybridCacheManager:
    """
    Orchestrates the L1 Memory -> L2 Redis -> DB caching hierarchy.
    Adheres strictly to Section 5 (E4) and Section 7 metrics formulas.
    """

    def __init__(self):
        self.l1 = LRUMemoryCache(capacity=Config.L1_CAPACITY, default_ttl=Config.L1_TTL_SECONDS)
        self.l2 = RedisCache(url=Config.REDIS_URL, timeout=Config.REDIS_TIMEOUT_SECONDS, default_ttl=Config.L2_TTL_SECONDS)
        self.db_misses_total = 0

    def l1_key(self, record_id: int) -> str:
        return f"{Config.CACHE_NAMESPACE}:l1:record:{record_id}"

    def l2_key(self, record_id: int) -> str:
        return f"{Config.CACHE_NAMESPACE}:l2:record:{record_id}"

    def get(self, record_id: int) -> Tuple[Optional[Any], str, str, float, float]:
        """
        Hierarchical Lookup:
        1. Check L1 Memory
        2. On L1 Miss, check L2 Redis. If hit, back-populate L1.
        3. On L2 Miss, return None (caller will query PostgreSQL).
        Returns: (data, cache_status, cache_layer, l1_time_ms, l2_time_ms)
        """
        # Step 1: Check L1 Memory
        data, is_l1_hit, l1_ms = self.l1.get(self.l1_key(record_id))
        if is_l1_hit:
            return data, "HIT", "L1", l1_ms, 0.0

        # Step 2: Check L2 Redis
        data, is_l2_hit, l2_ms = self.l2.get(self.l2_key(record_id))
        if is_l2_hit:
            # Back-populate L1 from L2 hit
            pop_l1_ms = self.l1.set(self.l1_key(record_id), data)
            return data, "HIT", "L2", l1_ms + pop_l1_ms, l2_ms

        # Step 3: Complete cache miss (will query DB)
        return None, "MISS", "NONE", l1_ms, l2_ms

    def populate(self, record_id: int, data: Any) -> Tuple[float, float]:
        """Populates both L2 Redis and L1 Memory after DB read."""
        _, l2_ms = self.l2.set(self.l2_key(record_id), data)
        l1_ms = self.l1.set(self.l1_key(record_id), data)
        return l1_ms, l2_ms

    def invalidate(self, record_id: int) -> Tuple[float, float]:
        """Invalidates shared L2 Redis first, then local L1 Memory on write/update/delete."""
        _, l2_ms = self.l2.delete(self.l2_key(record_id))
        l1_ms = self.l1.delete(self.l1_key(record_id))
        return l1_ms, l2_ms

    def stats(self) -> Dict[str, Any]:
        l1_stats = self.l1.stats()
        l2_stats = self.l2.stats()
        total_cache_served = l1_stats["hits_total"] + l2_stats["hits_total"]
        total_reads = total_cache_served + self.db_misses_total
        overall_ratio = round(total_cache_served / total_reads, 4) if total_reads > 0 else 0.0

        return {
            "l1_memory": l1_stats,
            "l2_redis": l2_stats,
            "db_misses_total": self.db_misses_total,
            "overall_cache_served_ratio": overall_ratio,
        }