"""
Workload & Access Pattern Generator (Section 6.1 & 6.3)
Generates deterministic request streams across Uniform, Zipfian, and Bursty distributions.
"""

import random
import numpy as np
from typing import List, Dict, Any
from .payloads import make_payload


class WorkloadGenerator:
    def __init__(
        self,
        dataset_size: int = 1000,
        payload_bytes: int = 256,
        read_ratio: float = 0.95,
        access_pattern: str = "zipfian",
        zipf_alpha: float = 1.2,
        seed: int = 42,
    ):
        self.dataset_size = max(10, dataset_size)
        self.payload_bytes = payload_bytes
        self.read_ratio = read_ratio
        self.access_pattern = access_pattern.lower()
        self.zipf_alpha = zipf_alpha
        self.seed = seed

        # Seed random generators for strict repeatability
        random.seed(self.seed)
        np.random.seed(self.seed)

    def _get_key_sequence(self, num_requests: int) -> List[int]:
        """Generates key IDs according to the requested distribution."""
        if self.access_pattern == "uniform":
            return [random.randint(1, self.dataset_size) for _ in range(num_requests)]

        elif self.access_pattern == "zipfian":
            # Sample from Zipf distribution, modulus-wrap within dataset bounds
            raw_keys = np.random.zipf(a=self.zipf_alpha, size=num_requests)
            return [int((k - 1) % self.dataset_size) + 1 for k in raw_keys]

        elif self.access_pattern == "bursty":
            keys = []
            hot_window_size = max(5, self.dataset_size // 50)
            burst_length = 50
            current_offset = 1

            for i in range(num_requests):
                if i > 0 and i % burst_length == 0:
                    current_offset = (current_offset + hot_window_size) % (self.dataset_size - hot_window_size) + 1
                keys.append(current_offset + random.randint(0, hot_window_size - 1))
            return keys

        else:
            raise ValueError(f"Unknown access pattern: {self.access_pattern}")

    def generate(self, num_requests: int) -> List[Dict[str, Any]]:
        """Produces a deterministic list of HTTP operations."""
        key_sequence = self._get_key_sequence(num_requests)
        workload = []

        for record_id in key_sequence:
            rnd = random.random()
            if rnd < self.read_ratio:
                action = "GET"
            elif rnd < self.read_ratio + (1.0 - self.read_ratio) * 0.70:
                action = "PUT"
            else:
                action = "DELETE"

            item = {
                "action": action,
                "record_id": record_id,
                "payload": make_payload(record_id, self.payload_bytes) if action in ["POST", "PUT"] else None,
            }
            workload.append(item)

        return workload
