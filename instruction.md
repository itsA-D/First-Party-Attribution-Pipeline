# Implementation Instructions — Urban Thread First-Party Attribution Pipeline

You are acting as a senior backend engineer responsible for implementing the **Urban Thread First-Party Attribution Pipeline**.

Your job is to fully implement the project according to the provided assignment and engineering documents.

This is an implementation task, not an opportunity to redesign the project, introduce unnecessary technologies, or build features that were never requested.

## 1. Read everything before writing code

Before modifying anything, inspect these files:

1. `architecture.md` — authoritative technical architecture.
2. `PRD.md` — product requirements and expected behaviour.
3. `claude.md` — project-specific implementation instructions.
4. `README.md` — setup and operational documentation.
5. `Backend Engineer Assignment — First-Party Attribution Pipeline.pdf` — original assignment and evaluation criteria.
6. `urbanthread_data_sep2026.zip` — actual dataset, including its README/data dictionary and all CSV/JSONL files.

Also inspect the existing repository structure and code before making changes.

**Do not start coding immediately.**

First, provide me with:

- Your understanding of the project.
- The existing repository structure.
- The architecture you intend to implement.
- Any assumptions you need to make.
- Any missing information or conflicting instructions.
- A concise implementation sequence.

If a material requirement is unclear, stop and ask me before making that decision.

Do not silently invent field names, business rules, relationships, API contracts, or dataset semantics.

For non-blocking implementation details, state reasonable assumptions explicitly and follow the supplied documentation.

## 2. Strict source-of-truth rules

Follow the supplied files rather than introducing your own architecture.

Use this priority:

1. Original assignment: acceptance criteria and deliverables.
2. `architecture.md`: architectural and technical decisions.
3. `PRD.md`: functional requirements and business behaviour.
4. `claude.md`: implementation conventions.
5. `README.md`: operational documentation.

The actual dataset README and records are the source of truth for data fields and their meanings.

If two documents materially contradict each other, tell me and wait for clarification.

Do not use external research, unrelated tutorials, or alternative architectural suggestions to change the agreed implementation.

## 3. Minimum-code philosophy — extremely important

Follow this principle throughout the implementation:

**Write the minimum code necessary to satisfy the requirements.**

- Do not write a thousand lines when a hundred will do.
- If 200 lines can reasonably become 50, rewrite them.
- Do not create unnecessary classes, interfaces, abstractions, wrappers or helper functions.
- Do not introduce design patterns simply to demonstrate knowledge.
- Do not build generic frameworks for a project that needs one straightforward implementation.
- Do not create unnecessary configuration layers.
- Avoid duplicated logic.
- Prefer simple functions and readable modules.
- Keep business logic understandable and deterministic.
- Do not compress everything into unreadable one-liners just to reduce line count.

Every file and every abstraction must have a clear reason to exist.

Implement only what the assignment and supplied documents require.

## 4. Protect existing code

This rule is mandatory.

- Inspect a file before editing it.
- Touch only the lines and files required for the implementation.
- Never rewrite an entire file when a small targeted change is sufficient.
- Never delete existing code just because you do not understand it.
- Never remove existing functionality to make your implementation easier.
- Do not rename existing modules, functions, variables or endpoints without a concrete requirement.
- Do not restructure unrelated directories.
- Preserve existing Git history and project configuration.
- Do not overwrite existing documentation unnecessarily.

Before modifying an existing component, understand its responsibilities and dependencies.

**Make targeted, minimal changes. Nothing unrelated.**

## 5. Technology stack

Use the technology stack established by `architecture.md`.

The intended stack is:

- Python 3.12+
- FastAPI
- PostgreSQL
- SQLAlchemy 2.0
- Alembic
- Pydantic
- PostgreSQL-backed background processing
- HTTPX for replay
- Streamlit for the dashboard
- Plotly for charts
- Pytest for test infrastructure only where required by the supplied documents

Do not independently introduce:

- Kafka
- Redis
- Celery
- Kubernetes
- Microservices
- MongoDB
- Airflow
- LLM-based attribution

Do not add another technology unless the existing documents explicitly require it or you ask me first.

## 6. Implement the complete assignment

Implement the following functionality.

### A. Data ingestion

Implement:

`POST /collect`

Receives website tracking events.

`POST /webhooks/orders`

Receives order update webhooks.

Requirements:

- Validate incoming payloads.
- Preserve original payloads.
- Persist raw records before acknowledging successful acceptance.
- Handle duplicate deliveries.
- Make ingestion idempotent.
- Preserve event occurrence timestamps separately from ingestion timestamps.
- Handle malformed and incomplete data according to the documented policies.
- Keep processing separate from synchronous API ingestion.

Do not load the supplied files directly into database tables as a substitute for the sender.

### B. Dataset replay sender

Implement a small sender script that:

- Reads `events.jsonl`, `orders.csv` and `ad_spend.csv`.
- Uses the actual fields defined in the dataset README.
- Preserves file ordering when scheduling records.
- Uses bounded concurrency, approximately 10–20 requests.
- Sends records through the intended ingestion interfaces.
- Uses stable identifiers for retries.
- Retries transient failures only.
- Reports accepted, duplicate, failed and retried records.

Do not silently discard failed records.

Do not change event timestamps to make the data appear ordered.

Where the ad-spend ingestion mechanism is not settled by the supplied architecture, ask me before selecting an interface.

### C. Data processing

Implement the processing pipeline described in `architecture.md`.

Keep responsibilities separate:

- Event normalization.
- Channel resolution.
- Identity resolution.
- Session reconstruction.
- Marketing touchpoint generation.
- Order reconciliation.
- Revenue attribution.
- Reporting.

Use PostgreSQL as the source of truth.

Preserve raw data so derived records can be reconstructed.

### D. Identity resolution

Use the actual identifiers available in the dataset.

The system must:

- Associate visitors with known customers when reliable evidence exists.
- Support multiple devices belonging to the same known customer.
- Avoid incorrectly combining unrelated visitors.
- Preserve unresolved identities.
- Handle late identity relationships according to the documented policy.

Never infer identity from IP addresses or similar browsing behaviour unless explicitly supported by the supplied specifications.

### E. Session reconstruction

Implement 30-minute inactivity-based sessionization.

Requirements:

- Partition sessions by visitor.
- Sort by event occurrence time.
- Use deterministic ordering for equal timestamps.
- Start a new session when the inactivity gap exceeds 30 minutes, following the documented boundary convention.
- Handle late-arriving events.
- Rebuild affected sessions when historical events change.

A single-event session must have zero duration.

### F. Attribution engine

Implement the primary last-click attribution model specified in the assignment.

Use the attribution policy and lookback window specified in the supplied documents.

Important:

- Use actual event occurrence timestamps.
- Never credit events occurring after the order conversion.
- Resolve the buyer's identity before selecting touchpoints.
- Preserve the relationship between an order and its selected touchpoint.
- Handle Direct, Organic, Paid Social, Paid Search and Unknown according to actual evidence and documented channel rules.
- Do not treat arbitrary website events as marketing touchpoints without supporting evidence.
- Attribute each eligible order independently.
- Recalculate attribution when relevant late data arrives.

Do not use an LLM or probabilistic guessing to determine attribution.

If the supplied documents disagree about literal last-click versus last-non-direct attribution, stop and ask me.

### G. Advertising spend and ROAS

Process the supplied `ad_spend.csv`.

Implement correct reporting semantics:

- Revenue by channel.
- Eligible paid orders by channel.
- Advertising spend by channel.
- ROAS = total attributed revenue / total advertising spend.

Do not average individual ROAS ratios to calculate an overall ROAS.

Handle missing spend separately from zero spend.

Return null when ROAS cannot be calculated because spend is zero or unavailable, as appropriate to the documented response contract.

Preserve currency information and never add different currencies without a documented conversion policy.

### H. Reporting API

Implement the API contracts specified in `architecture.md` and `PRD.md`.

At minimum, provide:

- Channel-level reporting.
- Daily channel reporting.
- Order listing.
- Individual order and attribution details where specified.
- Health and readiness endpoints.

Date filters must be inclusive of the selected reporting dates. Internally use correct timestamp boundaries and the documented timezone.

### I. Dashboard

Implement a clean, slightly polished Streamlit dashboard.

Do not overengineer the UI.

Include:

- Date-range filter.
- Revenue KPI.
- Paid orders KPI.
- Advertising spend KPI.
- Overall ROAS KPI.
- Channel performance comparison.
- Daily revenue trend.
- Channel-level performance table.
- A basic data freshness indicator where supported.

The dashboard must consume the backend reporting API.

Do not hardcode illustrative figures or calculate independent business metrics inside the UI.

The dashboard should be simple, readable and business-oriented.

### J. SQL deliverable

Create `queries.sql` using plain PostgreSQL SQL.

Implement both questions from the assignment:

1. Sessions: split each visitor's events into sessions using 30-minute inactivity and return session count and median session length per day.
2. Days to convert: for every paid order, calculate the number of days between the buyer's earliest observed visit across all reliably linked devices and the order.

Use actual table and column names.

Add a short explanatory SQL comment above each query.

Do not replace the required SQL queries with Python implementations.

### K. October incremental data

The system must continue accepting data after the September backfill.

Design the ingestion and reconciliation process so the follow-up 1–3 October dataset can be replayed into the same running system.

Do not assume the data always arrives chronologically.

Do not truncate historical records when new data is supplied.

Ensure repeated replay does not create duplicate logical events, webhook deliveries or orders.

### L. Documentation and submission

Maintain the supplied documentation and ensure it reflects the actual implementation.

The final repository must include:

- Application code.
- Database schema and migrations.
- Sender script.
- `queries.sql`.
- Dashboard.
- `.env.example`.
- Docker configuration where specified.
- README with setup and operational instructions.

The README must also cover the assignment's required deliverables:

- September per-channel results using last-click attribution.
- Dashboard screenshot.
- Decisions made about inconsistent or excluded data.
- A half-page explanation of what would break first at 500 stores and 50 million events per day, and what should change.
- A short, honest explanation of AI-tool usage, including one instance where an AI tool got something wrong.

Never fabricate results, screenshots or claims about production readiness.

## 7. Testing restriction — do not test autonomously

This is an explicit instruction.

**Do not test the project by yourself.**

Do not:

- Execute Pytest.
- Run unit or integration tests.
- Run endpoint requests.
- Start the application to validate it.
- Start Docker containers.
- Execute the replay sender.
- Run migrations against a database.
- Generate mock results to claim functionality.
- Run benchmarks or load tests.
- Perform autonomous manual testing.

Implement the project and its required files only.

You may write test files if explicitly required by the supplied project specifications, but do not execute them.

Do not claim that any test passed or that the application was verified.

At handoff, clearly state that execution and testing have not been performed and leave those steps for me.

## 8. Implementation sequence

Work in this order:

**Phase 1 — Inspection**

Read all specified documentation, inspect the repository and inspect the actual dataset schema.

Identify assumptions, missing inputs and contradictions.

Ask me about material uncertainties before implementation.

**Phase 2 — Foundation**

Implement the minimum project structure, configuration, database models and migrations.

Follow the documented architecture.

**Phase 3 — Ingestion**

Implement the two required ingestion endpoints, persistence, idempotency and sender integration.

**Phase 4 — Processing**

Implement normalization, identity resolution, sessionization and attribution.

**Phase 5 — Reporting**

Implement channel reporting, daily reporting and order-level details.

**Phase 6 — SQL and dashboard**

Implement `queries.sql` and the Streamlit dashboard.

**Phase 7 — Operational deliverables**

Finish Docker/configuration files, update README, document decisions and prepare the required output structure.

Do not skip a requirement to save time, and do not add unrelated features to appear more sophisticated.

## 9. How to make engineering decisions

For every implementation decision:

1. Check the supplied documentation.
2. Check the actual dataset structure where relevant.
3. Choose the smallest implementation that satisfies the requirement.
4. Preserve existing behaviour.
5. Make the decision explicit where it affects business metrics.
6. Ask me when a material decision cannot be grounded in the provided sources.

Do not overengineer for hypothetical future requirements.

Design for the stated future October replay and explain the larger 500-store scaling path without implementing an unnecessary distributed system.

## 10. Final response requirements

When implementation is complete, provide:

1. A summary of the implemented functionality.
2. The final project structure.
3. The files created and modified.
4. Important assumptions and business rules.
5. Any requirements that remain incomplete.
6. The exact commands I should run to start the application and database.
7. The command I should use to replay the September dataset.
8. The steps I should use to send the October dataset through the running system.
9. An explicit statement that you did not execute tests or verify the running application.

Do not say something works unless it has actually been verified by me.

---

## Final instruction

Think like a senior engineer who values correctness, simplicity and ownership.

The objective is not to produce the most code.

The objective is to produce the **smallest understandable implementation that correctly satisfies the assignment and follows the supplied engineering documents.**

Do not guess. Do not overbuild. Do not rewrite unrelated code. Do not silently discard existing functionality.

**List assumptions first. Ask when something is unclear. Write the minimum. Touch only what is necessary. Implement only what was requested.**