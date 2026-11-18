import time
import json
import logging
import threading
from collections import OrderedDict
from typing import Any, Tuple, Optional, Dict
import redis
from admission import FrequencyTracker
from ttl_policy import AdaptiveTTLPolicy
from invalidation import CrossInstanceInvalidator
from config import Config

logger = logging.getLogger(__name__)


class LRUMemoryCache:
    """Bounded process-local LRU memory cache with TTL (L1)."""

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
                return None, False, (time.perf_counter() - start) * 1000.0

            val, expiry = self._cache[key]
            if now > expiry:
                del self._cache[key]
                self.expirations_total += 1
                self.misses_total += 1
                return None, False, (time.perf_counter() - start) * 1000.0

            self._cache.move_to_end(key)
            self.hits_total += 1
            return val, True, (time.perf_counter() - start) * 1000.0

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
            "hits_total": self.hits_total,
            "misses_total": self.misses_total,
            "hit_ratio": ratio,
            "errors_total": self.errors_total,
            "fallback_total": self.fallback_total,
        }


class AdaptiveInvalidatingCacheManager:
    """
    E6 Cache Manager:
    Combines Frequency Admission Tracking + Adaptive Dynamic TTL +
    Cross-Instance Version Invalidation across L1 and L2.
    """

    def __init__(self):
        self.l1 = LRUMemoryCache(capacity=Config.L1_CAPACITY, default_ttl=Config.L1_TTL_SECONDS)
        self.l2 = RedisCache(url=Config.REDIS_URL, timeout=Config.REDIS_TIMEOUT_SECONDS, default_ttl=Config.L2_TTL_SECONDS)
        self.admission = FrequencyTracker(
            threshold=Config.ADMISSION_THRESHOLD,
            window_seconds=Config.ADMISSION_WINDOW_SECONDS,
        )
        self.ttl_policy = AdaptiveTTLPolicy(
            min_ttl=Config.ADAPTIVE_TTL_MIN_SECONDS,
            max_ttl=Config.ADAPTIVE_TTL_MAX_SECONDS,
        )
        self.invalidator = CrossInstanceInvalidator(
            redis_client=self.l2.client,
            namespace=Config.CACHE_NAMESPACE,
        )
        self.db_misses_total = 0

    def l1_key(self, record_id: int) -> str:
        return f"{Config.CACHE_NAMESPACE}:l1:record:{record_id}"

    def l2_key(self, record_id: int) -> str:
        return f"{Config.CACHE_NAMESPACE}:l2:record:{record_id}"

    def get(self, record_id: int) -> Tuple[Optional[Any], str, str, str, float, float, float, float]:
        """
        Hierarchical Read with Cross-Instance Stale Check.
        Returns: (data, status, layer, version_status, l1_ms, l2_ms, policy_ms, inv_ms)
        """
        key_id = str(record_id)
        _, policy_ms = self.admission.record_access(key_id)

        # 1. Check L1 Memory
        data, is_l1_hit, l1_ms = self.l1.get(self.l1_key(record_id))
        if is_l1_hit:
            # Cross-instance version check
            cached_version = data.get("version", 1) if isinstance(data, dict) else 1
            stale, inv_ms = self.invalidator.is_stale(record_id, cached_version)

            if stale:
                # Evict stale entry immediately!
                self.l1.delete(self.l1_key(record_id))
                return None, "STALE_MISS", "NONE", "STALE_EVICTED", l1_ms, 0.0, policy_ms, inv_ms

            return data, "HIT", "L1", "VALID", l1_ms, 0.0, policy_ms, inv_ms

        # 2. Check L2 Redis
        data, is_l2_hit, l2_ms = self.l2.get(self.l2_key(record_id))
        if is_l2_hit:
            # Back-populate L1
            pop_l1_ms = self.l1.set(self.l1_key(record_id), data, ttl=Config.L1_TTL_SECONDS)
            return data, "HIT", "L2", "VALID", l1_ms + pop_l1_ms, l2_ms, policy_ms, 0.0

        # 3. Complete Cache Miss
        return None, "MISS", "NONE", "N/A", l1_ms, l2_ms, policy_ms, 0.0

    def evaluate_and_populate(
        self, record_id: int, data: Any
    ) -> Tuple[bool, int, float, float, float]:
        """Evaluates admission and populates L2 and L1 with dynamic TTL."""
        pol_start = time.perf_counter()
        key_id = str(record_id)
        admit, freq = self.admission.should_admit(key_id)

        if not admit:
            return False, 0, 0.0, 0.0, (time.perf_counter() - pol_start) * 1000.0

        computed_ttl = self.ttl_policy.compute_ttl(freq)
        policy_ms = (time.perf_counter() - pol_start) * 1000.0

        _, l2_ms = self.l2.set(self.l2_key(record_id), data, ttl=computed_ttl)
        l1_ms = self.l1.set(self.l1_key(record_id), data, ttl=computed_ttl)

        return True, computed_ttl, l1_ms, l2_ms, policy_ms

    def broadcast_invalidation(self, record_id: int, new_version: int) -> Tuple[float, float, float]:
        """
        Full write invalidation sequence (Section 5 E6):
        1. Broadcast authoritative version to Redis marker + PubSub.
        2. Invalidate L2 Redis payload.
        3. Invalidate local L1 Memory.
        4. Reset frequency history.
        """
        _, inv_ms = self.invalidator.record_new_version(record_id, new_version)
        _, l2_ms = self.l2.delete(self.l2_key(record_id))
        l1_ms = self.l1.delete(self.l1_key(record_id))
        self.admission.reset_key(str(record_id))
        return l1_ms, l2_ms, inv_ms

    def stats(self) -> Dict[str, Any]:
        l1_stats = self.l1.stats()
        l2_stats = self.l2.stats()
        total_cache_served = l1_stats["hits_total"] + l2_stats["hits_total"]
        total_reads = total_cache_served + self.db_misses_total
        overall_ratio = round(total_cache_served / total_reads, 4) if total_reads > 0 else 0.0

        return {
            "l1_memory": l1_stats,
            "l2_redis": l2_stats,
            "admission_policy": self.admission.stats(),
            "ttl_policy": self.ttl_policy.stats(),
            "invalidation": self.invalidator.stats(),
            "db_misses_total": self.db_misses_total,
            "overall_cache_served_ratio": overall_ratio,
        }