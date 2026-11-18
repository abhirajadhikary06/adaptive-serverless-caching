from typing import Dict, Any


class AdaptiveTTLPolicy:
    """Deterministic Adaptive TTL Policy."""

    def __init__(self, min_ttl: int = 5, max_ttl: int = 300, step_seconds: int = 30):
        self.min_ttl = max(1, min_ttl)
        self.max_ttl = max(self.min_ttl, max_ttl)
        self.step_seconds = max(1, step_seconds)

        self.calculations_total = 0
        self.sum_ttl_seconds = 0

    def compute_ttl(self, frequency: int) -> int:
        self.calculations_total += 1
        if frequency <= 1:
            computed = self.min_ttl
        else:
            computed = self.min_ttl + (frequency - 1) * self.step_seconds

        final_ttl = min(self.max_ttl, max(self.min_ttl, computed))
        self.sum_ttl_seconds += final_ttl
        return final_ttl

    def stats(self) -> Dict[str, Any]:
        avg_ttl = (
            round(self.sum_ttl_seconds / self.calculations_total, 2)
            if self.calculations_total > 0
            else self.min_ttl
        )
        return {
            "min_ttl_seconds": self.min_ttl,
            "max_ttl_seconds": self.max_ttl,
            "step_seconds": self.step_seconds,
            "calculations_total": self.calculations_total,
            "avg_computed_ttl_seconds": avg_ttl,
        }