BTC Scanner Fresh Deploy
import streamlit as st
st.set_page_config(page_title="SCANNER TEST V9", layout="wide")
st.title("SCANNER TEST V9")
st.write("If you see this, Railway is deploying the newest app.py.")
st.stop()

st.set_page_config(page_title="BTC Scanner V5", layout="wide")
st.title("BTC Scanner V5")
st.caption("SCANNER V5 - live matched scanner build")
import ast
import math
import re
from datetime import datetime, timezone
from typing import Optional

import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title="BTC Polymarket Deribit Scanner", layout="wide")

GAMMA_BASE = "https://gamma-api.polymarket.com"
DERIBIT_BASE = "https://www.deribit.com/api/v2"


def safe_get(url: str, params: Optional[dict] = None):
    r = requests.get(url, params=params, timeout=20)
    r.raise_for_status()
    return r.json()


def parse_maybe_list(value):
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            return ast.literal_eval(value)
        except Exception:
            return []
    return []


def parse_yes_mid(outcomes, outcome_prices):
    outcomes = parse_maybe_list(outcomes)
    outcome_prices = parse_maybe_list(outcome_prices)
    if not outcomes or not outcome_prices:
        return None
    for outcome, price in zip(outcomes, outcome_prices):
        if str(outcome).strip().lower() == "yes":
            try:
                return float(price)
            except Exception:
                return None
    return None


def is_btc_text(text: str) -> bool:
    t = (text or "").lower()
    return any(x in t for x in ["bitcoin", "btc", "xbt"])


def classify_question(text: str) -> Optional[str]:
    t = (text or "").lower()
    if "above" in t or "over" in t:
        return "Above/Over"
    if "below" in t or "under" in t:
        return "Below/Under"
    if "reach" in t or "hit" in t:
        return "Reach/Hit"
    return None


def looks_like_price_trigger(text: str) -> bool:
    t = (text or "").lower()
    has_price_word = any(x in t for x in ["above", "below", "over", "under", "reach", "hit"])
    has_numeric_level = ("$" in t) or bool(re.search(r"\b\d{2,3}(?:,\d{3})+\b", t))
    return has_price_word and has_numeric_level


def parse_strike(text: str) -> Optional[float]:
    if not text:
        return None
    m = re.search(r"\$([\d,]+(?:\.\d+)?)", text)
    if not m:
        m = re.search(r"\b([\d]{2,3}(?:,[\d]{3})+)\b", text)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except Exception:
        return None


def parse_end_date(value) -> Optional[pd.Timestamp]:
    try:
        if not value:
            return None
        return pd.to_datetime(value, utc=True)
    except Exception:
        return None


@st.cache_data(ttl=120)
def fetch_active_events(limit=200, offset=0):
    return safe_get(
        f"{GAMMA_BASE}/events",
        params={
            "active": "true",
            "closed": "false",
            "limit": limit,
            "offset": offset,
            "order": "volume24hr",
            "ascending": "false",
        },
    )


@st.cache_data(ttl=120)
def fetch_deribit_instruments():
    payload = safe_get(
        f"{DERIBIT_BASE}/public/get_instruments",
        params={"currency": "BTC", "kind": "option", "expired": "false"},
    )
    return payload.get("result", [])


@st.cache_data(ttl=120)
def fetch_deribit_summaries():
    payload = safe_get(
        f"{DERIBIT_BASE}/public/get_book_summary_by_currency",
        params={"currency": "BTC", "kind": "option"},
    )
    return payload.get("result", [])


def build_polymarket_df(pages_to_pull=3, events_per_page=100) -> pd.DataFrame:
    all_events = []
    for i in range(pages_to_pull):
        batch = fetch_active_events(limit=events_per_page, offset=i * events_per_page)
        if not batch:
            break
        all_events.extend(batch)

    rows = []
    for event in all_events:
        event_title = event.get("title", "")
        event_slug = event.get("slug", "")
        for market in event.get("markets", []) or []:
            question = market.get("question") or market.get("title") or ""
            combined = f"{event_title} || {question}"
            if not is_btc_text(combined):
                continue

            category = classify_question(question)
            strike = parse_strike(question)
            yes_mid = parse_yes_mid(market.get("outcomes"), market.get("outcomePrices"))
            end_date = parse_end_date(market.get("endDate"))

            rows.append(
                {
                    "event_title": event_title,
                    "event_slug": event_slug,
                    "question": question,
                    "market_slug": market.get("slug"),
                    "category": category,
                    "strike": strike,
                    "yes_mid": yes_mid,
                    "liquidity": pd.to_numeric(market.get("liquidity") or market.get("liquidityNum"), errors="coerce"),
                    "volume": pd.to_numeric(market.get("volume") or market.get("volumeNum"), errors="coerce"),
                    "end_date": end_date,
                    "looks_like_price_trigger": looks_like_price_trigger(question),
                }
            )

    df = pd.DataFrame(rows)
    if df.empty:
        return df

    df = df[df["looks_like_price_trigger"] == True].copy()
    df = df[df["strike"].notna()].copy()
    df = df[df["category"].notna()].copy()
    return df.reset_index(drop=True)


def build_deribit_df() -> pd.DataFrame:
    instruments = pd.DataFrame(fetch_deribit_instruments())
    summaries = pd.DataFrame(fetch_deribit_summaries())

    if instruments.empty or summaries.empty:
        return pd.DataFrame()

    keep_cols = ["instrument_name", "expiration_timestamp", "strike", "option_type"]
    instruments = instruments[[c for c in keep_cols if c in instruments.columns]].copy()

    merged = instruments.merge(summaries, on="instrument_name", how="left")

    merged["expiration_dt"] = pd.to_datetime(merged["expiration_timestamp"], unit="ms", utc=True, errors="coerce")
    merged["strike"] = pd.to_numeric(merged["strike"], errors="coerce")
    merged["mid_price"] = pd.to_numeric(merged.get("mid_price"), errors="coerce")
    merged["bid_price"] = pd.to_numeric(merged.get("bid_price"), errors="coerce")
    merged["ask_price"] = pd.to_numeric(merged.get("ask_price"), errors="coerce")
    merged["underlying_price"] = pd.to_numeric(merged.get("underlying_price"), errors="coerce")
    merged["open_interest"] = pd.to_numeric(merged.get("open_interest"), errors="coerce")
    merged["volume"] = pd.to_numeric(merged.get("volume"), errors="coerce")

    merged["usable_mid"] = merged["mid_price"]
    missing_mid = merged["usable_mid"].isna() & merged["bid_price"].notna() & merged["ask_price"].notna()
    merged.loc[missing_mid, "usable_mid"] = (merged.loc[missing_mid, "bid_price"] + merged.loc[missing_mid, "ask_price"]) / 2

    return merged.reset_index(drop=True)


def find_best_deribit_match(row, deribit_df):
    if deribit_df.empty or pd.isna(row["strike"]) or pd.isna(row["end_date"]):
        return {
            "matched_instrument": None,
            "matched_option_type": None,
            "matched_expiry": None,
            "matched_strike": None,
            "deribit_mid": None,
            "deribit_underlying": None,
            "expiry_gap_days": None,
            "strike_gap_pct": None,
            "match_score": None,
        }

    target_type = None
    if row["category"] in ["Above/Over", "Reach/Hit"]:
        target_type = "call"
    elif row["category"] == "Below/Under":
        target_type = "put"

    tmp = deribit_df.copy()
    if target_type is not None and "option_type" in tmp.columns:
        tmp = tmp[tmp["option_type"] == target_type].copy()

    if tmp.empty:
        return {
            "matched_instrument": None,
            "matched_option_type": None,
            "matched_expiry": None,
            "matched_strike": None,
            "deribit_mid": None,
            "deribit_underlying": None,
            "expiry_gap_days": None,
            "strike_gap_pct": None,
            "match_score": None,
        }

    target_end = row["end_date"]
    tmp["expiry_gap_days"] = (tmp["expiration_dt"] - target_end).abs().dt.total_seconds() / 86400.0
    tmp["strike_gap_pct"] = ((tmp["strike"] - row["strike"]).abs() / row["strike"]) * 100.0

    tmp["match_score"] = tmp["expiry_gap_days"].fillna(9999) * 1.0 + tmp["strike_gap_pct"].fillna(9999) * 2.0
    tmp = tmp.sort_values(["match_score", "expiry_gap_days", "strike_gap_pct"], ascending=[True, True, True])

    if tmp.empty:
        return {
            "matched_instrument": None,
            "matched_option_type": None,
            "matched_expiry": None,
            "matched_strike": None,
            "deribit_mid": None,
            "deribit_underlying": None,
            "expiry_gap_days": None,
            "strike_gap_pct": None,
            "match_score": None,
        }

    best = tmp.iloc[0]
    return {
        "matched_instrument": best.get("instrument_name"),
        "matched_option_type": best.get("option_type"),
        "matched_expiry": best.get("expiration_dt"),
        "matched_strike": best.get("strike"),
        "deribit_mid": best.get("usable_mid"),
        "deribit_underlying": best.get("underlying_price"),
        "expiry_gap_days": best.get("expiry_gap_days"),
        "strike_gap_pct": best.get("strike_gap_pct"),
        "match_score": best.get("match_score"),
    }


def main():
    st.title("BTC Polymarket Deribit Scanner")
    st.caption(
        "Live scanner for BTC price-trigger Polymarket markets with nearest Deribit option matching. Current Deribit output is a matching proxy, not yet a final terminal probability model."
    )

    with st.sidebar:
        st.header("Filters")
        min_liquidity = st.number_input("Minimum liquidity", min_value=0.0, value=5000.0, step=1000.0)
        min_volume = st.number_input("Minimum volume", min_value=0.0, value=1000.0, step=1000.0)
        categories = st.multiselect(
            "Contract type",
            options=["Above/Over", "Below/Under", "Reach/Hit"],
            default=["Above/Over", "Below/Under", "Reach/Hit"],
        )
        max_expiry_gap_days = st.slider("Max Deribit expiry gap (days)", min_value=1, max_value=30, value=10)
        max_strike_gap_pct = st.slider("Max Deribit strike gap (%)", min_value=1, max_value=50, value=15)

    with st.spinner("Loading Polymarket markets..."):
        poly_df = build_polymarket_df()

    with st.spinner("Loading Deribit instruments and summaries..."):
        deribit_df = build_deribit_df()

    c1, c2, c3 = st.columns(3)
    c1.metric("Polymarket price-trigger markets", len(poly_df))
    c2.metric("Deribit BTC options loaded", len(deribit_df))
    c3.metric("Scanner timestamp UTC", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"))

    if poly_df.empty:
        st.warning("No BTC price-trigger Polymarket markets found.")
        return

    matches = poly_df.apply(lambda row: pd.Series(find_best_deribit_match(row, deribit_df)), axis=1)
    scanner_df = pd.concat([poly_df, matches], axis=1)

    scanner_df = scanner_df[
        scanner_df["category"].isin(categories)
        & (scanner_df["liquidity"].fillna(0) >= min_liquidity)
        & (scanner_df["volume"].fillna(0) >= min_volume)
    ].copy()

    scanner_df = scanner_df[
        scanner_df["expiry_gap_days"].fillna(9999) <= max_expiry_gap_days
    ].copy()

    scanner_df = scanner_df[
        scanner_df["strike_gap_pct"].fillna(9999) <= max_strike_gap_pct
    ].copy()

    scanner_df["proxy_gap"] = scanner_df["deribit_mid"] - scanner_df["yes_mid"]
    scanner_df["rank_score"] = (
        scanner_df["proxy_gap"].fillna(-999) * 100
        + scanner_df["liquidity"].fillna(0) / 100000
        + scanner_df["volume"].fillna(0) / 100000
        - scanner_df["match_score"].fillna(9999) / 100
    )

    scanner_df = scanner_df.sort_values(
        by=["rank_score", "liquidity", "volume"],
        ascending=[False, False, False],
    ).reset_index(drop=True)

    st.subheader("Scanner table")

    display = scanner_df[
        [
            "question",
            "category",
            "strike",
            "end_date",
            "yes_mid",
            "matched_instrument",
            "matched_option_type",
            "matched_expiry",
            "matched_strike",
            "deribit_mid",
            "proxy_gap",
            "expiry_gap_days",
            "strike_gap_pct",
            "liquidity",
            "volume",
            "market_slug",
        ]
    ].copy()

    display["strike"] = display["strike"].map(lambda x: f"${x:,.0f}" if pd.notna(x) else "")
    display["yes_mid"] = display["yes_mid"].map(lambda x: f"{x:.2%}" if pd.notna(x) else "")
    display["matched_strike"] = display["matched_strike"].map(lambda x: f"${x:,.0f}" if pd.notna(x) else "")
    display["deribit_mid"] = display["deribit_mid"].map(lambda x: f"{x:.4f}" if pd.notna(x) else "")
    display["proxy_gap"] = display["proxy_gap"].map(lambda x: f"{x:+.4f}" if pd.notna(x) else "")
    display["expiry_gap_days"] = display["expiry_gap_days"].map(lambda x: f"{x:.1f}" if pd.notna(x) else "")
    display["strike_gap_pct"] = display["strike_gap_pct"].map(lambda x: f"{x:.1f}%" if pd.notna(x) else "")
    display["liquidity"] = display["liquidity"].map(lambda x: f"{x:,.0f}" if pd.notna(x) else "")
    display["volume"] = display["volume"].map(lambda x: f"{x:,.0f}" if pd.notna(x) else "")
    display["end_date"] = pd.to_datetime(display["end_date"], utc=True, errors="coerce").dt.strftime("%Y-%m-%d %H:%M UTC")
    display["matched_expiry"] = pd.to_datetime(display["matched_expiry"], utc=True, errors="coerce").dt.strftime("%Y-%m-%d %H:%M UTC")

    st.dataframe(display, use_container_width=True, hide_index=True)

    st.subheader("Method notes")
    st.markdown(
        """
- **Polymarket yes mid** comes from the Yes outcome price in the public Gamma market payload. [web:93]
- **Deribit match** uses live BTC option instruments and summary data, then selects the nearest expiry and strike candidate after matching calls to above/reach contracts and puts to below contracts. [web:144][web:147]
- **Proxy gap** is only a temporary comparison metric. It is not yet a true terminal digital probability edge.
- The next version should replace this proxy with a modeled terminal probability from Deribit options around the event expiry and strike.
        """
    )

    st.subheader("Top opportunities snapshot")
    if scanner_df.empty:
        st.info("No rows passed the current filters.")
    else:
        top = scanner_df.head(10)[
            ["question", "category", "yes_mid", "deribit_mid", "proxy_gap", "liquidity", "volume"]
        ].copy()
        top["yes_mid"] = top["yes_mid"].map(lambda x: f"{x:.2%}" if pd.notna(x) else "")
        top["deribit_mid"] = top["deribit_mid"].map(lambda x: f"{x:.4f}" if pd.notna(x) else "")
        top["proxy_gap"] = top["proxy_gap"].map(lambda x: f"{x:+.4f}" if pd.notna(x) else "")
        top["liquidity"] = top["liquidity"].map(lambda x: f"{x:,.0f}" if pd.notna(x) else "")
        top["volume"] = top["volume"].map(lambda x: f"{x:,.0f}" if pd.notna(x) else "")
        st.dataframe(top, use_container_width=True, hide_index=True)


if __name__ == "__main__":
    main()
