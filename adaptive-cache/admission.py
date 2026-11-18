import time
import threading
from collections import OrderedDict
from typing import Tuple, Dict, Any


class FrequencyTracker:
    """
    Thread-safe bounded frequency tracker with time-window decay.
    Adheres to Section 5 (E5 Adaptive admission).
    """

    def __init__(self, threshold: int = 2, window_seconds: int = 60, max_keys: int = 10000):
        self.threshold = max(1, threshold)
        self.window_seconds = max(1, window_seconds)
        self.max_keys = max(100, max_keys)

        # Stores: key -> list of float timestamps
        self._history: OrderedDict[str, list] = OrderedDict()
        self._lock = threading.Lock()

        # Operational metrics
        self.admissions_total = 0
        self.rejections_total = 0

    def record_access(self, key: str) -> Tuple[int, float]:
        """
        Records an access for a key and purges timestamps older than window.
        Returns: (current_frequency, duration_ms)
        """
        start = time.perf_counter()
        now = time.monotonic()
        cutoff = now - self.window_seconds

        with self._lock:
            if key not in self._history:
                if len(self._history) >= self.max_keys:
                    self._history.popitem(last=False)
                self._history[key] = [now]
            else:
                timestamps = [t for t in self._history[key] if t > cutoff]
                timestamps.append(now)
                self._history[key] = timestamps
                self._history.move_to_end(key)

            freq = len(self._history[key])

        duration_ms = (time.perf_counter() - start) * 1000.0
        return freq, duration_ms

    def should_admit(self, key: str) -> Tuple[bool, int]:
        """
        Evaluates whether an item crosses the admission threshold.
        Returns: (is_admitted, frequency)
        """
        now = time.monotonic()
        cutoff = now - self.window_seconds

        with self._lock:
            if key not in self._history:
                self.rejections_total += 1
                return False, 0

            timestamps = [t for t in self._history[key] if t > cutoff]
            self._history[key] = timestamps
            freq = len(timestamps)

            admit = freq >= self.threshold
            if admit:
                self.admissions_total += 1
            else:
                self.rejections_total += 1

            return admit, freq

    def reset_key(self, key: str) -> None:
        """Removes key history upon write or invalidation."""
        with self._lock:
            if key in self._history:
                del self._history[key]

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            total_decisions = self.admissions_total + self.rejections_total
            rejection_ratio = round(self.rejections_total / total_decisions, 4) if total_decisions > 0 else 0.0
            return {
                "threshold": self.threshold,
                "window_seconds": self.window_seconds,
                "tracked_keys": len(self._history),
                "admissions_total": self.admissions_total,
                "rejections_total": self.rejections_total,
                "rejection_ratio": rejection_ratio,
            }