import time
import logging
from typing import Optional, Tuple, Dict, Any
import redis

logger = logging.getLogger(__name__)


class CrossInstanceInvalidator:
    """
    Manages Cross-Instance Invalidation & Version Tracking.
    Adheres strictly to Section 5 (E6): Version-based validation protocol
    with shared Redis version markers and optional pub/sub broadcast.
    """

    def __init__(self, redis_client: Optional[redis.Redis], namespace: str = "e6"):
        self.client = redis_client
        self.namespace = namespace

        # Metrics
        self.invalidation_broadcasts = 0
        self.stale_detections = 0
        self.version_checks = 0

    def version_key(self, record_id: int) -> str:
        return f"{self.namespace}:version:record:{record_id}"

    def pubsub_channel(self) -> str:
        return f"{self.namespace}:invalidations"

    def record_new_version(self, record_id: int, new_version: int) -> Tuple[bool, float]:
        """
        Broadcasts invalidation:
        1. Sets the shared authoritative version in Redis.
        2. Publishes best-effort notification on Pub/Sub channel.
        """
        if not self.client:
            return False, 0.0

        start = time.perf_counter()
        try:
            pipe = self.client.pipeline()
            # Set version marker with 24-hour retention
            pipe.set(self.version_key(record_id), str(new_version), ex=86400)
            # Best-effort Pub/Sub publish
            pipe.publish(self.pubsub_channel(), f"{record_id}:{new_version}")
            pipe.execute()

            self.invalidation_broadcasts += 1
            duration_ms = (time.perf_counter() - start) * 1000.0
            return True, duration_ms
        except Exception as e:
            duration_ms = (time.perf_counter() - start) * 1000.0
            logger.warning(f"Failed to broadcast invalidation: {e}")
            return False, duration_ms

    def get_authoritative_version(self, record_id: int) -> Tuple[Optional[int], float]:
        """Queries the shared Redis version marker to detect cross-instance updates."""
        if not self.client:
            return None, 0.0

        start = time.perf_counter()
        self.version_checks += 1
        try:
            val = self.client.get(self.version_key(record_id))
            duration_ms = (time.perf_counter() - start) * 1000.0
            if val is not None:
                return int(val), duration_ms
            return None, duration_ms
        except Exception as e:
            duration_ms = (time.perf_counter() - start) * 1000.0
            logger.warning(f"Failed to fetch authoritative version: {e}")
            return None, duration_ms

    def is_stale(self, record_id: int, cached_version: int) -> Tuple[bool, float]:
        """
        Checks if a cached item is stale by comparing cached version with Redis.
        Returns: (is_stale, check_duration_ms)
        """
        auth_version, duration_ms = self.get_authoritative_version(record_id)
        if auth_version is not None and auth_version > cached_version:
            self.stale_detections += 1
            return True, duration_ms
        return False, duration_ms

    def stats(self) -> Dict[str, Any]:
        return {
            "version_checks_total": self.version_checks,
            "invalidation_broadcasts_total": self.invalidation_broadcasts,
            "stale_detections_total": self.stale_detections,
        }