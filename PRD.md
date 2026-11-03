# Product Requirements Document

## Urban Thread — First-Party Attribution Pipeline

**Document status:** Proposed baseline for implementation  
**Version:** 1.0  
**Primary user:** Urban Thread marketing / ecommerce analyst  
**Technical owner:** Backend engineering  

---

## 1. Product summary

Urban Thread wants to own its marketing measurement data instead of depending on third-party analytics. The system will ingest first-party website events, order-system webhooks and daily advertising spend, connect visitor journeys to real orders, assign revenue to marketing channels using last-click attribution, and expose trusted results through APIs and a lightweight dashboard.

This is a backend/data-correctness assignment. The dashboard should be clear and slightly polished, but the principal product value is trustworthy metrics under duplicates, out-of-order records, changing order states, incomplete identity and late-arriving data.

## 2. Problem statement

Marketing and finance teams cannot make sound budget decisions when revenue is attributed using incomplete or inconsistent tracking. Typical failures include duplicated events, webhook retries, visitor IDs that do not directly match buyer IDs, late-arriving events, ad-spend mapping gaps and reporting logic that treats missing data as zero.

The system must make data provenance and uncertainty visible. A record can be accepted successfully and still be unusable for a particular metric; the platform must distinguish these states instead of producing confident but unsupported numbers.

## 3. Goals

1. Accept website events and order webhook updates over HTTP as a live system would.
2. Accept campaign/day advertising spend through a repeatable ingestion path.
3. Replay the supplied files through the HTTP endpoints with bounded concurrency; never load source files directly into database tables.
4. Preserve raw source records and deduplicate only where the source provides a trustworthy key.
5. Reconstruct visitor sessions using a 30-minute inactivity threshold.
6. Resolve visitor/customer identity only where reliable evidence exists.
7. Attribute eligible paid-order revenue using literal last-click with a documented, configurable attribution window.
8. Recalculate historical sessions and attribution when late events or identity changes arrive.
9. Provide date-filtered channel/day reporting for orders, revenue, spend and ROAS.
10. Provide a small dashboard with enough polish to make the results easy to inspect.
11. Support the later October 1–3 data batch without code changes, a reset or a special date path.
12. Demonstrate sound SQL using PostgreSQL window functions and identity-aware conversion-time queries.

## 4. Non-goals

- Replacing a full commercial analytics platform.
- Building a polished customer-facing product UI.
- Real live integrations with Meta, Google, TikTok, Shopify or payment providers beyond the provided file replay and webhook-shaped endpoints.
- Building a general-purpose ETL/orchestration platform.
- Introducing Kafka, Redis, Celery, Airflow, Kubernetes or microservices without a measured need.
- Machine-learning or LLM-based identity matching or attribution.
- Automatic FX conversion without trustworthy exchange-rate data.
- Profit, contribution margin, CAC or POAS calculations unless the source contains the required cost and customer-acquisition data.
- Claiming exactly-once delivery or processing.

## 5. Users and primary workflows

### Persona A — Marketing analyst

Needs to understand which channels drive paid-order revenue, compare spend and ROAS over a selected date range, inspect day-by-day movement and identify missing attribution/spend data.

**Primary workflow:** Open dashboard → choose date range → inspect KPI cards → compare channel table → inspect daily trend → drill into an order's attribution explanation.

### Persona B — Backend engineer / reviewer

Needs confidence that ingestion is durable, retries are safe, source data is traceable, identity resolution is conservative and historical metrics change correctly when late data arrives.

**Primary workflow:** Start services → replay source data → inspect ingestion/worker status → query reporting APIs → trigger or observe reconciliation → replay data again without double-counting.

### Persona C — Order-system producer

Needs to post order lifecycle updates without having webhook retries or out-of-order delivery create duplicate orders or incorrect revenue.

**Primary workflow:** Send order webhook → receive a durable acknowledgement → retry safely if the response is lost → update the same canonical order as its state changes.

## 6. Input data inventory and constraints

The assignment describes the following inputs. Volumes are approximate and must be verified against the archive.

| Input | Assignment description | Approximate size | Important interpretation |
|---|---|---:|---|
| `events.jsonl` | Website events captured by the tracking pixel, in server-received order | ~56,000 | File order is not necessarily event occurrence order |
| `orders.csv` | Order webhooks, one row per webhook delivery/update | ~1,150 | Many rows may refer to one canonical order |
| `ad_spend.csv` | Daily campaign spend from Meta, Google and TikTok | ~230 | Spend is a dated fact and must be mapped to a reporting channel |

The exact source fields, identifier semantics, timestamp timezone, order-state model and revenue basis must be confirmed from the ZIP README and data profiling before implementation. Do not assume the approximate sizes are exact counts.

## 7. Product scope and functional requirements

### FR-01 — Website event ingestion

**Requirement:** Expose `POST /collect` to accept one event per request.

**Expected behaviour:**
- Validate the actual source schema through an adapter.
- Preserve the raw request payload and source metadata.
- Record server receipt time separately from event occurrence time.
- Deduplicate by a trustworthy external event ID when present.
- If an ID is missing, generate an internal ID; do not apply unsafe fingerprint deduplication.
- Persist the raw row and processing job atomically.
- Return success only after durable commit.

**Acceptance:** A repeated event with the same trusted source ID does not create a second logical event. A malformed payload receives a clear 4xx response. Unknown optional fields do not silently disappear from raw storage.

### FR-02 — Order webhook ingestion

**Requirement:** Expose `POST /webhooks/orders` for order lifecycle updates.

**Expected behaviour:**
- Preserve every distinct webhook delivery.
- Deduplicate deliveries by webhook event/delivery ID when one exists.
- Maintain one canonical order per source order ID.
- Apply updates only when newer according to a reliable source version or source update timestamp.
- Do not count webhook deliveries as orders.
- Retain unpaid, cancelled and refunded history even when excluded from paid revenue.
- Authenticate with HMAC when the source/replay contract supports it.

**Acceptance:** Replaying one webhook does not create another delivery or inflate revenue. Multiple legitimate updates modify one canonical order. An older update cannot overwrite a newer status.

### FR-03 — Advertising spend ingestion

**Requirement:** Make the supplied daily spend records available to reporting through a repeatable ingestion path. Implement `POST /webhooks/ad-spend` as a supporting endpoint.

**Expected behaviour:**
- Preserve original spend payloads and source identifiers.
- Normalize spend date, platform, campaign key/name, amount and currency.
- Deduplicate only using a verified source ID or verified natural key.
- Map campaigns/platforms to channels transparently.
- Keep unmatched spend visible for reconciliation.

**Acceptance:** Spend records can be replayed without duplication. Missing spend is not represented as zero. Spend mapped only at channel level is not silently allocated to individual campaigns.

### FR-04 — HTTP replay sender

**Requirement:** Provide a small sender script that reads the supplied files and submits records to the API in source-file order with concurrency.

**Expected behaviour:**
- Use HTTP, never direct database imports.
- Preserve source sequence when scheduling each file's records.
- Default to 15 concurrent requests; allow 10–20/configurable concurrency.
- Retry network failures/timeouts, 429 and retryable 5xx with bounded exponential backoff and jitter; respect `Retry-After`.
- Do not retry permanent schema/authentication errors by default.
- Reuse the same event/webhook idempotency identity on retry.
- Report accepted, duplicate, retried, rejected and failed totals with source row references.

**Acceptance:** Running the script processes all available files and reports its final status. Re-running it does not inflate logical event/order/spend counts or financial metrics.

### FR-05 — Event normalization and data quality

**Requirement:** Transform source-specific records into a stable internal model without altering raw evidence.

**Expected behaviour:**
- Preserve source field values.
- Normalize timestamps to timezone-aware values.
- Flag invalid timestamps, missing identity keys, unknown event types and unknown marketing values.
- Separate invalid data from unknown-but-valid values.
- Maintain a source-to-channel mapping that is deterministic and auditable.

**Acceptance:** An unknown source or medium is preserved as `Unknown`/unresolved, not guessed as paid. Invalid timestamps are rejected or quarantined, never replaced with current time.

### FR-06 — Identity resolution

**Requirement:** Connect visitor journeys to customer/order identities using reliable evidence.

**Expected behaviour:**
- A visitor is a device/browser identity; a customer is a buyer/account identity.
- Cross-device linkage uses a trusted common customer ID or explicit identity event.
- Links retain evidence and effective time.
- Conflicting links are surfaced; the system does not silently overwrite mappings.
- IP, user-agent, location and behaviour similarity are not identity evidence.
- Orders without reliable identity remain valid and are classified as `Unattributed`.

**Acceptance:** Two visitor IDs with the same verified customer identity can contribute to that customer's journey. Two anonymous visitors are not merged. A shared device with conflicting customer identities does not automatically transfer all historical events to the latest account.

### FR-07 — Session reconstruction

**Requirement:** Convert visitor events into sessions using 30-minute inactivity.

**Rules:**
- Sessionize per visitor.
- Sort by event occurrence timestamp and stable tie-breaker.
- Start a new session when the gap is strictly greater than 30 minutes.
- Exactly 30 minutes remains in the same session.
- Sessions can cross midnight and source-file boundaries.
- Single-event session duration is zero.
- Late events cause affected sessions to be rebuilt.

**Acceptance:** Tests cover 29-minute, exactly 30-minute and 31-minute gaps; same-timestamp events; session crossing midnight; late event bridging sessions; and single-event duration.

### FR-08 — Marketing touchpoint generation

**Requirement:** Identify eligible acquisition/campaign/referral touchpoints from normalized events.

**Expected behaviour:**
- Do not treat every page view, product view or add-to-cart as a new marketing touchpoint.
- Use actual UTM, click ID, referrer and event semantics available in the source.
- Distinguish paid search, paid social, email, organic, referral, direct and unknown only where evidence supports it.
- Preserve original source/medium/campaign values.

**Acceptance:** `google` alone is not automatically classified as Google Ads. Unknown campaign values remain inspectable.

### FR-09 — Order eligibility and revenue policy

**Requirement:** Define which canonical orders count as paid orders and how revenue is measured.

**Expected behaviour:**
- Use a verified paid/conversion timestamp, preferably `paid_at` where supplied.
- Exclude unpaid/pending/failed orders from paid revenue.
- Exclude cancelled orders according to the documented policy while retaining state history.
- Apply refunds only when supported by reliable source data.
- Document whether revenue is gross, paid, net of discount/refund, tax-inclusive or tax-exclusive after inspecting field semantics.
- Preserve currency; do not implicitly convert or combine currencies.

**Acceptance:** Every monetary value has a currency; repeated order webhooks do not count a second order; a refunded/cancelled order follows the documented policy.

### FR-10 — Last-click attribution

**Requirement:** Credit each eligible paid order to the latest eligible marketing touchpoint and document the attribution window.

**Primary policy:** Literal last-click (Direct can win when explicitly evidenced), 30-day lookback by default, both temporal boundaries inclusive.

**Expected behaviour:**
- Use event occurrence time and the order conversion/paid timestamp.
- A touchpoint after payment is never eligible.
- The selected touchpoint must be within the 30-day lookback.
- Equal timestamps use a trustworthy sequence where available; otherwise deterministic tie-break and ambiguity metadata.
- Every order is attributed independently.
- If no reliable identity/touchpoint exists, preserve `Unattributed` rather than inventing Direct.
- Persist why the attribution was selected and which policy version was used.
- Late events and identity links trigger recalculation.

**Optional:** Add last-non-direct click as a secondary comparison model without changing the primary model silently.

**Acceptance:** Tests cover touchpoint at payment time, after payment, exactly 30 days old, older than 30 days, Direct as last click, no identity, multiple orders and late touchpoint correction.

### FR-11 — Reporting APIs

**Requirement:** Expose date-filtered reporting grouped by channel and day.

**Expected behaviour:**
- Channel report includes orders, attributed revenue, spend and ROAS.
- Daily report includes daily channel metrics.
- Include Direct, Unknown and Unattributed buckets where applicable.
- Return currency, attribution model, lookback and reporting timezone.
- Use revenue date based on the selected order conversion timestamp and spend date based on source spend date.
- ROAS uses ratio of sums.
- Missing/zero spend is distinguishable.
- Order ledger supports pagination and order-level attribution explanation.

**Acceptance:** The sum of attributed channel revenue, including Unattributed, reconciles to eligible revenue for the same date/currency basis. Date boundaries do not omit end-date records.

### FR-12 — Dashboard

**Requirement:** Provide a simple but presentable analytics dashboard.

**Minimum components:**
- Urban Thread / Marketing Attribution title.
- Date-range filter.
- KPI cards: attributed revenue, paid orders, spend, overall ROAS.
- Daily revenue/spend trend.
- Channel performance table: orders, revenue, spend and ROAS.
- Order drill-down with attribution explanation.
- Data freshness and data-quality/processing indicators.

**Acceptance:** The dashboard reads the reporting API, reflects the selected date range, handles loading/empty/error states and displays currency/model/window information. It does not calculate attribution independently.

### FR-13 — SQL assignment

**Requirement:** Include `queries.sql` with two PostgreSQL queries and comments.

1. Session count and median session duration per day, using 30-minute visitor sessionization and grouping by session start date.
2. Elapsed days from buyer's earliest visit across all reliably linked devices to each paid order.

**Acceptance:** Both queries run against the final migrated schema. They use actual identity mapping and documented timezone/day semantics. Any inability to link devices is explicitly documented.

### FR-14 — Operational readiness and reconciliation

**Requirement:** The system remains usable after restart and can continue accepting new data beyond September.

**Expected behaviour:**
- API and worker are separate processes.
- Jobs and raw input live in PostgreSQL, not process memory.
- Job leases recover crashed workers.
- Retries are bounded and permanent failures are dead-lettered.
- Reconciliation can be invoked for a visitor, customer, order or date scope through an internal CLI/service.
- Health/readiness endpoints and structured logs exist.
- No code path is limited to September 2026.

**Acceptance:** Restart between replay batches; replay the September data twice; ingest October 1–3 without schema/date code changes; all records remain queryable and processable.

## 8. Business rules and metric definitions

| Item | Definition / policy |
|---|---|
| Session gap | New session only when adjacent event gap `> 30 minutes` |
| Session date | Local date of session start, using configured reporting timezone |
| Attribution model | Literal last-click for primary assignment implementation |
| Attribution window | 30 days, configurable, inclusive lower boundary |
| Eligible touchpoint | Verified acquisition/campaign/referral/direct-landing evidence; not arbitrary browsing events |
| Conversion time | Verified paid timestamp (`paid_at` where available) |
| Paid order | Canonical order whose current state is eligible under verified source/business status semantics |
| Revenue | Source-derived amount with explicitly documented basis; never assume gross/net without inspection |
| Refund | Adjust revenue only when reliable refund data exists; retain refund lineage |
| Channel | Deterministic mapping from source + medium + click/referrer evidence |
| Unattributed | Valid paid order without reliable matching identity/touchpoint |
| Missing spend | No accepted spend fact; not the same as zero |
| ROAS | `SUM(attributed revenue) / SUM(ad spend)` by matching channel/date range/currency |
| Zero spend | ROAS is null/undefined |
| Mixed currency | Report separately; no implicit FX conversion |
| Report date range | User-inclusive dates implemented as half-open timestamp interval |

## 9. Non-functional requirements

### Reliability

- Raw record and processing job are committed atomically.
- Idempotent ingestion and job effects.
- Recoverable worker leases and bounded retries.
- Raw evidence is immutable and derived state is rebuildable.
- No silent revenue loss; unresolved amounts remain represented in an Unattributed or exception bucket.

### Performance

The supplied dataset is small enough for PostgreSQL on a developer laptop. The design should process the initial dataset and a three-day follow-up without requiring cloud services. Use indexes and bounded worker batches. Do not introduce caching or distributed infrastructure without evidence of a bottleneck.

### Security

- Environment-based secrets.
- HMAC validation for order webhooks if supported.
- Request size limits, input validation and parameterized SQL.
- No committed source data or credentials.
- Minimize personal data in logs and dashboard output.

### Maintainability

- Clear module boundaries and typed interfaces.
- Versioned Alembic migrations.
- Domain logic independently unit-testable.
- Structured logs and explicit data-quality records.
- README setup and replay instructions verified from a clean checkout.

### Auditability

Every attribution decision should identify the order, resolved identity/evidence, selected touchpoint, channel, conversion timestamp, model, window, policy version, revenue/currency, calculation timestamp and reason.

## 10. Data-quality and exception requirements

| Data issue | Required treatment |
|---|---|
| Missing visitor ID | Preserve if contract allows; mark unresolved; no visitor session |
| Missing event ID | Generate internal ID; avoid unsafe fingerprint dedupe |
| Invalid timestamp | Reject/quarantine and report reason |
| Unknown event type | Preserve raw; classify as unknown if structurally valid |
| Unknown UTM values | Preserve source fields; channel `Unknown` until mapped |
| Conflicting identity | Retain conflict; no silent overwrite/merge |
| No order identity | Keep order; mark Unattributed |
| Duplicate webhook delivery | Deduplicate by delivery ID |
| Out-of-order webhook | Respect source version/updated timestamp |
| Negative/invalid money | Flag/quarantine based on verified business semantics |
| Missing currency | Do not aggregate; flag as invalid/unresolved |
| Missing spend | Display unavailable/missing |
| Unmatched spend campaign | Preserve and report unmatched mapping |
| Worker poison job | Dead-letter after max attempts with source reference |

## 11. Acceptance criteria / submission checklist

The submission is acceptable when all of the following are demonstrated:

- [ ] The actual dataset has been inspected and a data dictionary is committed.
- [ ] The schema reflects source fields and does not rely on fabricated assumptions.
- [ ] The API accepts events, order webhooks and spend records.
- [ ] Raw records and jobs are persisted atomically.
- [ ] The replay sender uses HTTP endpoints, preserves source sequence, and uses 10–20 concurrency.
- [ ] Repeated source records do not double-count.
- [ ] One canonical order is maintained across multiple webhook updates.
- [ ] Sessionization passes 29/30/31-minute and midnight tests.
- [ ] Identity resolution links only with reliable evidence and leaves conflicts unresolved.
- [ ] Attribution uses a documented last-click model and 30-day window.
- [ ] A late event can change a previous attribution and reporting updates accordingly.
- [ ] Financial policy (paid/cancelled/refund/currency) is based on actual source semantics.
- [ ] Missing spend differs from zero; ROAS aggregation is correct.
- [ ] Both PostgreSQL SQL questions run against actual tables.
- [ ] Dashboard date filter, metrics, daily trend and channel table work.
- [ ] Health/readiness, worker recovery and data freshness are observable.
- [ ] September replay twice leaves logical counts and totals unchanged.
- [ ] October 1–3 data can be replayed into the running system without special handling.
- [ ] README commands work from a clean checkout.

## 12. Mapping to the evaluation rubric

| Evaluation signal | Product/system evidence | Review demonstration |
|---|---|---|
| Numbers you can defend | Raw-to-derived lineage, policy version, financial definitions and reconciliation | Trace a revenue figure back to the order, touchpoint and source records |
| Judgement with messy data | Explicit unknown/unresolved handling, conflict records and data-quality reasons | Explain why an ambiguous identity or missing spend is not guessed |
| A system, not a script | Durable HTTP ingestion, database-backed jobs, retry/lease recovery and restart-safe state | Restart services and continue processing; replay the same source data safely |
| Clear thinking about scale | Bounded concurrency, indexes, batching, worker metrics and a staged optimization path | Explain expected bottlenecks and what measurement would justify scaling a component |
| Ownership of your code | Small modules, documented policies, migrations and testable domain services | Change a business rule live and explain the consequences through the tests |
| Honest communication | Data dictionary, assumptions, actual test results and limitations in README | Distinguish observed facts, decisions, unverified assumptions and unimplemented work |

The project should report measured throughput and query/worker timings from its real execution. Do not add unsupported performance numbers to the submission.

## 13. Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Event/order data share no stable identity | Cross-device attribution incomplete | Preserve visitor-level attribution and label unresolved orders; document limits |
| Order webhooks lack delivery IDs or versions | Duplicates/order state ambiguity | Use safe internal IDs; do not unsafe-dedupe; mark weak ordering confidence |
| Event timestamps missing/inconsistent | Incorrect journey and session order | Quarantine invalid values; normalize timezone explicitly |
| UTM/source values inconsistent | Channel misclassification | Keep raw values; deterministic reviewed mapping; Unknown bucket |
| Spend granularity differs from revenue | Misleading ROAS | Aggregate at common supported granularity only |
| Refund semantics absent | Gross/net uncertainty | Do not fabricate net revenue; state the limitation |
| Late October events revise September attribution | Historical totals change | Reconciliation and calculation lineage |
| Worker concurrent rebuilds collide | Duplicate/inconsistent derived state | Per-identity database locking and idempotent writes |
| Data size increases | Slow rebuild/report queries | Measure, index, batch, then selectively optimize |

## 14. Delivery phases

| Phase | Deliverable | Exit condition |
|---|---|---|
| 0. Inspect | Data profile and data dictionary | Actual IDs, timestamps, statuses, channels and money fields identified |
| 1. Foundation | Project structure, config, DB migrations, health endpoints | Clean database boots reproducibly |
| 2. Ingestion | Three HTTP endpoints and durable jobs | Valid/duplicate/invalid cases behave correctly |
| 3. Replay | HTTPX sender with bounded concurrency and report | Complete source replay with no direct DB loading |
| 4. Core transformations | Normalization, identity, sessions and touchpoints | Unit/integration rules pass |
| 5. Attribution | Order state, financial policy, last-click and audit trail | Deterministic and late-event tests pass |
| 6. Reporting | Channel/daily/order APIs and `queries.sql` | Queries reconcile to canonical facts |
| 7. Presentation | Streamlit dashboard | Date filter, KPI, charts, table and order inspection work |
| 8. Verification | Restart, repeat replay, October simulation, documentation | Submission checklist complete |

## 15. Product success measures

Success is not defined by visual polish or the number of frameworks. It is demonstrated by:

- No duplicated logical events/orders/spend after repeated delivery.
- No revenue credited to a touchpoint after conversion.
- No silent cross-user identity merges.
- Reproducible session boundaries and attribution decisions.
- Complete accounting of eligible revenue across channel and Unattributed buckets.
- ROAS computed from summed revenue and spend at consistent granularity/currency.
- Historical calculations corrected when relevant late data arrives.
- Successful September + October replay using the same running system.

---

## Product principle

**Never let the system silently turn uncertain source data into confident business metrics.** Preserve evidence, expose uncertainty, document the policy and make every financial result explainable.
