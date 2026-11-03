# Urban Thread Data Dictionary

This document records the actual fields, counts and known anomalies found in `urbanthread_data_sep2026.zip`. Counts were measured from the supplied files. No source values are fabricated; uncertain meanings are marked as assumptions or limitations.

## Source files

| File | Measured rows | Key finding |
|---|---:|---|
| `events.jsonl` | 55,758 | 54,072 unique `event_id` values; 1,686 IDs repeat with identical domain data except `received_at`. |
| `orders.csv` | 1,153 webhook deliveries | 1,078 unique orders; 75 orders have exactly two update rows. |
| `ad_spend.csv` | 230 daily rows | 7 campaigns, 3 platforms; 20 repeated `(date, platform, campaign)` natural keys with differing values. |

The archive is not committed. Extract it locally under `data/`; the repository maintains a temporary inspection extraction outside the project tree.

## Events (`events.jsonl`, one JSON object per physical line)

| Field | Observed meaning | Handling |
|---|---|---|
| `event_id` | Source event identifier, e.g. `evt_...` | Stored as the trusted idempotency key. Duplicate source events are deduplicated by `event_id`. |
| `event_name` | `page_view`, `add_to_cart`, `checkout_started`, `identify`, `purchase` | Preserved. Only page views can become acquisition touchpoints. |
| `ts` | Device event timestamp | Parsed as ISO-8601 or epoch milliseconds. Never replaced with receipt time. |
| `received_at` | Server-side receipt timestamp supplied by the source | Normalized to UTC and stored separately from `ts`. |
| `anonymous_id` | First-party visitor/device identifier | Used for sessionization and as the anonymous side of identity links. |
| `email_hash` | Hashed customer email, present on some events | Used as explicit customer identity evidence. Never treated as a password or reversible email. |
| `page_url` | Landing/product URL | Parsed for UTM parameters and click IDs. |
| `referrer` | External or internal referrer | Used only for external acquisition classification. |
| `user_agent`, `ip` | Request metadata | Preserved in raw payload; never used to merge identities. |
| `value`, `currency` | Optional monetary fields on purchase events | Stored with `NUMERIC`; event value is not used as order revenue. |
| `order_id` | Optional event-to-order reference | Preserved; order revenue comes from order webhooks, not this field. |
| `event_name` counts | `page_view` 48,285; `add_to_cart` 3,025; `checkout_started` 1,724; `identify` 1,721; `purchase` 1,003 | Raw preservation keeps unknown event names usable. |

### Event timestamp findings

- 2,666 timestamps are epoch milliseconds, not ISO strings.
- 8,216 timestamps carry a `+05:30` offset.
- No timestamps contain invalid or out-of-range ISO years. Epoch-millisecond timestamps are handled separately.
- 107 timestamps are more than 24 hours after their `received_at`.
- 1,026 events arrived more than one day after their occurrence.
- Some packets arrive out of order relative to `ts`; arrival order is never used for journey order.

### Event-to-order observations

- 15 `purchase` events use `TEST-*` identifiers and have no corresponding order webhook.
- 60 orders have more than one `purchase` event.
- 151 orders have no matching `purchase` event.
- Purchase events are evidence only; canonical revenue and paid state come from `orders.csv`.

## Orders (`orders.csv`, one row per webhook delivery)

| Field | Observed meaning | Handling |
|---|---|---|
| `order_id` | Source order identifier, e.g. `#100839` | Canonical order key. Not an idempotency key: one order has multiple updates. |
| `email_hash` | Hashed customer email | Explicit customer identity when present. Empty values are preserved as missing identity. |
| `created_at` | Order creation timestamp | Used as the conversion timestamp proxy. This is an assumption; no separate `paid_at` field exists. |
| `updated_at` | Source state-update timestamp | Used to order webhook snapshots. A newer value advances canonical state. |
| `total` | Order total | Stored as `NUMERIC(18,4)`. Source does not break out gross, net, tax or discount. |
| `currency` | `INR` or `USD` | Kept on every financial record. No FX conversion. |
| `financial_status` | `paid`, `refunded`, `cancelled` | Only `paid` is eligible for paid revenue. Unknown values remain visible and are not treated as paid. |

### Order state findings

- 1,003 paid orders: INR 3,250,524.00 and USD 1,411.44.
- 53 refunded orders and 22 cancelled orders.
- 989 distinct order emails; 931 of those appear in the event stream.
- 58 paid orders have no event identity evidence.
- No refund amount, tax, discount or net-revenue field exists. Refunded orders are therefore excluded from paid revenue rather than adjusted by an unverified amount.
- Multiple rows per order are webhook deliveries, not duplicate orders.

### Order idempotency policy

- Raw webhook identity is a deterministic delivery key built from source file name, row number and canonical row payload (or an upstream delivery ID / `Idempotency-Key` when supplied).
- Replaying the same delivery is a duplicate.
- Two different delivery rows for the same `order_id` are both retained; the canonical order advances only when `updated_at` is newer.
- An older update arriving late is retained as evidence but does not overwrite newer state. An equal-timestamp conflict keeps the first canonical state and records a data-quality issue.

## Ad spend (`ad_spend.csv`, one row per daily campaign snapshot)

| Field | Observed meaning | Handling |
|---|---|---|
| `date` | Spend date | Stored as a calendar date and used as the spend reporting date. |
| `platform` | `google`, `meta`, `tiktok` (case variants) | Mapped to Google Ads, Meta Ads, TikTok Ads or Unknown. |
| `campaign` | Campaign name | Part of the natural spend key. |
| `spend` | Daily spend amount | Stored as `NUMERIC(18,4)`; negative or non-finite values are rejected from derived state. |
| `currency` | `INR` in this dataset | Kept separate; no cross-currency aggregation. |
| `pulled_at` | Snapshot extraction time | Latest snapshot wins per natural key. |

### Spend duplication findings

- 230 rows contain 210 distinct `(date, platform, campaign)` keys; 20 keys have multiple snapshots.
- Summing every row would overstate spend: INR 1,594,095.08.
- Latest-`pulled_at` selection gives INR 1,467,782.37.
- The pipeline preserves every raw snapshot and uses the latest `pulled_at` per `(date, platform, campaign, currency)`. Equal timestamps use a deterministic delivery-key tie-break and record a conflict issue.
- Spend is reported by `spend_date`; revenue is reported by order `conversion_at`. They are intentionally different date bases.
- There is no spend data for USD. USD ROAS is null rather than zero.

## Identity findings

- 2,636 distinct email hashes and 5,776 distinct anonymous IDs.
- 96 email hashes appear on more than one anonymous ID; the maximum is three visitors per email.
- No anonymous ID maps to more than one email hash in this dataset.
- Identity links are temporal: `valid_from` is the evidence time minus the configured backfill window (30 days by default).
- The 30-day backfill allows recent anonymous events to be retroactively attributed to newly identified customers. It does NOT limit historical first-visit calculation: SQL Query 2 finds the actual earliest page view across all non-conflicted identity links for the customer, regardless of when the link was established.
- Conflicted visitors are marked and excluded from attribution. They remain visible in `identity_links` and `data_quality_issues`.
- IP address, user agent, location and behavioural similarity are never identity evidence.

## Channel mapping and touchpoints

- Click IDs map directly: `gclid` → Google Ads, `fbclid` → Meta Ads, `ttclid` → TikTok Ads.
- Paid UTM combinations map to Google Ads, Meta Ads or TikTok Ads.
- Email UTM/medium maps to Email; organic UTM/referrer maps to Organic Search or Organic Social; other external referrers map to Referral.
- Internal navigation is not a touchpoint.
- A Direct touchpoint requires an explicit session-start page view with no campaign, click ID or external referrer.
- A generic product page view or add-to-cart without acquisition evidence does not compete for last click.
- Orders with no reliable identity or eligible touchpoint remain `Unattributed`, never `Direct`.

## Reporting and time policy

- Internal timestamps are UTC-aware.
- Reporting timezone is `UTC` by default and is exposed in API responses. It is configurable via `REPORTING_TIMEZONE`.
- Date filters are inclusive user dates, converted to a half-open UTC timestamp range.
- Session threshold is strictly `gap > 30 minutes`; exactly 30 minutes stays in the session.
- Attribution lookback is 30 days by default and inclusive at the lower boundary.
- Revenue uses `orders.conversion_at` (from source `created_at`) as the conversion timestamp.
- Spend uses `ad_spend.date`, not `pulled_at`.

## Known limitations

- `orders.created_at` is used as the paid conversion timestamp because no separate paid/refund timestamp exists.
- Refund amounts are unavailable, so refunded orders are excluded rather than netted.
- No FX rates are supplied; currencies are reported separately.
- The source has only 96 cross-device email cases and no conflicting email/visitor pairs, so identity conflict behaviour is tested with synthetic fixtures rather than observed in production data.
- The dataset does not include a platform spend-to-campaign mapping beyond the `platform` and `campaign` strings.
- September replay and October follow-up results are not recorded until the application and tests are actually executed.
