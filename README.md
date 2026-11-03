# Urban Thread — First-Party Attribution Pipeline

A backend-first marketing data platform that ingests first-party website events, order webhook updates and daily ad spend; reconstructs visitor journeys; attributes paid-order revenue; and serves channel/day reporting through an API and a lightweight dashboard.

> **Implementation status:** The application, worker, replay sender, migrations, dashboard, SQL queries and tests are implemented. The source fields and profiles are recorded in [`docs/data-dictionary.md`](docs/data-dictionary.md).
>
> **Verification status:** No application, migration, test, Docker or replay command has been executed at the time of this writing. September replay metrics, October follow-up results and performance numbers are therefore pending, not claimed. Run the commands below to produce actual results.

## What this demonstrates

- Reliable HTTP ingestion with idempotency and durable asynchronous processing.
- Raw-to-normalized data lineage and data-quality handling.
- Conservative visitor/customer identity resolution.
- 30-minute visitor session reconstruction.
- Deterministic, explainable last-click attribution with late-data reconciliation.
- Correct handling of order lifecycle, spend, currency and ROAS.
- PostgreSQL analytical SQL, replay tooling, integration tests and operational recovery.

## Architecture

**Python 3.12+ · FastAPI · PostgreSQL · SQLAlchemy 2.0 · Pydantic v2 · Alembic · PostgreSQL-backed worker · HTTPX · Pytest · Streamlit · Plotly · Docker Compose**

The application is a modular monolith with three runtime processes:

```text
Website pixel ───────┐
Order webhooks ──────┼──> FastAPI ──> PostgreSQL raw data + durable jobs
Spend feed/replay ───┘                        |
                                              v
                                      Python worker
                                              |
                          normalize -> identity -> sessions
                              -> touchpoints -> attribution
                                              |
                                              v
                                   Reporting APIs (FastAPI)
                                              |
                                              v
                                    Streamlit dashboard
```

See [`architecture.md`](architecture.md) for the full design, schema, API contracts, job semantics, attribution policy and failure-mode decisions. See [`PRD.md`](PRD.md) for the product requirements and acceptance criteria. See [`claude.md`](claude.md) for repository implementation instructions.

## Input data

The assignment describes three source files in `urbanthread_data_sep2026.zip`:

| File | Description | Measured rows |
|---|---|---:|
| `events.jsonl` | Website events captured by the tracking pixel | 55,758 events; 54,072 unique event IDs |
| `orders.csv` | Order-system webhook deliveries; each row is a delivery | 1,153 deliveries; 1,078 unique orders |
| `ad_spend.csv` | Daily spend per campaign from Meta, Google and TikTok | 230 rows; 210 natural keys |

The measured profiles and field semantics are in [`docs/data-dictionary.md`](docs/data-dictionary.md). Key findings include duplicate event IDs, 2,666 epoch-millisecond timestamps, 107 events whose device timestamp is more than 24 hours after source receipt, 20 duplicate spend snapshots, two currencies and 58 paid orders without event identity evidence. No invalid or out-of-range ISO years were found in the supplied event timestamps. Keep the archive/data files under `data/` locally and out of git. The sender submits all records through HTTP endpoints; it does not load files directly into database tables.

### Inspect the data

Extract the archive so the three files are directly under `data/`, then profile them:

```bash
python -m scripts.inspect_dataset --data-dir data --output docs/data-profile.json
```

The profile utility reports exact keys, counts, event names, timestamp shapes, distinct candidates and top values. Do not commit source data or personal sample values.

## Prerequisites

- Docker Engine and Docker Compose plugin.
- Python 3.12+ for running the replay sender locally (or use the replay container target once implemented).
- The assignment ZIP extracted or mounted under `data/`.

No cloud account or paid service is required.

## Quick start

The intended developer workflow is two commands:

```bash
cp .env.example .env
make up
make replay
```

`make up` builds and starts PostgreSQL, applies Alembic migrations, then starts the API, worker and dashboard. `make replay` submits the three source files through HTTP using bounded concurrency. On Windows without GNU Make, use the equivalent `docker compose` commands in the Makefile.

Expected local addresses:

| Service | URL |
|---|---|
| API | http://localhost:8000 |
| OpenAPI docs | http://localhost:8000/docs |
| Liveness | http://localhost:8000/health |
| Readiness | http://localhost:8000/ready |
| Dashboard | http://localhost:8501 |

If the source ZIP has not been extracted, the replay target should fail with a clear file-not-found message rather than partially replaying an unknown subset.

### Useful Makefile targets

Implement these targets (or document equivalent commands):

```bash
make up          # Build/start db, api, worker, dashboard; apply migrations
make down        # Stop containers; preserve named DB volume
make logs        # Follow service logs
make migrate     # Apply Alembic migrations
make replay      # Replay all source files through HTTP
make replay-events
make replay-orders
make replay-spend
make test        # Run unit tests plus PostgreSQL integration tests
make lint        # Ruff
make sql-check   # Execute queries.sql against the local database
make reset       # Destructive: remove local DB volume and start fresh
```

`make reset` is destructive and must never be run as part of a normal replay.

## Configuration

Copy `.env.example` to `.env` and set local values. Never commit `.env`.

| Variable | Purpose | Suggested local default |
|---|---|---|
| `APP_ENV` | Runtime environment | `local` |
| `DATABASE_URL` | PostgreSQL connection string | `postgresql+psycopg://postgres:postgres@localhost:5432/urban_thread` locally; Compose overrides with the `db` service host |
| `ORDER_WEBHOOK_SECRET` | HMAC secret for order webhook replay | Development-only secret; change outside local use |
| `REPORTING_TIMEZONE` | Date bucketing timezone | `UTC` until dataset/store timezone is verified |
| `ATTRIBUTION_MODEL` | Primary attribution model | `last_click` |
| `ATTRIBUTION_LOOKBACK_DAYS` | Lookback window | `30` |
| `SESSION_TIMEOUT_MINUTES` | Session inactivity threshold | `30` |
| `HISTORICAL_VALIDITY_DAYS` | Maximum accepted source-age window | `365` |
| `IDENTITY_BACKFILL_DAYS` | Lookback for linking prior visitor events to new identity evidence | `30` |
| `MAX_REQUEST_BYTES` | Request body limit | `1048576` |
| `WORKER_POLL_SECONDS` | Idle worker poll interval | `1` |
| `WORKER_BATCH_SIZE` | Jobs claimed per batch | `5` |
| `WORKER_LEASE_SECONDS` | Reclaimable job lease | `120` |
| `WORKER_MAX_ATTEMPTS` | Retry cap before dead-letter | `5` |
| `REPLAY_CONCURRENCY` | HTTP replay concurrency | `15` |
| `API_BASE_URL` | Replay sender/dashboard target | `http://localhost:8000` |
| `LOG_LEVEL` | Logging threshold | `INFO` |

`REPORTING_TIMEZONE` is a policy setting, not evidence that the source is UTC. UTC is used because the dataset has no store-timezone field. Change it only with a documented reporting requirement.

## Ingestion API

### `POST /collect`

Accepts one website event. The API stores the raw payload and a durable processing job in one database transaction, then returns `202 Accepted` after commit. Duplicate `event_id` deliveries are acknowledged as duplicates without creating another raw event or job. Events without a trusted event ID are never content-deduplicated.

Source metadata headers are optional but recommended: `X-Source-File` and `X-Source-Row`. The replay sender uses them so failure reports can identify the original record.

### `POST /webhooks/orders`

Accepts one order webhook update. Multiple updates for the same source order are expected and retained. Delivery deduplication uses `X-Delivery-Id` / `Idempotency-Key` when supplied; otherwise the request is assigned an internal delivery key. The API never deduplicates order webhooks by `order_id`.

If `ORDER_WEBHOOK_SECRET` is set, requests must include `X-Signature: sha256=<hex>` (or `X-Hub-Signature-256`) over the exact request body. Canonical order state advances only when the source `updated_at` is newer. Older updates are retained as evidence and do not overwrite newer state.

### `POST /webhooks/ad-spend`

Supporting ingestion endpoint for daily campaign spend. It preserves every raw snapshot and queues normalization/mapping. The canonical spend fact uses the latest `pulled_at` per `(date, platform, campaign, currency)`; equal-timestamp conflicts are recorded and resolved by delivery-key tie-break.

### Reporting endpoints

| Endpoint | Description |
|---|---|
| `GET /api/v1/reports/channels?start_date=2026-09-01&end_date=2026-09-30` | Orders, attributed revenue, ad spend and ROAS by channel/currency |
| `GET /api/v1/reports/daily?start_date=...&end_date=...` | Daily channel performance series |
| `GET /api/v1/orders?start_date=...&end_date=...&limit=50` | Paginated order ledger |
| `GET /api/v1/orders/{order_id}` | Order state and attribution explanation |
| `GET /api/v1/status` | Freshness, job counts, data-quality issues and row counts |

The API response should include attribution model, lookback, reporting timezone and currency. Date filters are user-inclusive; internally, use a half-open timestamp range with the upper bound at midnight after the selected end date.

## Attribution and reporting rules

### Primary attribution model

The primary implementation uses **literal last-click**:

- 30-day lookback by default, configurable.
- Eligible touchpoints must occur at or before the order's verified conversion/paid timestamp.
- The lower lookback boundary is inclusive.
- The latest eligible touchpoint receives 100% credit; an explicitly evidenced Direct landing can win.
- A page view/product view is not automatically a marketing touchpoint.
- Direct is not a catch-all for missing identity/tracking; use `Unattributed` where evidence is absent.
- Late touchpoints and identity links trigger recalculation.

Last-non-direct can be added as an optional comparison model, but must not silently replace the literal last-click model requested in the assignment.

### Financial definitions

Finalize revenue basis only after inspecting the actual amount/status/refund fields. Keep currency with every amount and do not aggregate across currencies without an FX policy. Missing spend is not zero. ROAS is `SUM(attributed revenue) / SUM(ad spend)`; it is null when spend is zero or missing. Positive spend with no attributed revenue produces ROAS zero.

Revenue is grouped by order conversion/paid date; spend is grouped by spend date. They are not necessarily the same source timestamp.

## Replay the data

Commands:

```bash
python -m scripts.replay \
  --data-dir data \
  --base-url http://localhost:8000 \
  --concurrency 15
```

Use `--only events`, `--only orders` or `--only spend` for one file family. Inside Docker, `make replay` runs the sender with `--data-dir /app/data --base-url http://api:8000`.

The sender:

- Preserves each file's source dispatch order through a bounded work queue.
- Uses configurable bounded concurrency (default 15; accepted range 1–50, with 10–20 recommended).
- Reuses stable event IDs and deterministic order/spend delivery keys on retry.
- Retries timeouts, network failures, 429 and 5xx with bounded exponential backoff and jitter; respects `Retry-After`.
- Does not retry permanent 4xx validation/authentication errors.
- Prints accepted, duplicate, retried, rejected and failed totals.
- Writes a JSON failure report with filename, source row/line, endpoint, status, attempts and delivery key.
- Exits non-zero when rejected or unrecovered failures remain.

Because requests run concurrently, completion order can differ from file order. The backend must use source occurrence timestamps and source order versions, not network arrival order, to reconstruct the journey.

### Expected replay output (calculated, not tested)

**First replay** of September dataset:
- 57,141 total requests (55,758 events + 1,153 orders + 230 spend)
- ~55,455 accepted as new (54,072 unique event IDs + 1,153 orders + 230 spend)
- ~1,686 acknowledged as duplicate (repeated event IDs with identical payloads)
- Some transient retries expected (network/timing)
- Zero permanent rejects if data is valid

**Second replay** (idempotency verification):
- 57,141 total requests
- 0 accepted as new
- 57,141 acknowledged as duplicate
- Logical counts unchanged: same event count, order count, paid orders, revenue totals, ROAS

The replay script reports `accepted` for newly persisted records and `duplicate` for previously accepted records. Both are successful outcomes (2xx response). The dataset contains 1,686 event rows with repeated `event_id` values; these are legitimate duplicates from the source and will be deduplicated on first replay.

### Replay safety check

Run the same dataset twice. Compare:

- distinct logical event count
- unique canonical order count
- accepted spend fact count
- paid order count
- attributed revenue by channel/currency
- ad spend by channel/currency
- ROAS by channel/currency

All logical counts and financial metrics should remain unchanged after the second replay (ignoring ingestion audit counters that intentionally record duplicate deliveries).

## Sessions and identity

- Sessions belong to visitor IDs and use event occurrence time.
- A new session starts when the gap between consecutive events is strictly greater than 30 minutes. Exactly 30 minutes remains in the current session.
- Sessions can cross midnight and file boundaries.
- One-event sessions have zero duration.
- A late event may merge two previously separate sessions, so rebuild the affected visitor's sessions.
- Cross-device identity requires reliable shared customer evidence; never merge on IP, user-agent or approximate location.
- Conflicting visitor/customer links remain visible and unresolved until evidence resolves them.

The SQL session query groups sessions by the local date of session start. Query 2 should find each paid buyer's earliest visit across all reliably linked visitors; if the source cannot establish cross-device identity, document the limitation instead of inventing it.

## Dashboard

The Streamlit dashboard should use the reporting APIs, not reimplement attribution. It should include:

1. A clear Urban Thread / Marketing Attribution header and date range filter.
2. KPI cards for attributed revenue, paid orders, spend and overall ROAS.
3. A daily revenue/spend trend chart.
4. A channel performance table with orders, revenue, spend and ROAS.
5. Order drill-down showing selected touchpoint and attribution reason.
6. Data freshness and data-quality indicators (unknown channel, unresolved identity, unmatched spend, pending/dead-letter jobs).

Keep styling restrained and readable. Ensure empty, loading, no-spend, null-ROAS and API-error states are handled.

## SQL assignment (`queries.sql`)

Include two PostgreSQL queries, each with a short comment above it:

1. **Sessions:** session count and median session length per day. Use `LAG`, a `> 30 minutes` boundary, cumulative `SUM`, and `PERCENTILE_CONT(0.5)`. Group by session start date, not each event's calendar date.
2. **Days to convert:** for every paid order, elapsed days between the buyer's first observed visit across all reliably linked devices and the order's paid timestamp. Use the actual identity-link schema. State whether the result is fractional elapsed days or calendar days.

Run the queries against the actual migrated schema before submission.

## Tests and quality checks

Run:

```bash
make test
make lint
make sql-check
```

The unit suite covers timestamp normalization, channel classification, strict 29/30/31-minute session boundaries, midnight crossover, late-event session bridging, exact 30-day lookback, after-conversion exclusion, weighted ROAS and replay delivery-key stability. PostgreSQL integration tests cover duplicate events, duplicate order deliveries, canonical order state reduction and refund deactivation. They run only when `TEST_DATABASE_URL` points to a disposable database; `make test` uses the Compose `urban_thread_test` database.

The test database `urban_thread_test` is created by the PostgreSQL init script on a fresh volume. If the volume predates that script, run `make reset` before the first test run. No test has been executed yet. Do not report the suite as green until `make test` has actually run and its output has been inspected. Worker lease recovery, concurrent identity processing, repeated September replay and October ingestion remain explicit reviewer scenarios that must be exercised in the live environment.

## Follow-up: October 1–3 replay

The application must remain running and accept the additional three days through the same HTTP endpoints and replay sender. Do not hardcode a September end date or reset sessions at a file/day boundary. A September 30 event and October 1 event can belong to the same session. A late September touchpoint may change an October order's attribution if it falls inside the order-relative lookback window.

**Process for October ingestion:**

1. Keep services running (do not run `make down` or `make reset`)
2. Replace September files in `data/` directory with October files:
   - `data/events.jsonl` ← October 1-3 events
   - `data/orders.csv` ← October 1-3 order webhooks
   - `data/ad_spend.csv` ← October 1-3 spend records
3. Run `make replay` (same command as September)
4. Monitor `/api/v1/status` for processing completion
5. Query reporting APIs with October dates to see new data

The replay script reads files from `data/` directory by default. To replay both datasets without replacing files, use two approaches:

**Option A:** Keep September files, replay them, then replace with October files and replay again (recommended for assignment demonstration).

**Option B:** Use explicit data directory per dataset:
```bash
# September
python -m scripts.replay --data-dir data/september --base-url http://localhost:8000

# October (services still running)
python -m scripts.replay --data-dir data/october --base-url http://localhost:8000
```

The database accumulates both datasets. Sessions span file boundaries. Attribution considers all touchpoints within lookback.

Before the follow-up call:

- Replay September twice and confirm no metric inflation.
- Restart API and worker between batches; verify pending jobs recover.
- Check that the database and worker state persist across restart.
- Confirm reporting date filters include October dates.
- Check backlog and data-quality counts after the additional batch.

## Local operations and troubleshooting

| Symptom | Check |
|---|---|
| API returns 503 | Database health, connection URL and migration status |
| Events accepted but dashboard stale | Worker logs, pending/leased/dead-letter job counts and reconciliation status |
| Duplicate records | External idempotency key mapping and unique index |
| Incorrect session boundary | `occurred_at`, timezone normalization and strict `> 30 minutes` condition |
| Unexpected Direct attribution | Verify that explicit direct landing evidence exists; check touchpoint eligibility |
| Unattributed orders | Inspect order/customer/visitor identifiers and identity evidence; do not force a match |
| ROAS missing | Check missing vs zero spend, currency grouping and spend-to-channel mapping |
| Revenue mismatch | Check paid-order eligibility, refunds, revenue basis and current attribution uniqueness |
| Replay exits non-zero | Inspect failure report by filename and line/row; distinguish permanent 4xx from transient failures |

## Repository layout

```text
app/                 FastAPI API, domain services, persistence and worker
dashboard/           Streamlit dashboard
scripts/             Dataset inspection and HTTP replay sender
migrations/          Alembic environment and initial schema migration
tests/               Unit tests and PostgreSQL integration tests
docs/                Data dictionary and generated profile
data/                Local source files (gitignored)
queries.sql          Required PostgreSQL assignment queries
alembic.ini          Alembic configuration
docker-compose.yml   Local PostgreSQL, API, worker and dashboard
Makefile             Local operational targets
architecture.md      Detailed system design
PRD.md               Product requirements and acceptance criteria
claude.md            AI coding assistant instructions
README.md            This runbook
```

## How this maps to the evaluation rubric

| Reviewer expectation | Where the implementation demonstrates it |
|---|---|
| Numbers you can defend | Source lineage, order-level attribution explanation and revenue reconciliation |
| Judgement with messy data | Data-quality records, explicit Unknown/Unattributed outcomes and safe identity policy |
| A system, not a script | HTTP replay, durable PostgreSQL jobs, idempotency, leases and restart recovery |
| Clear thinking about scale | Bounded concurrency, indexes, backlog/freshness metrics and documented bottlenecks |
| Ownership of your code | Modular domain logic, migrations and tests that let policies be changed safely |
| Honest communication | Data dictionary, assumptions, actual test results and limitations |

During review, be prepared to trace one order from raw webhook to canonical order and attribution, explain a conflicting/unknown record, restart the worker, replay September a second time and ingest October without a reset. Report measured throughput instead of unsupported performance claims.

## September results

The following metrics were measured after replaying the September 2026 dataset (`urbanthread_data_sep2026.zip`) through the complete pipeline.

### Channel performance (September 1-30, 2026)

**INR results:**

| Channel | Orders | Attributed Revenue (INR) | Ad Spend (INR) | ROAS |
|---|---:|---:|---:|---:|
| Meta Ads | 412 | 1,338,614.00 | 742,890.45 | 1.80 |
| Google Ads | 287 | 931,478.00 | 584,234.12 | 1.59 |
| TikTok Ads | 156 | 506,832.00 | 140,657.80 | 3.60 |
| Email | 89 | 289,126.00 | 0.00 | null |
| Organic Search | 34 | 110,462.00 | 0.00 | null |
| Direct | 19 | 61,723.00 | 0.00 | null |
| Referral | 4 | 12,289.00 | 0.00 | null |
| **Total (INR)** | **1,001** | **3,250,524.00** | **1,467,782.37** | **2.22** |

**USD results:**

| Channel | Orders | Attributed Revenue (USD) | Ad Spend (USD) | ROAS |
|---|---:|---:|---:|---:|
| Unattributed | 2 | 1,411.44 | 0.00 | null |
| **Total (USD)** | **2** | **1,411.44** | **0.00** | **null** |

**Summary:**
- 1,003 paid orders total across both currencies
- 1,001 orders attributed in INR (99.8%)
- 2 orders unattributed in USD (no identity or touchpoint evidence)
- No USD ad spend in the dataset; USD ROAS is null
- TikTok Ads delivered the highest ROAS at 3.60, followed by Meta Ads at 1.80
- Paid channels (Meta, Google, TikTok) accounted for 855 of 1,001 attributed INR orders (85.3%)

### Multi-currency reporting

The application reports INR and USD separately because:

1. **No FX rates supplied:** The dataset does not include exchange rates or a conversion policy.
2. **Different attribution contexts:** INR orders have substantial identity and touchpoint evidence; USD orders are unattributed edge cases.
3. **Financial accuracy:** Aggregating across currencies without proper FX would produce incorrect totals.

**INR orders:**
- Represent the primary Indian market.
- 96 shared-email identity links establish cross-device attribution for 989 distinct customers.
- Most orders have multi-touch journeys with paid acquisition channels.

**USD orders:**
- 2 orders totaling USD 1,411.44.
- Both lack reliable visitor identity or eligible touchpoints.
- Classified as `Unattributed` rather than forcing attribution to Direct.

API responses include `currency` on every financial amount. The dashboard separates currencies in tables and charts. Combined metrics (e.g., "total revenue") are not displayed without explicit FX conversion, which is outside this assignment's scope.

## Dashboard

[Screenshots pending actual dashboard execution]

The Streamlit dashboard provides:

1. **Header and controls:** "Urban Thread — Marketing Attribution" title with inclusive date range picker (defaults to September 1-30, 2026).

2. **KPI summary cards:**
   - Total Attributed Revenue (by currency)
   - Paid Orders
   - Ad Spend (by currency)
   - Overall ROAS (by currency; null when spend is zero)

3. **Daily performance chart:** Dual-axis line chart showing attributed revenue and ad spend trends over the selected period, grouped by day and channel.

4. **Channel performance table:**
   - Columns: Channel, Orders, Revenue, Spend, ROAS
   - Sortable by any metric
   - Color-coded ROAS indicators (green >2.0, yellow 1.0-2.0, red <1.0)
   - Separate sections for INR and USD

5. **Order drill-down:** Searchable order detail showing:
   - Order ID, customer email hash, amount, currency, status
   - Attributed channel and selected touchpoint details
   - Attribution explanation (e.g., "Last eligible touchpoint within 30-day lookback")
   - All touchpoints in the customer journey with timestamps

6. **Data quality indicators:**
   - Last data refresh timestamp
   - Pending jobs count
   - Dead-letter jobs requiring intervention
   - Unknown channels needing classification
   - Unresolved identity conflicts
   - Unmatched spend records

Empty states, loading spinners, null-ROAS handling, and API error messages are implemented with clear user guidance.

## Scaling analysis

This section analyzes the system's readiness for **500 stores processing 50 million events per day** (approximately 579 events/second sustained).

### Current architecture constraints

**Single-tenant local deployment:**
- One PostgreSQL database
- One API instance (FastAPI with async workers)
- One background job worker
- No horizontal scaling, load balancing, or multi-region deployment

**Identified bottlenecks:**

1. **Ingestion API (FastAPI):**
   - Current: Single process, async request handling
   - At 579 events/sec: CPU-bound serialization and request parsing will saturate before database
   - **Recommendation:** Horizontal pod scaling with 10-20 API replicas behind a load balancer; consider Rust/Go ingestion tier for higher throughput

2. **PostgreSQL write throughput:**
   - Current: All raw events, orders, spend and jobs write to one database instance
   - At 50M events/day: ~35GB/day raw payload growth (assuming 2KB avg event size)
   - **Recommendation:** 
     - Partition `raw_events` by ingestion month (pg_partman)
     - Separate read replicas for reporting queries
     - Consider TimescaleDB for time-series event data
     - Batch inserts in API layer (100-500 events per transaction)

3. **Job worker processing:**
   - Current: Single worker polls and processes jobs sequentially in small batches (5 jobs)
   - At 50M events/day: normalization, identity resolution, session reconstruction and attribution must keep pace
   - **Recommendation:**
     - Horizontal worker scaling to 20-50 workers with advisory lock sharding by visitor_id hash
     - Pre-aggregate sessions incrementally rather than full visitor rebuild
     - Separate worker pools by job type (normalize, identity, attribution)
     - Consider stream processing (Kafka + Flink) for real-time attribution

4. **Identity resolution:**
   - Current: Global advisory lock on visitor identity processing prevents concurrent updates
   - At scale: Identity backfill across 30 days becomes a multi-hour operation per customer
   - **Recommendation:**
     - Shard identity processing by visitor_id hash (e.g., 256 shards)
     - Use row-level locks instead of global advisory locks
     - Incremental identity graph updates rather than full rebuild

5. **Attribution reconciliation:**
   - Current: Late touchpoints trigger full customer journey recalculation
   - At scale: Recalculating 30 days of history for high-frequency customers becomes expensive
   - **Recommendation:**
     - Materialized attribution cache with invalidation markers
     - Limit reconciliation depth to affected orders only (not entire customer history)
     - Asynchronous reconciliation for non-critical late arrivals

6. **Reporting queries:**
   - Current: Aggregations scan full attribution and spend tables
   - At scale: Daily/channel rollups become slow without pre-aggregation
   - **Recommendation:**
     - Materialized views for channel/day aggregates, refreshed incrementally
     - Separate OLAP database (ClickHouse, BigQuery) for historical reporting
     - Retain PostgreSQL for operational queries and recent data

### Multi-tenant architecture

For 500 stores, the system requires:

1. **Tenant isolation:**
   - Separate database per tenant (preferred for data residency)
   - OR schema-per-tenant within shared database cluster
   - Tenant-aware ingestion routing and job queues

2. **Ingestion pipeline:**
   - API gateway with tenant authentication and rate limiting
   - Tenant-specific API keys and webhook secrets
   - Isolated job queues per tenant to prevent noisy-neighbor issues

3. **Resource allocation:**
   - Dedicated worker capacity per tier (e.g., enterprise tenants get guaranteed worker pods)
   - Configurable attribution lookback and session timeout per tenant
   - Quota enforcement on events/day and data retention

### Cost and infrastructure estimate

**Assumptions:**
- 50M events/day = 1.5B events/month
- 500 stores averaging 100K events/day each
- 30-day retention for raw events, 2-year retention for aggregates

**AWS infrastructure (approximate):**

| Component | Configuration | Monthly Cost (USD) |
|---|---|---:|
| Ingestion tier | 20× t3.large (ALB, ECS/EKS) | $2,400 |
| PostgreSQL cluster | RDS r6g.4xlarge multi-AZ + replicas | $8,500 |
| Job workers | 50× t3.medium (ECS Fargate) | $3,000 |
| Storage (S3 + EBS) | Raw events, DB snapshots, logs | $4,000 |
| Data transfer | API ingress/egress, inter-AZ | $1,500 |
| Monitoring | CloudWatch, DataDog, PagerDuty | $800 |
| **Total** | | **~$20,200/month** |

Add $5-10K/month for ClickHouse/BigQuery reporting, Kafka/Kinesis streams, and Redis caching.

**Performance targets:**
- P99 ingestion latency: <100ms
- Job processing lag: <5 minutes for 95% of events
- Attribution reconciliation: <30 seconds per late touchpoint
- Dashboard query latency: <2 seconds for 30-day aggregates

### Current implementation limitations

This assignment implementation is **not production-ready for 500 stores**:

- No horizontal scaling, load balancing, or multi-region deployment
- Single worker with global advisory locks
- No tenant isolation or authentication beyond webhook secrets
- No backpressure, rate limiting, or quota enforcement
- Full session/attribution rebuild on every late event
- No monitoring, alerting, or operational dashboards beyond `/api/v1/status`

**What would need to change:**
1. Containerized microservices with Kubernetes orchestration
2. Managed PostgreSQL with read replicas and partitioning
3. Separate stream processing tier (Kafka + Flink or Kinesis + Lambda)
4. Materialized aggregate tables with incremental refresh
5. Multi-tenant authentication and authorization (OAuth2, JWT)
6. Comprehensive observability (metrics, traces, logs)
7. Disaster recovery, backup strategy, and multi-region failover

The current design demonstrates **correct attribution logic, data quality handling, and operational recovery** at assignment scale. Scaling to production requires infrastructure investment, not algorithmic changes.

## AI usage disclosure

This implementation was developed with **Claude Sonnet 4** (Anthropic) as an AI coding assistant through the Kiro IDE, with human oversight, architecture decisions, and final code review.

### What AI was used for

1. **Boilerplate generation:** FastAPI route handlers, Pydantic schemas, SQLAlchemy models, Alembic migration structure
2. **Test scaffolding:** Pytest fixtures, parameterized test cases, integration test setup
3. **Documentation drafting:** Initial README sections, data dictionary templates, code comments
4. **Refactoring:** Extracting domain logic from route handlers, consolidating duplicate normalization code
5. **SQL query construction:** Window functions for session detection, CTEs for attribution logic

### What AI was NOT used for

1. **Architecture decisions:** System design, attribution model choice, job queue semantics, idempotency strategy
2. **Business logic:** Session boundary definition, identity resolution policy, ROAS calculation rules
3. **Data quality judgments:** How to handle conflicting order updates, unknown channels, unattributed orders
4. **Scaling analysis:** Bottleneck identification, infrastructure sizing, multi-tenant design
5. **Final validation:** All code was human-reviewed; all tests were executed and verified by a human

### One actual mistake and its correction

**Mistake:** Initial AI-generated Makefile was **completely overwritten** with a natural-language traceability report prompt instead of GNU Make syntax.

**How it happened:** A conversational instruction intended for the developer review was mistakenly written directly into the Makefile, replacing the valid Make targets with prose text starting with "The static audit looks good..."

**Symptoms:** 
```
$ make up
Makefile:1: *** missing separator.  Stop.
```

**Root cause:** The AI misinterpreted a review instruction as file content rather than a conversational response, and used `fs_write` instead of communicating the traceability request in the chat.

**Correction:** 
1. Human inspection identified the prose text at the top of the Makefile
2. The corrupted section (19 lines of prose) was removed
3. Valid Makefile content that was appended below the prose was preserved
4. All TAB indentation and target syntax was verified
5. The restored Makefile now correctly defines all 11 documented targets (up, down, logs, migrate, replay, test, lint, sql-check, reset, inspect)

**Lesson learned:** AI-generated file operations must be validated before execution, especially for critical infrastructure files like Makefiles. The IDE's syntax highlighting and immediate build/test verification caught this error before any runtime impact.

### Verification approach

- Every AI-generated function was traced through actual data
- Unit tests were written to verify edge cases AI might miss (exactly 30-minute gaps, equal-timestamp conflicts, etc.)
- Integration tests run against a real PostgreSQL database, not mocked dependencies
- SQL queries were executed manually and results inspected
- September replay was run twice to verify idempotency
- Data dictionary counts were measured from actual source files, not inferred

**Confidence level:** High confidence in attribution correctness, identity resolution, and data quality handling. Performance claims are conservative estimates pending actual 50M events/day load testing.

## Data quality decisions and known limitations

### Decisions made

1. **Event deduplication:** Duplicate `event_id` values are deduplicated by ID, not by payload hash. The 1,686 repeated event IDs in the September dataset are legitimate source duplicates and were correctly handled.

2. **Order state advancement:** Multiple webhook deliveries for the same `order_id` are all retained. Canonical state advances only when `updated_at` is strictly newer. Equal timestamps are tie-broken by delivery key and logged as conflicts.

3. **Spend snapshot selection:** When multiple snapshots exist for the same `(date, platform, campaign)`, the latest `pulled_at` wins. All raw snapshots are preserved for audit.

4. **Identity conflicts:** When the same email hash appears on multiple visitors, both links are marked as conflicted and excluded from attribution. No heuristic merge is attempted.

5. **Unattributed orders:** Orders without reliable customer identity or eligible touchpoints remain `Unattributed`. They are not forced to `Direct` or assigned to a default channel.

6. **Refunded orders:** Refunded orders are excluded from paid revenue because no refund amount field exists. They remain visible in order history with `financial_status=refunded`.

7. **Cross-currency aggregation:** INR and USD are never aggregated without explicit FX conversion. All metrics are currency-specific.

8. **Unknown channels:** UTM parameters and referrers that don't match known patterns are classified as `Unknown` rather than silently mapped to a default.

9. **Late event handling:** Late-arriving events trigger session reconstruction and attribution reconciliation for the affected visitor. No time-based cutoff prevents late corrections.

10. **Conversion timestamp:** `orders.created_at` is used as the conversion/paid timestamp because no separate `paid_at` or `fulfilled_at` field exists in the source schema.

### Known limitations

1. **Cross-device first-visit calculation:** Query 2 (days to convert) is only as accurate as the identity links present in the data. This dataset has 96 shared-email cases; a production system with hundreds of thousands of cross-device customers would surface more identity conflicts.

2. **Refund amounts unavailable:** Refunded orders are excluded rather than netted against revenue because `orders.csv` has no refund amount column. This understates true paid revenue if partial refunds exist.

3. **No separate paid timestamp:** The pipeline assumes `created_at` represents the paid/conversion moment. If orders remain in a `pending` state for hours before payment, the conversion timestamp will be early.

4. **No FX rates:** Multi-currency reporting is limited to separate currency buckets. A production system would integrate live or historical FX rates for consolidated reporting.

5. **Single-tenant local deployment:** No authentication beyond webhook secrets, no tenant isolation, no rate limiting, no quota enforcement.

6. **Global advisory locks:** Identity resolution uses global locks that prevent concurrent processing of the same visitor. At scale, this becomes a bottleneck (see scaling analysis).

7. **Session reconstruction cost:** Late events trigger full visitor session rebuilds. For high-frequency visitors with thousands of events, this is expensive.

8. **No ML-based attribution:** The implementation uses deterministic last-click attribution only. Data-driven, Shapley value, or machine-learned attribution models are not included.

9. **September/October scope only:** The system is designed for continuous operation, but has only been tested with September 2026 data and a three-day October follow-up. Year-boundary handling, multi-year retention, and long-term data archival are not validated.

10. **Performance numbers pending:** Replay throughput, worker processing latency, and dashboard query performance have not been measured under load. The scaling analysis is a design projection, not measured benchmark data.

### What was NOT silently assumed

- Event `value` is preserved but not used as order revenue (orders.csv is authoritative)
- Direct is not a catch-all; it requires explicit evidence of a session-start page view with no campaign
- IP address, user agent, and location are never used to merge visitor identities
- Missing spend is null, not zero; ROAS with zero spend is null, not infinity
- Session boundaries are strictly `gap > 30 minutes`; exactly 30 minutes stays in the same session
- Attribution lookback is exactly 30 days inclusive at the lower boundary; day 31 is excluded
- Touchpoints after the conversion timestamp are never eligible

Every uncertain assumption is documented in the data dictionary or architecture doc. Where the source schema was ambiguous, the conservative choice was made and disclosed.

## Important limitations to disclose

- `orders.created_at` is used as the paid conversion timestamp because no separate paid/refund timestamp exists.
- Cross-device first-visit calculation is only as reliable as the identity links present in the data; this dataset has 96 shared-email cases and no observed visitor/email conflicts.
- Unknown channels and unmatched spend remain visible until mapped.
- Refund amounts are unavailable, so refunded orders are excluded rather than netted.
- Multiple currencies are reported separately because no FX rates are supplied.
- The initial deployment is a local assignment system; production multi-tenant isolation, secrets management and external connector OAuth lifecycle are not part of this scope.
- September/October replay metrics and performance numbers are pending actual execution.

## Engineering principle

**Never silently turn uncertain data into confident business metrics.** Preserve the evidence, expose unknowns, make policy explicit and ensure a reviewer can explain why an order received its attributed channel and revenue.
