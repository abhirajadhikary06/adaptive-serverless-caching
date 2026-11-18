# Adaptive Hybrid Caching for Low-Latency Serverless Architectures

## Technical Research, Implementation, and Experimental Plan

**Repository:** `adaptive-serverless-caching`\
**Document:** `docs/technical-research-plan.md`\
**Primary language:** Python\
**Backend:** Flask\
**Database:** Neon PostgreSQL\
**External cache:** Upstash Redis\
**Deployment target:** Vercel Python Functions (subject to runtime and
deployment-limit validation)\
**Research focus:** Compare six progressively more capable serverless
data-access configurations, with particular attention to latency, cache
efficiency, multi-instance behaviour, and consistency.

---------------------------------------------------------------------------------------------------------

## 1. Research objective

This project investigates whether a two-level cache — process-local
memory (L1) plus shared Redis (L2) — can improve the latency and
efficiency of a serverless application, and whether adaptive cache
policies can improve on a basic hybrid design.

PostgreSQL remains the authoritative source of truth. Cache entries are
derived data and must be safe to discard or rebuild.

The project is an experimental study, not a claim that a particular
cloud provider exactly reproduces AWS Lambda. The selected hosting
platform, region, runtime, instance reuse, database location, and
network path all affect observed results. These details must be recorded
and discussed.

### Main research questions

-   **RQ1:** How do no-cache, local-memory, Redis, and hybrid caching
    compare for response latency and database load?
-   **RQ2:** How does distribution across multiple serverless execution
    instances affect local-cache hit ratio and tail latency?
-   **RQ3:** Do adaptive cache-admission and TTL policies improve cache
    efficiency under changing access patterns?
-   **RQ4:** What are the latency and freshness trade-offs of
    cross-instance invalidation under concurrent reads and writes?

### Hypotheses to test

These are hypotheses, not expected findings:

-   H1: A cache hit in process-local memory will generally be faster
    than a Redis lookup, although local cache availability depends on
    the request reaching the same warm process.
-   H2: A shared Redis cache may provide more consistent cache reuse
    across instances than process-local memory, at the cost of a network
    round trip.
-   H3: A hybrid cache may reduce average lookup latency when L1 hits
    are sufficiently frequent.
-   H4: Adaptive admission and TTL may improve cache utilisation for
    skewed or changing workloads, but may add computation and policy
    overhead.
-   H5: Cross-instance invalidation can reduce stale reads, while adding
    write-path and coordination overhead.

---------------------------------------------------------------------------------------------------------

## 2. Scope and experimental configurations

Build six independently selectable configurations. They should share the
same API contract, data model, workload generator, instrumentation
conventions, and database dataset wherever applicable.

| ID | Directory | Configuration | Cache Behaviour |
|---|---|---|---|
| E1 | `baseline/` | No cache | Every read goes to PostgreSQL |
| E2 | `memory-cache/` | L1 memory | Process-local cache; PostgreSQL on miss |
| E3 | `redis-cache/` | L2 Redis | Shared Redis; PostgreSQL on miss |
| E4 | `hybrid-cache/` | L1 + L2 | Memory, then Redis, then PostgreSQL |
| E5 | `adaptive-cache/` | Adaptive hybrid | E4 plus adaptive admission and TTL |
| E6 | `adaptive-cache-invalidation/` | Adaptive hybrid with invalidation | E5 plus cross-instance invalidation/version handling |

**Important:** These six configurations are not six separate cloud
accounts or six separate databases. Use one Neon project/database and
one Upstash Redis database initially. Separate configurations by
explicit cache-key prefixes, configuration values, and—-if
useful—-PostgreSQL schemas or clearly named tables. Use separate
credentials only if isolation or security requires it.

The configurations must not accidentally share cache keys. For example:

-   `e3:record:{record_id}`
-   `e4:l1:record:{record_id}` (local process key; prefix is still
    useful in code/logs)
-   `e4:l2:record:{record_id}`
-   `e5:l1:record:{record_id}`
-   `e5:l2:record:{record_id}`
-   `e6:l1:record:{record_id}`
-   `e6:l2:record:{record_id}`

E1 must not read from or write to Redis or local cache. E2 must not use
Redis. E3 must not use L1. E4—E6 use both layers as specified.

---------------------------------------------------------------------------------------------------------

## 3. Repository structure

Suggested initial structure:

``` text
adaptive-serverless-caching/
├── baseline/
│   ├── app.py
│   ├── routes.py
│   ├── database.py
│   ├── config.py
│   └── requirements.txt
├── memory-cache/
│   ├── app.py
│   ├── routes.py
│   ├── database.py
│   ├── cache.py
│   ├── config.py
│   └── requirements.txt
├── redis-cache/
│   ├── app.py
│   ├── routes.py
│   ├── database.py
│   ├── cache.py
│   ├── config.py
│   └── requirements.txt
├── hybrid-cache/
│   ├── app.py
│   ├── routes.py
│   ├── database.py
│   ├── cache.py
│   ├── config.py
│   └── requirements.txt
├── adaptive-cache/
│   ├── app.py
│   ├── routes.py
│   ├── database.py
│   ├── cache.py
│   ├── admission.py
│   ├── ttl_policy.py
│   ├── config.py
│   └── requirements.txt
├── adaptive-cache-invalidation/
│   ├── app.py
│   ├── routes.py
│   ├── database.py
│   ├── cache.py
│   ├── admission.py
│   ├── ttl_policy.py
│   ├── invalidation.py
│   ├── config.py
│   └── requirements.txt
├── benchmarks/
│   ├── workloads/
│   ├── runners/
│   ├── analysis/
│   ├── results/
│   └── README.md
├── docs/
│   └── technical-research-plan.md
├── .env.example
├── .gitignore
└── README.md
```

This is a logical starting point, not a requirement to duplicate every
utility forever. Once the six configurations work, common code may be
moved into a shared package (for example, `common/`) while preserving
independently selectable configurations. Avoid premature abstraction
before the first working baseline.

Keep secrets out of Git. Commit `.env.example` with variable names and
placeholder values only.

---------------------------------------------------------------------------------------------------------

## 4. Shared application and data design

### 4.1 Application behaviour

Use the same Flask API contract in all six configurations so that the
benchmark runner can switch targets without changing request semantics.

Suggested endpoints:

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/health` | Application health; must not query or warm the cache |
| `GET` | `/records/{record_id}` | Fetch one record through the selected cache configuration |
| `POST` | `/records` | Create a record |
| `PUT` | `/records/{record_id}` | Update a record |
| `DELETE` | `/records/{record_id}` | Delete a record |
| `GET` | `/metrics` | Return application-level counters, if enabled |
| `GET` | `/openapi.json` | OpenAPI specification |
| `GET` | `/docs` | Swagger UI, if supported by the chosen Flask documentation package |

Use identical response bodies and status codes across configurations. Do
not include cache implementation details in the business response unless
explicitly enabled for diagnostic experiments. Record cache-layer
outcomes in server-side metrics or response headers reserved for
testing.

### 4.2 Example data model

Use a repeatable synthetic dataset. A simple starting table:

``` sql
CREATE TABLE records (
    id BIGINT PRIMARY KEY,
    category VARCHAR(80) NOT NULL,
    payload JSONB NOT NULL,
    version BIGINT NOT NULL DEFAULT 1,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

The `payload` should have a controlled size. Include a deterministic
seed script so every experiment can restore the same records and values.

Suggested payload-size levels:

-   Small: 256 bytes
-   Medium: 1 KiB
-   Large: 4 KiB
-   Stress case: 16 KiB

These are target serialized payload sizes, not exact PostgreSQL row
sizes. Measure and record actual serialized response size.

Suggested dataset sizes:

-   1,000 records —- development and smoke tests
-   10,000 records —- standard experiment
-   100,000 records —- scale experiment

Start with 10,000 records for the main benchmark. Use the other sizes
for sensitivity tests if platform limits permit.

### 4.3 Database consistency

-   PostgreSQL is the source of truth.
-   Use parameterized SQL and connection pooling supported by the
    deployment environment.
-   Keep schema, indexes, and query shape identical across all
    configurations.
-   Use the same database region and connection settings for every run.
-   Record database region, compute settings, connection method, and any
    pooling/proxy configuration.
-   Do not let one configuration use a different SQL query or index
    plan.
-   Reset test data between write-consistency experiments.

---------------------------------------------------------------------------------------------------------

## 5. Cache design by configuration

### E1 —- No cache

-   Every `GET /records/{id}` performs the same PostgreSQL query.
-   Do not instantiate or call Redis.
-   Do not retain records in process-global state.
-   Capture database query time and total response time.
-   This is the serverless no-cache reference configuration.

### E2 —- Process-local L1 memory cache

-   Use a bounded process-local cache.
-   Start with LRU eviction and a configurable TTL.
-   A cache hit returns the local value.
-   A miss queries PostgreSQL and stores the result.
-   Track hit, miss, eviction, expiration, and current entry count.
-   Treat each process/instance as having an independent cache.
-   Do not assume that a Vercel function instance will be reused or that
    requests will reach the same instance.

A process-local cache is not shared across independent instances and may
disappear when an instance is recycled.

### E3 —- Shared L2 Redis cache

-   Use Upstash Redis as the shared cache.
-   Use a namespaced key for each configuration.
-   On hit, deserialize and return the value.
-   On miss, query PostgreSQL and populate Redis with an expiry.
-   Set bounded connection/read timeouts and handle Redis failures
    gracefully.
-   If Redis is unavailable, fall back to PostgreSQL rather than failing
    the data request, and record the fallback.
-   Record Redis lookup latency separately from total request latency.

### E4 —- Hybrid L1 + L2 cache

Read path:

1.  Check process-local L1.
2.  On L1 hit, return the value.
3.  On L1 miss, check shared Redis L2.
4.  On L2 hit, populate L1 and return the value.
5.  On L2 miss, query PostgreSQL.
6.  Populate L2 and L1, then return the value.

Write path for the initial version:

1.  Commit the write to PostgreSQL.
2.  Invalidate the corresponding Redis key.
3.  Invalidate the local cache entry in the current process.
4.  Record the invalidation action and timing.

This first version does not yet guarantee that every other process-local
cache is invalidated. E6 will address cross-instance propagation.

### E5 —- Adaptive hybrid cache

Begin with a simple, explainable policy rather than a complex
machine-learning policy.

Adaptive admission:

-   Track access frequency using a bounded counter or approximate
    frequency structure.
-   Admit an item to cache only after it crosses a configurable
    frequency threshold, or compare this with admit-on-first-miss.
-   Ensure counters themselves are bounded and have a defined
    aging/reset policy.
-   Compare policy overhead with the saved cache/database latency.

Adaptive TTL:

-   Start with a configurable minimum and maximum TTL.
-   Use access frequency and/or recency to select an expiry within those
    bounds.
-   Keep the algorithm deterministic and document the formula.
-   Do not let the adaptive policy exceed a maximum freshness limit.
-   Record the TTL selected for each admitted item in diagnostic
    metrics.

Compare adaptive admission and adaptive TTL separately before evaluating
them together. This helps identify which mechanism contributes to any
observed change.

### E6 —- Adaptive hybrid with cross-instance invalidation

Implement a documented invalidation mechanism. Candidate approaches:

-   **Version-based validation:** store a record version in PostgreSQL
    and cache entries; compare versions according to a clearly defined
    validation protocol.
-   **Redis Pub/Sub invalidation:** publish an invalidation event after
    a successful database commit. A process must have a reliable
    subscriber lifecycle for this to work; short-lived serverless
    functions may not maintain persistent subscriptions.
-   **Short local TTL plus shared version state:** use a bounded local
    TTL and a shared version/invalidation marker to limit stale windows.

Do not assume Redis Pub/Sub alone is reliable for serverless processes
that are not continuously connected. Test and document missed-message
behaviour. A version-based or bounded-TTL fallback may be needed.

For each write, define the ordering of database commit, shared-cache
invalidation/update, and local-cache invalidation. Record the resulting
stale-read window and invalidation propagation delay.

---------------------------------------------------------------------------------------------------------

## 6. Parameters and numerical experiment matrix

Do not vary every parameter in every run at once. Use staged
experiments: first establish a stable reference, then vary one dimension
at a time, and finally run selected combined workloads.

### 6.1 Primary workload parameters

| Parameter | Suggested Levels | Purpose |
|---|---|---|
| Dataset size | 1K, 10K, 100K records | Effect of working-set size |
| Payload size | 256 B, 1 KiB, 4 KiB, 16 KiB | Serialization/network/data-size effects |
| Target cache hit ratio | 0%, 25%, 50%, 75%, 90%, 99% | Sensitivity to cache usefulness |
| Concurrent clients | 1, 10, 25, 50; optionally 100 | Load and contention |
| Total requests per run | 1,000 smoke; 5,000 standard; 10,000+ extended | Measurement sample size |
| Independent repetitions | At least 5 | Run-to-run variation |
| Read/write mix | 100/0, 95/5, 80/20, 50/50 | Write pressure and invalidation |
| Fixed TTL | 5, 30, 60, 300 seconds | Expiration sensitivity |
| Access pattern | Uniform, Zipfian, bursty | Locality and skew |
| Warm-up requests | 100 or a documented fixed period | Separate warm-up from measurement |
| Idle interval | 0, 30, 120, 300 seconds | Explore instance/cache persistence |
| Cache capacity | 100, 1K, 10K entries | Eviction and memory pressure |

These values are proposed starting points, not universal requirements.
First verify that the deployment tier and benchmark environment can
sustain them. Record any reduced levels and why they were selected.

### 6.2 Hit-ratio generation

A target hit ratio is a workload property, not a guaranteed observed
cache hit ratio. Generate requests using a controlled hot/cold key
distribution and report both:

-   configured target hit ratio; and
-   measured L1, L2, and overall hit ratios.

For E1, measured cache hit ratio is not applicable. For E2, report L1
hit ratio. For E3, report L2 hit ratio. For E4—E6, report L1 hits, L2
hits after L1 misses, database misses, and total cache-served requests.

### 6.3 Access distributions

-   **Uniform:** keys are selected with approximately equal probability.
-   **Zipfian:** a small subset of keys receives a large fraction of
    requests. Record the Zipf exponent used (start with 0.8 and 1.0).
-   **Bursty:** requests temporarily concentrate on a subset of keys,
    then shift to another subset. Document burst duration and key-set
    changes.

### 6.4 Concurrency and load generation

-   Use a benchmark runner separate from the application.
-   Keep the load generator machine and network stable across
    comparative runs.
-   Run one configuration at a time for primary comparisons to avoid
    resource contention.
-   Record offered concurrency, completed requests, timeouts, and
    errors.
-   Do not interpret configured concurrency as achieved throughput.
-   If the load generator becomes the bottleneck, reduce concurrency or
    use a more capable controlled runner.

### 6.5 Cache policy parameters

For E2—E6, document:

-   L1 capacity and eviction policy.
-   L1 TTL.
-   L2 TTL.
-   Admission threshold/window.
-   Frequency-counter aging interval.
-   Adaptive TTL minimum and maximum.
-   Invalidation mechanism.
-   Version or generation number rules.
-   Redis timeout and fallback behaviour.

A suggested initial policy range:

| Policy Parameter | Initial Values to Test |
|---|---|
| L1 capacity | 100, 1,000 entries |
| L1 TTL | 5, 30, 60 seconds |
| L2 TTL | 30, 60, 300 seconds |
| Admission threshold | 1, 2, 3 accesses in observation window |
| Adaptive TTL minimum | 5 seconds |
| Adaptive TTL maximum | 300 seconds |
| Redis timeout | Set based on measured platform behaviour; record exact value |

Avoid choosing a TTL or timeout after seeing the results without
documenting the change. Any tuning must be reported as a separate
experiment.

---------------------------------------------------------------------------------------------------------

## 7. Metrics to collect

Collect raw per-request observations wherever practical. Do not store
only aggregate averages.

### Latency

-   End-to-end response latency (milliseconds).
-   L1 lookup latency.
-   L2 Redis lookup latency.
-   PostgreSQL query latency.
-   Cache population latency.
-   Invalidation operation latency.
-   Cold-start or first-request latency, where observable.

Report median, mean, p90, p95, and p99. Include sample count and
dispersion (for example, standard deviation or interquartile range).
Avoid relying on the mean alone.

### Cache effectiveness

-   L1 hit count and hit ratio.
-   L2 hit count and hit ratio.
-   Database miss count.
-   Eviction count.
-   Expiration count.
-   Admission/rejection count.
-   Cache entry count and approximate memory usage.
-   Redis errors and fallback count.

Define ratios explicitly. For example:

`L1 hit ratio = L1 hits / total read requests`

`L2 hit ratio = L2 hits / L1 misses`

`Overall cache-served ratio = (L1 hits + L2 hits) / total read requests`

### System and correctness metrics

-   Completed requests and requests per second.
-   Error and timeout rate.
-   Database query count.
-   Database connection errors.
-   Stale-read count and stale-read duration, where measurable.
-   Invalidation propagation delay.
-   Version mismatch count.
-   Write completion and read-after-write correctness.

### Cost

If provider usage or billing data is available, record it. Otherwise, do
not invent cost figures. Report service tier, request volume, and any
available usage units, and clearly label cost analysis as unavailable or
approximate.

---------------------------------------------------------------------------------------------------------

## 8. Benchmark procedure

Use the same procedure for every configuration.

1.  Record code commit, dependency versions, deployment identifier,
    platform region, database region, Redis region, and environment
    settings.
2.  Restore the deterministic database fixture.
3.  Clear only the relevant configuration's Redis key namespace.
4.  Restart or otherwise reset the local test process for local-cache
    tests. For deployed serverless tests, a true global cache reset may
    not be possible; use versioned test keys and document the
    limitation.
5.  Send a fixed warm-up workload and exclude it from steady-state
    measurements.
6.  Run the selected workload with the fixed request count, concurrency,
    data size, and access distribution.
7.  Save raw request observations and application counters.
8.  Repeat at least five independent times.
9.  Check errors, missing samples, and benchmark-runner saturation.
10. Summarize results with consistent scripts and chart definitions.

### Cache reset cautions

-   Redis: delete only the experiment's key prefix; do not flush a
    shared database if other experiments may be using it.
-   L1 memory: clear the in-process cache in local tests or create a
    fresh process. A deployed serverless platform may retain or recycle
    instances unpredictably.
-   Database: do not delete and reseed production or shared data during
    normal benchmarks. Use a dedicated test dataset/schema and a
    controlled reset script.
-   A cache reset does not reset platform cold-start state. Treat cache
    state and runtime lifecycle as separate experimental variables.

### Suggested raw result columns

`run_id, timestamp, config_id, deployment_id, request_id, operation, record_id, status_code, total_latency_ms, l1_latency_ms, l2_latency_ms, db_latency_ms, cache_layer, cache_hit, payload_bytes, concurrency, target_hit_ratio, access_pattern, dataset_size, ttl_seconds, error_type`

Add columns for policy version, selected TTL, instance identifier (only
if reliably available), and invalidation timing when those values can be
measured. Never fabricate an instance identifier if the platform does
not expose one.

---------------------------------------------------------------------------------------------------------

## 9. API documentation and observability

Each implementation should expose the same OpenAPI contract and Swagger
UI where practical.

Document:

-   Endpoint path, method, parameters, and request body.
-   Response schema and status codes.
-   Whether the endpoint reads or writes data.
-   Cache behaviour for that configuration.
-   Test-only diagnostic headers or metrics.
-   Error and fallback behaviour.

Suggested diagnostic headers (use consistently across configurations):

-   `X-Cache-Status`: `HIT`, `MISS`, or `BYPASS`
-   `X-Cache-Layer`: `L1`, `L2`, `DB`, or `NONE`
-   `X-Request-ID`: unique request identifier

Do not expose credentials, raw cache contents, internal stack traces, or
sensitive infrastructure details through public documentation endpoints.

Swagger is for manual inspection and API testing. Use the separate
benchmark runner—-not manual Swagger clicks—-for formal performance
measurements, because manual requests are not sufficiently repeatable.

---------------------------------------------------------------------------------------------------------

## 10. Local development and deployment strategy

### Local-first workflow

1.  Create the repository and six configuration directories.
2.  Create a shared `.env.example` and local environment file (ignored
    by Git).
3.  Provision one Neon database and one Upstash Redis database.
4.  Implement and test E1 first.
5.  Reuse the same API and database contract for E2—E6.
6.  Run unit tests for cache behaviour and integration tests against
    PostgreSQL/Redis.
7.  Run local smoke benchmarks to confirm instrumentation and CSV
    output.
8.  Run the same benchmark workload against all six configurations
    locally.
9.  Fix correctness and measurement issues before deployment.

Local tests establish correctness and provide a controlled first
comparison. They do not reproduce serverless instance lifecycle or cloud
network latency.

### Vercel deployment validation

Before committing to Vercel, verify current Python runtime support,
Flask routing behaviour, function limits, request duration limits,
package size limits, environment-variable handling, and whether the
deployment model permits the process-local state assumptions required by
E2/E4—E6.

Important constraints:

-   A process-global Python dictionary is local to one execution
    process; it is not a globally shared cache.
-   Instance reuse is not guaranteed.
-   Multiple instances may have independent L1 caches.
-   Serverless functions may be frozen, recycled, or started fresh.
-   Long-lived Redis Pub/Sub subscribers may not fit a short-lived
    serverless execution model.
-   Do not assume that deploying six folders in one repository
    automatically creates six independent Vercel projects. Configure
    separate project roots/build settings or use one deployment with six
    clearly separated function routes, after validating platform
    support.
-   If the selected Vercel model cannot support the required Flask or
    lifecycle behaviour, use a suitable persistent service for the
    experiment or revise the deployment design and document the change.

Keep one shared Neon database and one shared Upstash database initially.
Configure the same credentials securely in each deployment. Use separate
prefixes and test records to prevent cross-configuration contamination.

### Environment variables

Suggested names:

``` dotenv
DATABASE_URL=
UPSTASH_REDIS_REST_URL=
UPSTASH_REDIS_REST_TOKEN=
APP_ENV=
CACHE_NAMESPACE=
L1_CAPACITY=
L1_TTL_SECONDS=
L2_TTL_SECONDS=
REDIS_TIMEOUT_SECONDS=
```

Only configurations that need Redis should require the Redis variables.
Never commit real credentials.

---------------------------------------------------------------------------------------------------------

## 11. Testing requirements

### Unit tests

-   Cache hit returns the stored value.
-   Cache miss reaches the next layer.
-   Expired entries are not served.
-   LRU/LFU eviction behaves as specified.
-   Serialization round-trips correctly.
-   Redis failures fall back safely.
-   Admission policy admits/rejects according to its documented rule.
-   Adaptive TTL remains within configured bounds.
-   Invalidation removes or supersedes the correct entry.
-   Version checks detect outdated entries.

### Integration tests

-   PostgreSQL read and write operations work.
-   Redis set/get/expiry works.
-   E4 read-through populates both cache layers.
-   E5 policy decisions affect cache admission and expiry as expected.
-   E6 invalidation is observed by another test process.
-   Concurrent update/read tests follow the documented consistency
    model.

### Correctness tests

-   A successful write is persisted in PostgreSQL.
-   Read-after-write returns the new value under the documented
    consistency model.
-   Deleted records are not returned from stale cache entries beyond the
    declared bound.
-   Redis outage does not silently return corrupt data.
-   Cache data can be discarded and reconstructed from PostgreSQL.

---------------------------------------------------------------------------------------------------------

## 12. Analysis plan

For each experiment:

-   Compare E1—E6 under identical workload conditions.
-   Plot latency distributions, not only averages.
-   Compare p50, p95, and p99 latency.
-   Plot L1/L2 hit ratios and database query volume.
-   Compare throughput and error rate.
-   Analyze how payload size, dataset size, concurrency, hit ratio, and
    access distribution change the results.
-   Evaluate adaptive policies separately and jointly.
-   Quantify stale-read frequency and invalidation delay for E6.
-   Report confidence intervals or another appropriate uncertainty
    measure where sample size supports it.
-   Distinguish statistical significance from practical significance.
-   Explain unexpected results, including network distance, cold starts,
    platform throttling, and benchmark noise.

Do not compare absolute latency numbers directly with the 2019 AWS paper
as if the environments were equivalent. Compare results within the
controlled environment and use the paper as prior work and motivation.

---------------------------------------------------------------------------------------------------------

## 13. Research phases and deliverables

### Phase 1 — Literature review and research design

-   Review serverless execution, caching, multi-level cache, adaptive
    admission/TTL, and consistency literature.
-   Identify the precise gap and avoid claiming novelty before the
    review.
-   Finalize research questions, hypotheses, architecture, metrics, and
    experiment matrix.

**Deliverable:** Literature review and research protocol.

### Phase 2 — Baseline implementation

-   Build the Flask API and Neon schema.
-   Implement E1 without any cache.
-   Add OpenAPI/Swagger, logging, timing instrumentation, and benchmark
    runner.
-   Validate deterministic dataset setup and raw CSV output.

**Deliverable:** Reproducible no-cache baseline.

### Phase 3 — Individual cache implementations

-   Implement E2 process-local L1.
-   Implement E3 shared Redis L2.
-   Add TTL, eviction, counters, and failure handling.
-   Run initial local comparisons against E1.

**Deliverable:** E1, E2, and E3 working and measured.

### Phase 4 — Hybrid cache architecture

-   Implement E4 L1 → L2 → PostgreSQL read-through.
-   Add write-through-to-database followed by cache invalidation.
-   Test cache population, fallback, and multi-process behaviour.

**Deliverable:** Working hybrid cache.

### Phase 5 — Adaptive cache management and invalidation

-   Implement E5 adaptive admission and TTL, evaluating each separately.
-   Implement E6 cross-instance invalidation or version-based
    validation.
-   Test concurrent writes, stale reads, and failure scenarios.

**Deliverable:** Adaptive hybrid configurations with documented
consistency behaviour.

### Phase 6 — Experimental evaluation

-   Execute the workload matrix using repeatable runs.
-   Capture raw results and configuration metadata.
-   Analyze latency, hit ratios, database load, throughput, resource
    use, and consistency.
-   Produce reproducible tables and charts.

**Deliverable:** Experimental dataset and evaluation results.

### Final research-writing activity

After the experiments, write the paper using the actual measured
results. Include architecture, implementation, methodology, findings,
limitations, and reproducibility instructions. Do not write conclusions
or claim improvements before the experiments support them.

**Deliverable:** Research paper and public reproducibility repository.

---------------------------------------------------------------------------------------------------------

## 14. Suggested execution order

Do not implement all six configurations simultaneously.

1.  Complete E1 and its benchmark runner.
2.  Freeze the API contract and dataset.
3.  Add E2 and E3 independently.
4.  Verify E2/E3 measurements and failure handling.
5.  Build E4 from the validated cache components.
6.  Add E5 policies one at a time.
7.  Add E6 invalidation and consistency tests.
8.  Run the full experiment matrix.
9.  Deploy only after local correctness and smoke benchmarks pass.
10. Repeat the controlled experiments against the deployed environment
    and label local and cloud results separately.

---------------------------------------------------------------------------------------------------------

## 15. Threats to validity and limitations

Document at least the following:

-   Vercel execution lifecycle may differ from AWS Lambda.
-   Process-local cache state may be difficult to observe or reset in a
    managed serverless platform.
-   Neon and Upstash network latency may dominate cache lookup time
    depending on region placement.
-   Free-tier quotas, throttling, sleeping, and resource limits may
    affect results.
-   A shared database or Redis service can introduce contention between
    configurations.
-   The benchmark generator may become a bottleneck.
-   Small request counts can produce unstable p95/p99 estimates.
-   Cache admission and adaptive TTL introduce CPU and bookkeeping
    overhead.
-   Invalidation mechanisms may offer different delivery guarantees.
-   Results from one application, workload, provider, or region may not
    generalize to all serverless systems.

Mitigate these issues by recording environment metadata, using repeated
runs, isolating key namespaces, using identical workloads, and reporting
limitations honestly.

---------------------------------------------------------------------------------------------------------

## 16. Definition of completion

The research implementation is ready for final evaluation when:

-   All six configurations implement the same API contract.
-   E1 is demonstrably cache-free.
-   E2 uses only process-local memory.
-   E3 uses shared Redis without L1.
-   E4 implements L1 → L2 → PostgreSQL.
-   E5 implements documented adaptive admission and TTL.
-   E6 implements and tests cross-instance invalidation or version-based
    consistency.
-   Unit and integration tests pass.
-   The benchmark runner produces raw, reproducible results.
-   Every result includes configuration and environment metadata.
-   The analysis reports latency distributions, cache effectiveness,
    database load, errors, and consistency.
-   The repository documents setup, deployment, benchmark execution, and
    known limitations.

**Core principle:** Keep the database as the source of truth, make each
configuration independently testable, change one experimental variable
at a time, and base every research conclusion on reproducible
measurements.