# Adaptive Hybrid Caching for Low-Latency Serverless Architectures

[![Serverless](https://img.shields.io/badge/Architecture-Serverless%20FaaS-orange.svg)](https://vercel.com)
[![Database](https://img.shields.io/badge/Database-Neon%20PostgreSQL-green.svg)](https://neon.tech)
[![Cache](https://img.shields.io/badge/Cache-Upstash%20Redis%20(TLS)-red.svg)](https://upstash.com)
[![Python](https://img.shields.io/badge/Python-3.11%20%7C%203.12-blue.svg)](https://python.org)

An empirical research implementation investigating multi-tier caching architectures (process-local memory L1 + shared remote Redis L2) combined with **adaptive admission control**, **dynamic TTL scaling**, and **real-time cross-instance cache invalidation** on serverless runtimes.

---

## 🔬 Research Architecture & Progressive Tiers

This repository implements six progressively sophisticated data-access architectures evaluated against authoritative **Neon PostgreSQL** and **Upstash Redis**:

| Tier ID | Directory | Architecture | Key Characteristics |
| :--- | :--- | :--- | :--- |
| **E1** | [`baseline/`](file:///c:/Abhiraj/OpenSource/adaptive-serverless-caching/baseline) | **Baseline (No Cache)** | Direct queries to Neon PostgreSQL. Pure database latency floor (~691 ms). |
| **E2** | [`memory-cache/`](file:///c:/Abhiraj/OpenSource/adaptive-serverless-caching/memory-cache) | **Process Memory (L1)** | Bounded LRU in-RAM cache. Microsecond read latency (0.05 ms, 13,815× speedup). |
| **E3** | [`redis-cache/`](file:///c:/Abhiraj/OpenSource/adaptive-serverless-caching/redis-cache) | **Shared Redis (L2)** | Native TCP/TLS Upstash Redis. Persistent across container recycles (~222 ms). |
| **E4** | [`hybrid-cache/`](file:///c:/Abhiraj/OpenSource/adaptive-serverless-caching/hybrid-cache) | **Two-Tier Hybrid** | Hierarchical Read-Through (L1 $\rightarrow$ L2 $\rightarrow$ DB) with Write-Through invalidation. |
| **E5** | [`adaptive-cache/`](file:///c:/Abhiraj/OpenSource/adaptive-serverless-caching/adaptive-cache) | **Adaptive Caching** | E4 + Sliding-Window Admission ($\theta=2, W=60\text{s}$) + Bounded Dynamic TTL (5s–300s). |
| **E6** | [`adaptive-cache-invalidation/`](file:///c:/Abhiraj/OpenSource/adaptive-serverless-caching/adaptive-cache-invalidation) | **Adaptive + Sync** | E5 + Shared Version Marker Invalidation. Eliminates cross-container stale reads. |

---

## 📊 Summary of Empirical Findings (Vercel Serverless Production)

```
┌───────────────────────────────┬─────────────────┬───────────────────┬──────────────────────┐
│ Metric                        │ PostgreSQL (DB) │ Shared Redis (L2) │ Process RAM (L1)     │
├───────────────────────────────┼─────────────────┼───────────────────┼──────────────────────┤
│ Observed Read Latency         │ ~691 ms         │ ~222 ms           │ 0.05 ms – 0.06 ms    │
│ Relative Speedup vs DB        │ 1.0× (Baseline) │ 3.11× Speedup     │ 13,812× Speedup      │
│ Database Compute Offload      │ 0% (Full Load)  │ 100% Offload      │ 100% Offload         │
│ Multi-Instance Resilience     │ Authoritative   │ Fully Persistent  │ Isolated Per Instance│
└───────────────────────────────┴─────────────────┴───────────────────┴──────────────────────┘
```

* **Adaptive Admission:** Successfully filters out "one-hit wonders" on first access, preserving cache capacity.
* **Dynamic TTL:** Records admitted at frequency $f=2$ scale to $35\text{s}$ TTL; high-heat records scale up to $300\text{s}$.
* **Consistency:** Cross-instance version tracking purges stale local caches in real-time (`STALE_EVICTED`) upon out-of-band updates.

---

## 📖 Research Documentation & Reports

* 📄 **[Technical Research Plan](file:///c:/Abhiraj/OpenSource/adaptive-serverless-caching/docs/technical-research-plan.md)**: Specifications, mathematical models, API contracts, and evaluation methodology.
* 📈 **[Vercel Serverless Empirical Report](file:///c:/Abhiraj/OpenSource/adaptive-serverless-caching/docs/vercel-serverless-empirical-report.md)**: Full production benchmarks on Vercel Edge (`bom1::iad1`), header traces, latency tables, Mermaid diagrams, and theoretical derivations for the research paper.
* 🔬 **[Local Empirical Report](file:///c:/Abhiraj/OpenSource/adaptive-serverless-caching/docs/empirical-observation-report.md)**: Initial controlled local baseline measurements and comparative cloud serialization analysis.

---

## 🛠️ Technology Stack
* **Language & Web Framework:** Python 3.11 / 3.12, Flask 3.0+
* **Primary Relational Store:** Neon Serverless PostgreSQL (TLS Connection Pooling)
* **Distributed External Cache:** Upstash Redis (Serverless TCP/TLS, Port 6379)
* **Hosting Platform:** Vercel Python Serverless Functions (Fluid Compute Engine)

---

## 📚 Academic Citation
```bibtex
@inproceedings{ghosh2019caching,
  author    = {Ghosh, Bishakh Chandra and Addya, Sourav Kanti and Chakraborty, Sandip},
  title     = {Caching Techniques to Improve Latency in Serverless Architectures},
  booktitle = {2019 IEEE/ACM 12th International Conference on Utility and Cloud Computing (UCC)},
  pages     = {97--106},
  year      = {2019},
  publisher = {IEEE},
  doi       = {10.1109/UCC47432.2019.00021}
}
```
