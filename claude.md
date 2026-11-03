# CLAUDE.md — Engineering instructions for Urban Thread Attribution

This file defines how an AI coding assistant (including Claude Code) should work in this repository. Follow it together with `PRD.md`, `architecture.md` and `README.md`.

## 1. Mission

Build a correct, explainable first-party attribution pipeline for the Urban Thread backend assignment. The product must ingest website events, order webhooks and daily campaign spend through HTTP; persist raw evidence; process it asynchronously; reconstruct sessions and identity; attribute paid-order revenue; expose reporting APIs; and provide a modestly polished dashboard.

**Correctness and data provenance take precedence over feature count, clever abstractions and UI polish.** Do not claim ten years of experience or write documentation that implies unimplemented functionality is complete. Implement to a strong production-engineering standard and explain actual trade-offs.

## 2. Read these sources first

Before making changes, read:

1. `PRD.md` — product goals and acceptance criteria.
2. `architecture.md` — architectural decisions, domain policies and schema intent.
3. `README.md` — local setup and operational contract.
4. The actual assignment-provided dataset README/data dictionary.
5. A representative sample and a programmatic profile of every source file.

The assignment says the input archive contains approximately:

- `events.jsonl`: ~56,000 website events.
- `orders.csv`: ~1,150 order webhook deliveries (not necessarily 1,150 unique orders).
- `ad_spend.csv`: ~230 daily campaign-spend records.

Treat these counts as approximate until measured. The exact source fields were not available when these design documents were drafted. **Do not invent field names or assume identifiers exist.**

## 3. Mandatory data-inspection gate

Before implementing source adapters or finalizing model columns:

- Inspect file encodings, delimiters, JSONL validity and row counts.
- Print exact column/key names and representative redacted records.
- Profile nulls, distinct values, timestamp formats/timezones and duplicate candidate IDs.
- Identify the actual event ID, visitor ID, customer ID, webhook ID, order ID and spend record/campaign keys.
- Identify order states, payment/conversion timestamp and refund/amount semantics.
- Determine how paid, organic, email, referral and direct traffic are represented.
- Verify whether customer identity can link multiple devices.
- Record the result in `docs/data-dictionary.md` (create `docs/` if needed) and update schema mappings.

Do not proceed with assumptions hidden in code. If the dataset cannot establish a relationship, preserve the uncertainty and document the limitation.

## 4. Non-negotiable engineering principles

### 4.1 Preserve source evidence

- Raw event and webhook payloads are append-only.
- Store `received_at` separately from source `occurred_at`, `paid_at` and `source_updated_at`.
- Do not replace invalid timestamps with `now()`.
- Do not edit raw payloads to fix a normalization bug; change the adapter and rebuild derived state.
- Unknown fields and unknown channel values should be preserved when the record is structurally usable.

### 4.2 Idempotency

- Use database uniqueness constraints for trustworthy external event IDs and webhook delivery IDs.
- Never deduplicate order webhooks solely by `order_id`; one order can legitimately have multiple updates.
- If an external ID is missing, generate an internal UUID. Do not use a risky content fingerprint unless the data contract proves it is safe.
- Stable sender IDs must remain stable across retries.
- Worker effects must be idempotent. Do not promise exactly-once delivery or execution; implement at-least-once processing with idempotent results.

### 4.3 Time correctness

- Journey ordering uses source occurrence time, not database insertion order or HTTP completion order.
- Session threshold is strictly `gap > 30 minutes`; exactly 30 minutes remains in the same session.
- Events after an order conversion timestamp can never receive attribution credit.
- The default attribution lookback is 30 days, inclusive at the lower boundary, but make it configurable.
- Never reject historical source events simply because they are old relative to the current date.
- Use a configured reporting timezone and document it. Do not mix UTC timestamps with local dates implicitly.

### 4.4 Identity correctness

- Link visitor IDs across devices only with explicit, reliable customer evidence.
- Model identity links as temporal and retain evidence.
- Do not merge users based on IP, user-agent, location or behavioural similarity.
- A conflicting identity mapping must remain visible and unresolved until a rule supported by evidence resolves it.
- A valid order with no reliable identity must remain in reports as `Unattributed`; do not drop it.

### 4.5 Financial correctness

- Use `NUMERIC`/`Decimal` or integer minor units; never floating point for stored money.
- Keep currency with every financial amount.
- Do not aggregate across currencies without an explicit FX policy.
- Do not describe an amount as gross/net/paid revenue until its source semantics are verified.
- Do not fabricate zero spend where a spend record is missing.
- ROAS is `SUM(attributed revenue) / SUM(spend)`, not an average of ratios.
- ROAS is null when spend is zero or missing; positive spend with zero revenue yields zero ROAS.

### 4.6 Deterministic attribution

- Primary model is literal `last_click`, including Direct only when a direct landing touchpoint is explicitly evidenced.
- A 30-day lookback is configurable and must be exposed/documented.
- Only eligible acquisition/referral/campaign touchpoints can compete. A generic product view or add-to-cart is not automatically a marketing touchpoint.
- `Direct` is not a catch-all for missing identity or unknown tracking; use `Unattributed` when evidence is insufficient.
- `last_non_direct` may be implemented as an optional strategy, but do not silently substitute it for the assignment's literal last-click request.
- Persist lineage: order, identity, touchpoint/event, channel, model, policy version, conversion time, lookback, attributed amount/currency, reason and calculation time.
- Do not use an LLM to decide attribution.

## 5. Architecture boundaries

Keep a modular monolith. Use small, focused modules for:

- ingestion and API contracts
- raw persistence and repositories
- normalization/channel mapping
- identity resolution
- session reconstruction
- touchpoint extraction
- order state and revenue eligibility
- attribution policy/strategies
- PostgreSQL job worker and reconciliation
- reporting SQL/API
- Streamlit presentation

Domain services must not import FastAPI, Streamlit or SQLAlchemy. Use SQLAlchemy 2.0 for transactional persistence and explicit PostgreSQL SQL for window functions/reporting when SQL is clearer.

Do not add Kafka, Redis, Celery, Airflow, Kubernetes, microservices or a vector database unless a measured requirement demands one. Keep dependencies small.

## 6. Ingestion contract

Mandatory endpoints:

- `POST /collect`
- `POST /webhooks/orders`

Supporting endpoint required to keep spend on the same auditable ingestion path:

- `POST /webhooks/ad-spend`

For each accepted request:

1. Parse and validate the source contract.
2. Preserve the original payload.
3. Apply only safe idempotency checks.
4. Insert the raw record and durable processing job in the same database transaction.
5. Return `202 Accepted` only after commit.

Use consistent errors (`400`, `401`, `413`, `422`, `429`, `503`). Do not acknowledge an event when storage/job enqueue failed. Authenticate order webhooks with HMAC if supported by the source/replay contract; secrets come from environment configuration.

## 7. Worker requirements

- Use PostgreSQL jobs with atomic claim/lease updates and `FOR UPDATE SKIP LOCKED`.
- Recover jobs after lease expiry.
- Retry only transient failures with exponential backoff and jitter.
- Move permanent/poison records to dead-letter state with the error and source reference.
- Serialize rebuilding for the same visitor/customer identity using PostgreSQL locking.
- Make derived writes transactional and idempotent.
- Ensure a new event arriving during reconciliation cannot be lost when a worker clears a dirty flag; use a generation/version check or equivalent safe design.
- Keep job data in PostgreSQL so restarts do not lose work.

## 8. Replay sender requirements

Implement a Python HTTPX/asyncio sender. It must:

- Read `events.jsonl`, `orders.csv` and `ad_spend.csv` from local files.
- Submit records to HTTP endpoints; never load files directly into database tables.
- Preserve each file's submission sequence while using bounded concurrency (default 15, configurable 10–20).
- Keep event/webhook IDs stable across retries.
- Retry timeouts, network failures, 5xx and 429 with backoff; respect `Retry-After`.
- Avoid retrying permanent 4xx errors without intervention.
- Print accepted/duplicate/retried/rejected/failed totals and save a failure report with filename and line/row number.
- Exit non-zero when unrecoverable failures remain.

The backend must not rely on request arrival/completion order. Use `occurred_at` and trustworthy source order versions/timestamps.

## 9. SQL assignment

Create `queries.sql` in PostgreSQL dialect with a short comment above each query.

### Query 1

- Sessionize by visitor, not by customer.
- Use `LAG` over `ORDER BY occurred_at, stable_event_id`.
- Mark a new session on `gap > INTERVAL '30 minutes'`.
- Use cumulative `SUM` to number sessions.
- Calculate session start/end/duration and then daily `COUNT` + `PERCENTILE_CONT(0.5)`.
- Bucket by session start local date; sessions crossing midnight count once.
- A single-event session has zero duration.

### Query 2

- For each paid order, find the buyer's earliest observed visit across all reliably linked visitor IDs.
- Use the actual identity-link model, not just `orders.visitor_id`.
- Calculate elapsed days explicitly (fractional elapsed days unless the assignment reviewer clarifies calendar-day difference).
- If the source cannot link devices, document the limitation; do not infer identities.

Do not write these queries against hypothetical column names and leave them unverified. Match them to the implemented migrations and dataset mapping.

## 10. Reporting and dashboard requirements

Expose channel and daily reporting endpoints for date ranges. Always return attribution model, lookback, reporting timezone and currency. Include Unattributed/Unknown buckets so revenue is never hidden. Spend and revenue use their own documented date bases. Keep the dashboard read-only and API-driven.

Dashboard should be modestly polished: title/brand, date filter, KPI row, daily chart, channel table, order drill-down and data-quality/freshness indicators. Prefer clear labels and number formatting over visual effects.

## 11. Testing expectations

Use unit tests for pure domain rules and integration tests against real PostgreSQL for constraints, transactions, worker leases and concurrency. At minimum test:

- duplicate event and webhook deliveries
- no unsafe deduplication when event ID is absent
- raw event + job atomicity
- out-of-order events and order updates
- two devices linked by explicit customer identity
- conflicting identities and shared-device boundaries
- 29/30/31-minute session gaps
- session crossing midnight
- late event bridging sessions
- touchpoint at conversion time and after conversion
- exact 30-day lookback boundary
- late touchpoint revises attribution
- unpaid/cancelled/refunded order policy
- multiple currencies
- missing spend versus zero spend
- weighted aggregate ROAS
- worker crash/lease reclaim/dead-letter
- two workers contending for one identity
- September replay twice without metric changes
- October 1–3 ingestion with the same running system
- restart between replay batches

Do not claim a test passes unless it was actually run. Include the exact test command and actual result in completion notes.

## 12. Code quality and implementation style

- Add type annotations to public functions and domain interfaces.
- Prefer dataclasses/Pydantic models and composition over complex inheritance.
- Keep functions cohesive and avoid one giant pipeline function.
- Use `Decimal` for monetary calculations.
- Use UTC-aware datetime objects internally; convert to reporting timezone only for date buckets and display.
- Use structured logs with correlation IDs. Do not log secrets or full payloads containing unnecessary personal information.
- Use transactions around logically consistent derived writes.
- Add indexes only for known access patterns.
- Add Alembic migrations for schema changes; never use runtime auto-create in production paths.
- Keep `.env`, source data, local DB volumes and replay output out of git.
- Add comments for policy decisions and non-obvious edge cases, not obvious code.

## 13. Reviewer-rubric alignment

Implementation and documentation should make it easy to demonstrate all six signals:

- **Defensible numbers:** raw lineage, order-level attribution explanation, currency-aware revenue and reconciliation queries.
- **Judgement with messy data:** explicit data-quality states; unknown/unresolved is safer than guessing.
- **System, not script:** HTTP-only replay, persistent jobs, migrations, retries, worker restart recovery and October ingestion.
- **Scale awareness:** bounded concurrency, targeted indexes, measured worker backlog/query latency and a reasoned bottleneck/optimization explanation.
- **Code ownership:** small modules and tests; never introduce generated code or abstractions that cannot be explained and changed.
- **Honest communication:** document verified fields, assumptions, actual test outcomes, remaining limitations and unimplemented features.

Do not add performance numbers unless they have been measured against the actual dataset and environment.

## 14. Definition of done

A task is not done merely because the happy path runs. Before declaring completion:

- [ ] Source fields are mapped from the actual dataset and documented.
- [ ] Migrations create the schema from an empty database.
- [ ] API persists raw records and durable jobs atomically.
- [ ] Sender submits all three files through HTTP with bounded concurrency.
- [ ] Replaying the same records does not inflate counts or revenue.
- [ ] Worker can recover from restart/expired lease.
- [ ] Sessions obey the strict 30-minute rule and can be rebuilt after late events.
- [ ] Identity is evidence-based and conflicts are visible.
- [ ] Attribution is deterministic, auditable and recalculates after late data.
- [ ] Financial values preserve precision and currency.
- [ ] Reporting APIs produce correct date/channel aggregates and ROAS.
- [ ] `queries.sql` runs against the actual schema.
- [ ] Dashboard reads APIs and exposes data freshness/quality.
- [ ] Tests cover P0 failure modes and have actually been executed.
- [ ] README setup/replay commands work from a clean checkout.
- [ ] No secrets or source data are committed.

## 15. Recommended implementation sequence

1. Inspect and profile the ZIP; document the data dictionary.
2. Finalize canonical mapping and unresolved assumptions.
3. Define domain models and migrations.
4. Implement ingestion endpoints with raw persistence and idempotency.
5. Implement the HTTP replay sender and verify it uses endpoints only.
6. Implement normalization and channel mapping.
7. Implement identity resolution and conflict policy.
8. Implement session reconstruction and tests.
9. Implement order-state reduction and financial eligibility.
10. Implement touchpoints, literal last-click and audit lineage.
11. Implement late-data reconciliation and durable worker recovery.
12. Implement reporting endpoints and SQL assignment.
13. Implement the dashboard.
14. Run repeat-replay and October follow-up simulations; finish docs and clean-checkout verification.

## 16. How to respond when blocked

When source data or assignment wording is ambiguous:

1. Show the exact field/row or requirement that is ambiguous.
2. State what can and cannot be concluded from the available evidence.
3. Choose the least-assumptive safe behaviour (usually preserve and mark unresolved).
4. Record the assumption in the data dictionary and README.
5. Add a test that protects the chosen policy.

Never fabricate source fields, expected metrics, business outcomes, completed tests or production guarantees.
