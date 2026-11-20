"""
Core Benchmark Runner Engine (Section 6.4, 8, & 9)
Executes concurrent load against local or Vercel serverless endpoints.
Records fine-grained latency and research headers into raw CSV files.
"""

import os
import sys
import time
import argparse
import logging
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import pandas as pd
import requests

# Ensure parent directory is in python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from workloads.generator import WorkloadGenerator

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def send_single_request(session: requests.Session, base_url: str, item: dict, run_id: str, tier: str) -> dict:
    """Executes a single HTTP operation and extracts research diagnostic headers."""
    record_id = item["record_id"]
    action = item["action"]
    url = f"{base_url.rstrip('/')}/records/{record_id}"

    start_time = time.perf_counter()
    status_code = 0
    error_type = ""
    headers = {}

    try:
        if action == "GET":
            resp = session.get(url, timeout=10.0)
        elif action == "PUT":
            payload_data = item.get("payload") or {}
            body = {
                "category": payload_data.get("category", "benchmark"),
                "payload": payload_data.get("payload", {"name": f"updated_{record_id}"})
            }
            resp = session.put(url, json=body, timeout=10.0)
        elif action == "POST":
            post_url = f"{base_url.rstrip('/')}/records"
            payload_data = item.get("payload") or {}
            body = {
                "id": record_id,
                "category": payload_data.get("category", "benchmark"),
                "payload": payload_data.get("payload", {"name": f"record_{record_id}"})
            }
            resp = session.post(post_url, json=body, timeout=10.0)
        elif action == "DELETE":
            resp = session.delete(url, timeout=10.0)
        else:
            resp = session.get(url, timeout=10.0)

        total_latency_ms = (time.perf_counter() - start_time) * 1000.0
        status_code = resp.status_code
        headers = resp.headers

        if status_code >= 400 and status_code != 404 and status_code != 409:
            error_type = f"HTTP_{status_code}"

    except requests.exceptions.Timeout:
        total_latency_ms = (time.perf_counter() - start_time) * 1000.0
        status_code = 504
        error_type = "Timeout"
    except Exception as e:
        total_latency_ms = (time.perf_counter() - start_time) * 1000.0
        status_code = 500
        error_type = str(e)[:50]

    # Parse standard research diagnostic headers
    cache_status = headers.get("X-Cache-Status", "N/A")
    cache_layer = headers.get("X-Cache-Layer", "N/A")
    db_ms = float(headers.get("X-Database-Time-Ms", 0.0) or 0.0)
    l1_ms = float(headers.get("X-L1-Time-Ms", headers.get("X-Cache-Time-Ms", 0.0)) or 0.0)
    l2_ms = float(headers.get("X-L2-Time-Ms", headers.get("X-Redis-Time-Ms", 0.0)) or 0.0)
    policy_ms = float(headers.get("X-Policy-Time-Ms", 0.0) or 0.0)
    adaptive_ttl = int(headers.get("X-Adaptive-TTL", 0) or 0)
    version_status = headers.get("X-Version-Status", "N/A")
    admission_status = headers.get("X-Admission-Status", "N/A")
    req_id = headers.get("X-Request-ID", "N/A")

    return {
        "timestamp": datetime.utcnow().isoformat(),
        "run_id": run_id,
        "config_id": tier,
        "operation": action,
        "record_id": record_id,
        "status_code": status_code,
        "total_latency_ms": round(total_latency_ms, 2),
        "db_latency_ms": round(db_ms, 2),
        "l1_latency_ms": round(l1_ms, 2),
        "l2_latency_ms": round(l2_ms, 2),
        "policy_latency_ms": round(policy_ms, 2),
        "cache_layer": cache_layer,
        "cache_status": cache_status,
        "cache_hit": 1 if cache_status == "HIT" else 0,
        "admission_status": admission_status,
        "adaptive_ttl": adaptive_ttl,
        "version_status": version_status,
        "request_id": req_id,
        "error_type": error_type,
    }


def execute_benchmark(
    base_url: str,
    tier: str,
    num_requests: int = 200,
    concurrency: int = 5,
    warmup_requests: int = 20,
    dataset_size: int = 100,
    payload_bytes: int = 256,
    access_pattern: str = "zipfian",
    read_ratio: float = 0.95,
    run_id: str = "run_1",
    output_dir: str = "benchmarks/results",
) -> str:
    """Executes warmup followed by steady-state load test, saving results to CSV."""
    os.makedirs(output_dir, exist_ok=True)
    logger.info(f"[{tier}] Initiating benchmark against {base_url}")
    logger.info(f"[{tier}] Config: {num_requests} requests, concurrency={concurrency}, pattern={access_pattern}, payload={payload_bytes}B")

    generator = WorkloadGenerator(
        dataset_size=dataset_size,
        payload_bytes=payload_bytes,
        read_ratio=read_ratio,
        access_pattern=access_pattern,
    )

    # 1. Warm-up Phase (discarded from final measurements per Section 8)
    if warmup_requests > 0:
        logger.info(f"[{tier}] Sending {warmup_requests} warmup requests...")
        warmup_workload = generator.generate(warmup_requests)
        with requests.Session() as session:
            with ThreadPoolExecutor(max_workers=min(concurrency, 4)) as executor:
                for item in warmup_workload:
                    executor.submit(send_single_request, session, base_url, item, "warmup", tier)
        logger.info(f"[{tier}] Warmup complete.")

    # 2. Steady-State Measurement Phase
    logger.info(f"[{tier}] Running steady-state workload ({num_requests} requests)...")
    workload = generator.generate(num_requests)
    results = []

    start_bench = time.perf_counter()
    with requests.Session() as session:
        adapter = requests.adapters.HTTPAdapter(pool_connections=concurrency, pool_maxsize=concurrency)
        session.mount("https://", adapter)
        session.mount("http://", adapter)

        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = [executor.submit(send_single_request, session, base_url, item, run_id, tier) for item in workload]
            for future in as_completed(futures):
                results.append(future.result())

    total_bench_duration = time.perf_counter() - start_bench
    throughput_rps = round(len(results) / total_bench_duration, 2)
    logger.info(f"[{tier}] Benchmark finished in {total_bench_duration:.2f}s ({throughput_rps} req/sec).")

    # 3. Save Raw CSV Output
    df = pd.DataFrame(results)
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    out_filename = f"{tier.lower()}_{run_id}_{timestamp}.csv"
    out_path = os.path.join(output_dir, out_filename)
    df.to_csv(out_path, index=False)
    logger.info(f"[{tier}] Raw measurements saved to: {out_path}")

    return out_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run research benchmark on serverless endpoint")
    parser.add_argument("--url", required=True, help="Target Vercel URL")
    parser.add_argument("--tier", required=True, help="Configuration Tier (E1, E2, E3, E4, E5, E6)")
    parser.add_argument("--requests", type=int, default=200, help="Number of steady-state requests (default: 200)")
    parser.add_argument("--concurrency", type=int, default=5, help="Number of concurrent client workers (default: 5)")
    parser.add_argument("--warmup", type=int, default=20, help="Number of warmup requests (default: 20)")
    parser.add_argument("--dataset-size", type=int, default=100, help="Dataset key range size (default: 100)")
    parser.add_argument("--payload-bytes", type=int, default=256, help="Payload size in bytes (default: 256)")
    parser.add_argument("--pattern", default="zipfian", choices=["zipfian", "uniform", "bursty"], help="Access distribution")
    parser.add_argument("--read-ratio", type=float, default=0.95, help="Read ratio (default: 0.95)")
    parser.add_argument("--run-id", default="run_1", help="Repetition Run ID (default: run_1)")
    parser.add_argument("--output-dir", default="benchmarks/results", help="Output directory")

    args = parser.parse_args()
    execute_benchmark(
        base_url=args.url,
        tier=args.tier.upper(),
        num_requests=args.requests,
        concurrency=args.concurrency,
        warmup_requests=args.warmup,
        dataset_size=args.dataset_size,
        payload_bytes=args.payload_bytes,
        access_pattern=args.pattern,
        read_ratio=args.read_ratio,
        run_id=args.run_id,
        output_dir=args.output_dir,
    )
