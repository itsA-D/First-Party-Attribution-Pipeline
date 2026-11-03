from __future__ import annotations

import os
from datetime import date
from urllib.parse import quote

import pandas as pd
import plotly.express as px
import requests
import streamlit as st

API_BASE = os.getenv("API_BASE_URL", "http://localhost:8000").rstrip("/")

st.set_page_config(
    page_title="Urban Thread Attribution",
    page_icon=None,
    layout="wide",
)
st.markdown(
    """
    <style>
      .block-container {padding-top: 2rem; padding-bottom: 3rem; max-width: 1400px;}
      h1, h2, h3 {letter-spacing: -0.02em;}
      [data-testid="stMetricValue"] {font-size: 1.65rem;}
      div[data-testid="stSidebar"] {border-right: 1px solid rgba(128,128,128,.18);}
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_data(ttl=20, show_spinner=False)
def get_json(base: str, path: str, params: dict | None = None) -> dict:
    response = requests.get(f"{base}{path}", params=params, timeout=20)
    response.raise_for_status()
    return response.json()


def money(value: str | None, currency: str) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{currency} {float(value):,.2f}"
    except (TypeError, ValueError):
        return "n/a"


def ratio(value: float | None) -> str:
    return "n/a" if value is None else f"{value:,.2f}"


st.title("Urban Thread Attribution")
st.caption("First-party, order-level last-click attribution")

with st.sidebar:
    st.subheader("Controls")
    api_base = st.text_input("API base URL", value=API_BASE)
    if api_base.rstrip("/") != API_BASE:
        st.caption("Restart Streamlit after changing API_BASE_URL.")
    start_date = st.date_input("Start date", value=date(2026, 9, 1))
    end_date = st.date_input("End date", value=date(2026, 9, 30))
    refresh = st.button("Refresh data", use_container_width=True)
    st.divider()
    st.caption("Revenue dates use order conversion time. Spend dates use the platform spend date. All date buckets use the configured reporting timezone.")

if refresh:
    st.cache_data.clear()

if start_date > end_date:
    st.error("Start date must be on or before end date.")
    st.stop()

params = {"start_date": start_date.isoformat(), "end_date": end_date.isoformat()}

try:
    status = get_json(api_base, "/api/v1/status")
    channel_report = get_json(api_base, "/api/v1/reports/channels", params)
    daily_report = get_json(api_base, "/api/v1/reports/daily", params)
except requests.RequestException as exc:
    st.error("The attribution API is unavailable. Start the API and verify API_BASE_URL.")
    st.code(str(exc))
    st.stop()

currencies = [group["currency"] for group in channel_report.get("currencies", [])]
if not currencies:
    st.info("No paid orders were found for this date range.")
    st.stop()

with st.sidebar:
    selected_currency = st.selectbox("Currency", currencies)
    group = next(
        (item for item in channel_report["currencies"] if item["currency"] == selected_currency),
        None,
    )
    channel_names = [row["channel"] for row in (group or {}).get("channels", [])]
    selected_channel = st.selectbox("Channel filter", ["All channels"] + channel_names)

group = next(
    (item for item in channel_report["currencies"] if item["currency"] == selected_currency),
    None,
)
if group is None:
    st.warning("The selected currency has no report data.")
    st.stop()

totals = group["totals"]
kpi_columns = st.columns(4)
kpi_columns[0].metric("Attributed revenue", money(totals["revenue"], selected_currency))
kpi_columns[1].metric("Paid orders", f"{totals['orders']:,}")
kpi_columns[2].metric(
    "Ad spend",
    money(totals["ad_spend"], selected_currency),
    help="Missing spend is shown as n/a. Zero spend is reported as zero.",
)
kpi_columns[3].metric(
    "ROAS",
    ratio(totals["roas"]),
    help="Weighted ROAS: total attributed revenue divided by total recorded spend.",
)

if totals["spend_status"] == "missing":
    st.caption("No spend records exist for this currency and date range. ROAS is intentionally null, not zero.")
elif totals["spend_status"] == "zero":
    st.caption("Spend records exist and total zero. ROAS is null when revenue is also zero.")

st.subheader("Daily revenue and spend")
daily_rows = [
    row
    for row in daily_report.get("rows", [])
    if row["currency"] == selected_currency
    and (selected_channel == "All channels" or row["channel"] == selected_channel)
]
if daily_rows:
    daily = pd.DataFrame(daily_rows)
    daily["date"] = pd.to_datetime(daily["date"])
    daily["revenue"] = pd.to_numeric(daily["revenue"], errors="coerce").fillna(0)
    daily["ad_spend"] = pd.to_numeric(daily["ad_spend"], errors="coerce")
    left, right = st.columns([1.35, 1])
    with left:
        fig = px.area(
            daily,
            x="date",
            y="revenue",
            color="channel",
            labels={"revenue": f"Attributed revenue ({selected_currency})", "date": ""},
        )
        fig.update_layout(
            margin=dict(l=0, r=0, t=10, b=0),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
        )
        st.plotly_chart(fig, use_container_width=True)
    with right:
        spend_daily = daily.groupby("date", as_index=False)["ad_spend"].sum(min_count=1)
        if spend_daily["ad_spend"].notna().any():
            fig = px.bar(
                spend_daily,
                x="date",
                y="ad_spend",
                labels={"ad_spend": f"Spend ({selected_currency})", "date": ""},
            )
            fig.update_layout(
                margin=dict(l=0, r=0, t=10, b=0),
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
            )
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("No ad spend records for this date range.")
else:
    st.info("No daily channel rows for this selection.")

st.subheader("Channel performance")
channel_rows = [
    row
    for row in group["channels"]
    if selected_channel == "All channels" or row["channel"] == selected_channel
]
if channel_rows:
    frame = pd.DataFrame(channel_rows)
    frame["revenue"] = pd.to_numeric(frame["revenue"], errors="coerce")
    frame["ad_spend"] = pd.to_numeric(frame["ad_spend"], errors="coerce")
    frame["roas"] = pd.to_numeric(frame["roas"], errors="coerce")
    st.dataframe(
        frame,
        hide_index=True,
        use_container_width=True,
        column_config={
            "channel": "Channel",
            "orders": st.column_config.NumberColumn("Paid orders", format="%d"),
            "revenue": st.column_config.NumberColumn(
                f"Revenue ({selected_currency})", format="%.2f"
            ),
            "ad_spend": st.column_config.NumberColumn(
                f"Spend ({selected_currency})", format="%.2f"
            ),
            "roas": st.column_config.NumberColumn("ROAS", format="%.2f"),
            "spend_status": "Spend status",
        },
    )
    st.caption("Spend status is missing, zero or available. Missing spend is never treated as zero.")

st.subheader("Order drill-down")
order_params = dict(params)
if selected_channel != "All channels":
    order_params["channel"] = selected_channel
try:
    ledger = get_json(api_base, "/api/v1/orders", {**order_params, "limit": 100})
except requests.RequestException as exc:
    st.warning("Order ledger could not be loaded.")
    st.code(str(exc))
    ledger = {"items": []}

if ledger.get("items"):
    options = [
        f"{row['order_id']} | {row['channel']} | {money(row['revenue'], row['currency'])}"
        for row in ledger["items"]
    ]
    selected_order = st.selectbox("Order", options)
    order_id = ledger["items"][options.index(selected_order)]["order_id"]
    try:
        detail = get_json(api_base, f"/api/v1/orders/{quote(order_id, safe='')}")
    except requests.RequestException as exc:
        st.warning("Order detail could not be loaded.")
        st.code(str(exc))
    else:
        order = detail["order"]
        attribution = detail["attribution"]
        st.markdown(
            f"**Order {order['order_id']}** · {order['financial_status']} · "
            f"{money(order['total'], order['currency'])} · {order['webhook_deliveries']} webhook deliveries"
        )
        if attribution:
            st.markdown(
                f"Current attribution: **{attribution['channel']}** using "
                f"`{attribution['model']}` with a {attribution['lookback_days']}-day lookback. "
                f"Reason: {attribution['reason']}"
            )
            if attribution.get("touchpoint"):
                touch = attribution["touchpoint"]
                st.caption(
                    f"Touchpoint event {touch['event_id']} occurred at {touch['occurred_at']} "
                    f"({touch['utm_source'] or 'no utm_source'}, {touch['utm_campaign'] or 'no campaign'})."
                )
        else:
            st.warning("This order has no current attribution record.")
        with st.expander("Attribution history"):
            st.dataframe(pd.DataFrame(detail["attribution_history"]), hide_index=True)
        with st.expander("Identity evidence"):
            st.dataframe(pd.DataFrame(detail["identity_links"]), hide_index=True)
else:
    st.info("No paid orders for this selection.")

st.subheader("Data quality and freshness")
freshness = status.get("freshness", {})
job_counts = status.get("jobs", {})
counts = status.get("counts", {})
q1, q2, q3 = st.columns(3)
q1.metric("Raw events", f"{counts.get('raw_events', 0):,}")
q2.metric("Normalized events", f"{counts.get('events', 0):,}")
q3.metric("Current attributions", f"{counts.get('current_attributions', 0):,}")
st.caption(
    "Latest raw event: {event} · latest order webhook: {order} · latest spend: {spend} · "
    "latest completed job: {job}".format(
        event=freshness.get("latest_raw_event_received_at") or "n/a",
        order=freshness.get("latest_raw_order_received_at") or "n/a",
        spend=freshness.get("latest_raw_spend_received_at") or "n/a",
        job=freshness.get("latest_completed_job_at") or "n/a",
    )
)
active_jobs = {key: value for key, value in job_counts.items() if key != "completed"}
if active_jobs:
    st.caption("Open jobs: " + ", ".join(f"{key}={value}" for key, value in sorted(active_jobs.items())))
issues = status.get("data_quality", [])
if issues:
    st.dataframe(pd.DataFrame(issues), hide_index=True, use_container_width=True)
else:
    st.caption("No data-quality issues recorded.")
st.caption(
    f"Reporting timezone: {status.get('reporting_timezone')} · "
    f"Model: {status.get('attribution_model')} · Lookback: {status.get('lookback_days')} days"
)
