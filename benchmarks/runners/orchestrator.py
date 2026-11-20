"""
Full Experiment Matrix Orchestrator (Section 6, 8, & 14)
Coordinates automated multi-tier benchmarking across all 6 live Vercel deployments.
Designed with safety limits for Neon & Upstash Free Tiers.
"""

import os
import sys
import time
import argparse
import logging

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from runners.run_benchmark import execute_benchmark
from reset_environment import clean_postgres, clean_redis

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Registered Live Vercel Production Deployments
DEPLOYMENT_TARGETS = {
    "E1": {
        "name": "baseline",
        "url": "https://baseline-gules-two.vercel.app",
        "desc": "Baseline direct Neon PostgreSQL (no cache)"
    },
    "E2": {
        "name": "memory-cache",
        "url": "https://memory-cache-eosin.vercel.app",
        "desc": "Process-local memory LRU cache (L1)"
    },
    "E3": {
        "name": "redis-cache",
        "url": "https://redis-cache-liard.vercel.app",
        "desc": "Shared external Upstash Redis over TLS (L2)"
    },
    "E4": {
        "name": "hybrid-cache",
        "url": "https://adaptive-serverless-caching.vercel.app",
        "desc": "Two-tier hierarchical cache (L1 -> L2 -> PostgreSQL)"
    },
    "E5": {
        "name": "adaptive-cache",
        "url": "https://adaptive-cache.vercel.app",
        "desc": "Adaptive frequency admission and dynamic TTL hierarchy"
    },
    "E6": {
        "name": "adaptive-cache-invalidation",
        "url": "https://adaptive-serverless-caching-r61o.vercel.app",
        "desc": "Adaptive caching with cross-instance version invalidation"
    },
}


def run_orchestration(
    tiers: list,
    repetitions: int = 3,
    requests_per_run: int = 150,
    concurrency: int = 5,
    access_pattern: str = "zipfian",
    clean_before: bool = False,
    pause_between_tiers_sec: int = 5,
):
    """Executes the benchmark matrix across selected tiers."""
    logger.info("======================================================================")
    logger.info("Starting Research Benchmark Orchestrator (Vercel Serverless Production)")
    logger.info(f"Target Tiers: {tiers}")
    logger.info(f"Repetitions per Tier: {repetitions} | Requests per Run: {requests_per_run} | Concurrency: {concurrency}")
    logger.info("======================================================================")

    if clean_before:
        logger.info("Clean before run requested: Resetting Neon & Upstash...")
        clean_postgres()
        clean_redis()

    generated_files = []

    for tier in tiers:
        if tier not in DEPLOYMENT_TARGETS:
            logger.warning(f"Unknown tier {tier}, skipping.")
            continue

        meta = DEPLOYMENT_TARGETS[tier]
        logger.info(f"\n>>> Running Experiments for Tier {tier} ({meta['name']})")
        logger.info(f"    URL: {meta['url']}")
        logger.info(f"    Desc: {meta['desc']}")

        for rep in range(1, repetitions + 1):
            run_id = f"run_{rep}"
            logger.info(f"\n--- Tier {tier} - Repetition {rep}/{repetitions} ({run_id}) ---")

            csv_file = execute_benchmark(
                base_url=meta["url"],
                tier=tier,
                num_requests=requests_per_run,
                concurrency=concurrency,
                warmup_requests=15,
                dataset_size=100,
                payload_bytes=256,
                access_pattern=access_pattern,
                read_ratio=0.95,
                run_id=run_id,
            )
            generated_files.append(csv_file)

            # Brief pause between repetitions to avoid overwhelming free tier rate-limits
            time.sleep(2)

        time.sleep(pause_between_tiers_sec)

    logger.info("\n======================================================================")
    logger.info("All benchmark runs completed successfully!")
    logger.info(f"Generated {len(generated_files)} result datasets in benchmarks/results/")
    logger.info("Next step: Run `python benchmarks/analysis/analyze_results.py` to compile tables & charts.")
    logger.info("======================================================================")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Orchestrate multi-tier benchmark runs on Vercel")
    parser.add_argument("--tier", default="ALL", help="Target tier (E1, E2, E3, E4, E5, E6, or ALL)")
    parser.add_argument("--repetitions", type=int, default=3, help="Number of repetitions per tier (default: 3)")
    parser.add_argument("--requests", type=int, default=150, help="Requests per repetition (default: 150)")
    parser.add_argument("--concurrency", type=int, default=5, help="Concurrent workers (default: 5)")
    parser.add_argument("--pattern", default="zipfian", choices=["zipfian", "uniform", "bursty"])
    parser.add_argument("--clean", action="store_true", help="Reset DB and Redis before starting")

    args = parser.parse_args()
    selected_tiers = list(DEPLOYMENT_TARGETS.keys()) if args.tier.upper() == "ALL" else [args.tier.upper()]

    run_orchestration(
        tiers=selected_tiers,
        repetitions=args.repetitions,
        requests_per_run=args.requests,
        concurrency=args.concurrency,
        access_pattern=args.pattern,
        clean_before=args.clean,
    )
