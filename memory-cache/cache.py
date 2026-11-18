import time
import threading
from collections import OrderedDict
from typing import Any, Tuple, Optional, Dict


class LRUMemoryCache:
    """
    Thread-safe bounded in-memory LRU cache with TTL expiration.
    Adheres to Section 5 (E2) and Section 7 metrics tracking.
    """

    def __init__(self, capacity: int = 1000, default_ttl: int = 30):
        self.capacity = max(1, capacity)
        self.default_ttl = max(1, default_ttl)
        self._cache: OrderedDict[str, Tuple[Any, float]] = OrderedDict()
        self._lock = threading.Lock()

        # Operational metrics counters
        self.hits_total = 0
        self.misses_total = 0
        self.evictions_total = 0
        self.expirations_total = 0

    def get(self, key: str) -> Tuple[Optional[Any], bool]:
        """
        Retrieves an item from cache.
        Returns (value, is_hit).
        """
        now = time.monotonic()
        with self._lock:
            if key not in self._cache:
                self.misses_total += 1
                return None, False

            val, expiry = self._cache[key]

            # Check expiration
            if now > expiry:
                del self._cache[key]
                self.expirations_total += 1
                self.misses_total += 1
                return None, False

            # Mark recently used
            self._cache.move_to_end(key)
            self.hits_total += 1
            return val, True

    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        """Stores an item with TTL and performs LRU eviction if full."""
        effective_ttl = ttl if ttl is not None else self.default_ttl
        expiry = time.monotonic() + effective_ttl

        with self._lock:
            if key in self._cache:
                self._cache[key] = (value, expiry)
                self._cache.move_to_end(key)
                return

            # Check capacity and evict oldest if needed
            if len(self._cache) >= self.capacity:
                self._cache.popitem(last=False)
                self.evictions_total += 1

            self._cache[key] = (value, expiry)

    def delete(self, key: str) -> bool:
        """Invalidates an item from local memory."""
        with self._lock:
            if key in self._cache:
                del self._cache[key]
                return True
            return False

    def clear(self) -> None:
        """Clears all entries and resets statistics."""
        with self._lock:
            self._cache.clear()
            self.hits_total = 0
            self.misses_total = 0
            self.evictions_total = 0
            self.expirations_total = 0

    def stats(self) -> Dict[str, Any]:
        """Returns L1 cache diagnostic counters."""
        with self._lock:
            total_lookups = self.hits_total + self.misses_total
            hit_ratio = round(self.hits_total / total_lookups, 4) if total_lookups > 0 else 0.0
            return {
                "capacity": self.capacity,
                "entry_count": len(self._cache),
                "ttl_seconds": self.default_ttl,
                "hits_total": self.hits_total,
                "misses_total": self.misses_total,
                "hit_ratio": hit_ratio,
                "evictions_total": self.evictions_total,
                "expirations_total": self.expirations_total,
            }