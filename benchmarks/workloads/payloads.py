"""
Synthetic Payload Generator (Section 6.1 & Section 4.2)
Generates controlled, byte-accurate JSON payloads for benchmarking.
Optimized for Free-Tier memory & network limits.
"""

import json
from typing import Dict, Any


def make_payload(record_id: int, size_bytes: int = 256, category: str = "benchmark") -> Dict[str, Any]:
    """
    Creates a synthetic JSON payload strictly matching the API contract:
    {"id": record_id, "category": category, "payload": {"name": ..., "data": "xxx..."}}
    """
    inner_payload = {
        "name": f"synthetic_record_{record_id}",
        "data": ""
    }
    base = {
        "id": record_id,
        "category": category,
        "payload": inner_payload
    }
    # Calculate padding needed to hit target serialized byte size
    base_size = len(json.dumps(base).encode("utf-8"))
    padding_needed = max(0, size_bytes - base_size)
    inner_payload["data"] = "x" * padding_needed
    return base
