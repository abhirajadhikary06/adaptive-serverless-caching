# Automated Benchmark Evaluation Harness

This directory contains the automated load generation, measurement, and statistical analysis suite for evaluating the 6 serverless caching configurations (E1–E6) deployed on **Vercel Serverless Functions**.

---

## 📁 Directory Structure

```text
benchmarks/
├── requirements.txt         # Dependencies (numpy, pandas, matplotlib, etc.)
├── reset_environment.py    # Empties Neon PostgreSQL and clears Upstash Redis
├── workloads/
│   ├── generator.py        # Zipfian, Uniform, and Bursty request generator
│   └── payloads.py         # Byte-accurate synthetic JSON payload generator
├── runners/
│   ├── run_benchmark.py    # Multi-threaded load tester & header parser
│   └── orchestrator.py     # Automated multi-tier runner with live Vercel URLs
├── results/                # Raw, unedited CSV benchmark logs
└── analysis/
    ├── analyze_results.py  # Computes percentiles, hit ratios, LaTeX/MD tables
    └── plot_charts.py      # Generates publication-ready figures (PNG & PDF)
```

---

## 🚀 Step-by-Step Usage Guide

### 1. Install Benchmark Dependencies
Make sure you are in your Python virtual environment:
```powershell
pip install -r benchmarks/requirements.txt
```

---

### 2. Reset the Environment (Clean State)
Before starting a benchmark run, run the cleanup script to empty PostgreSQL records and purge Redis cache namespaces:
```powershell
python benchmarks/reset_environment.py
```
* **PostgreSQL:** Truncates the `records` table so old test records don't conflict.
* **Redis:** Clears `e3*`, `e4*`, `e5*`, `e6*` namespaces.
* **Free-Tier Safe:** Does not exhaust quotas.

---

### 3. Run the Automated Benchmark Suite

#### Option A: Run ALL 6 Tiers on Vercel Sequentially (Recommended)
This runs across all 6 live Vercel deployments, sending 150 requests across 3 independent repetitions (default settings stay safely under Upstash and Neon free limits):
```powershell
python benchmarks/runners/orchestrator.py --tier ALL --repetitions 3 --requests 150
```

#### Option B: Run a Single Specific Tier (e.g. E6)
```powershell
python benchmarks/runners/orchestrator.py --tier E6 --repetitions 3 --requests 150
```

#### Option C: Custom Execution using `run_benchmark.py`
```powershell
python benchmarks/runners/run_benchmark.py `
  --url "https://adaptive-serverless-caching-r61o.vercel.app" `
  --tier "E6" `
  --requests 200 `
  --concurrency 5 `
  --warmup 20 `
  --pattern "zipfian"
```

---

### 4. Analyze Results & Generate Paper Tables
Compile summary statistics (Mean, Median, $p90$, $p95$, $p99$ tail latency, Hit Ratio, Speedup):
```powershell
python benchmarks/analysis/analyze_results.py
```
* Outputs summary table to console.
* Saves `benchmarks/analysis/summary_table.md`.
* Generates `benchmarks/analysis/table_results.tex` (LaTeX table ready for your paper).

---

### 5. Generate Publication Figures (PNG & PDF)
Generate high-resolution (300 DPI) plots for your paper:
```powershell
python benchmarks/analysis/plot_charts.py
```
* **`latency_comparison.png` / `.pdf`:** Mean & $p95$ bar chart comparing all tiers.
* **`latency_cdf.png` / `.pdf`:** Cumulative Distribution Function showing the exponential latency drop.
* **`hit_ratio_breakdown.png` / `.pdf`:** Stacked bar chart showing L1 vs L2 vs DB resolution.

---

## 🛡️ Free-Tier Safety Notes
* **Upstash Redis:** The default benchmark run consumes fewer than ~1,500 commands total across all 6 tiers, safely below the 10,000 daily command cap.
* **Neon PostgreSQL:** Payloads default to 256 bytes (`size_bytes: 256`), consuming < 1 MB of database storage.
