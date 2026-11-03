-- Query 1: Sessionize normalized events by visitor with the strict >30-minute gap rule.
-- A new session starts on the first event for a visitor, or when the gap to the
-- previous event is strictly greater than 30 minutes. Events at exactly 30 minutes
-- remain in the same session. Session start local date is the session bucket;
-- single-event sessions have zero duration. This query uses UTC reporting dates;
-- update the AT TIME ZONE literal if REPORTING_TIMEZONE changes.
WITH valid_events AS (
    SELECT
        id,
        anonymous_id,
        occurred_at,
        COALESCE(external_event_id, id) AS stable_event_id
    FROM events
    WHERE anonymous_id IS NOT NULL
      AND occurred_at IS NOT NULL
      AND data_quality_status = 'ok'
),
marked AS (
    SELECT
        id,
        anonymous_id,
        occurred_at,
        stable_event_id,
        CASE
            WHEN LAG(occurred_at) OVER (
                PARTITION BY anonymous_id
                ORDER BY occurred_at, stable_event_id, id
            ) IS NULL THEN 1
            WHEN occurred_at - LAG(occurred_at) OVER (
                PARTITION BY anonymous_id
                ORDER BY occurred_at, stable_event_id, id
            ) > INTERVAL '30 minutes' THEN 1
            ELSE 0
        END AS is_new_session
    FROM valid_events
),
sessionized AS (
    SELECT
        id,
        anonymous_id,
        occurred_at,
        stable_event_id,
        SUM(is_new_session) OVER (
            PARTITION BY anonymous_id
            ORDER BY occurred_at, stable_event_id, id
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        ) AS session_number
    FROM marked
),
sessions AS (
    SELECT
        anonymous_id,
        session_number,
        MIN(occurred_at) AS started_at,
        MAX(occurred_at) AS ended_at,
        COUNT(*) AS event_count,
        EXTRACT(EPOCH FROM (MAX(occurred_at) - MIN(occurred_at)))::numeric AS duration_seconds
    FROM sessionized
    GROUP BY anonymous_id, session_number
)
SELECT
    (started_at AT TIME ZONE 'UTC')::date AS session_date,
    COUNT(*) AS sessions,
    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY duration_seconds) AS median_duration_seconds
FROM sessions
GROUP BY 1
ORDER BY 1;

-- Query 2: For each paid order, find the buyer's earliest linked page-view visit
-- across all reliable identity links. This uses identity_links, not a direct
-- orders.visitor_id column. Elapsed days are fractional, measured from the first
-- linked visit to the order conversion. Conflicted links are excluded.
-- Note: For first-visit calculation, we use all non-conflicted identity links for
-- the customer, regardless of the link's valid_from date. This finds the true
-- earliest observed visit even if it predates the 30-day attribution backfill window.
WITH linked_visits AS (
    SELECT
        o.id AS order_row_id,
        o.order_id,
        o.customer_id,
        o.conversion_at,
        MIN(e.occurred_at) AS first_visit_at
    FROM orders AS o
    LEFT JOIN identity_links AS l
        ON l.customer_id = o.customer_id
       AND l.conflict = false
    LEFT JOIN events AS e
        ON e.anonymous_id = l.anonymous_id
       AND e.event_name = 'page_view'
       AND e.occurred_at IS NOT NULL
       AND e.data_quality_status = 'ok'
       AND e.occurred_at <= o.conversion_at
    WHERE o.paid_eligible = true
    GROUP BY o.id, o.order_id, o.customer_id, o.conversion_at
)
SELECT
    order_id,
    customer_id,
    conversion_at,
    first_visit_at,
    CASE
        WHEN first_visit_at IS NULL THEN NULL
        ELSE ROUND(
            EXTRACT(EPOCH FROM (conversion_at - first_visit_at))::numeric / 86400.0,
            4
        )
    END AS elapsed_days
FROM linked_visits
ORDER BY conversion_at, order_id;
