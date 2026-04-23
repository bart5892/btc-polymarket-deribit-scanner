import ast
import re
from typing import Optional

import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title="BTC Polymarket Debugger", layout="wide")

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


def looks_like_price_question(text: str) -> bool:
    t = (text or "").lower()
    return any(x in t for x in ["above", "below", "over", "under", "reach", "hit"]) and (
        "$" in t or re.search(r"\b\d{2,3}(?:,\d{3})+\b", t)
    )


def parse_direction(text: str):
    t = (text or "").lower()
    if "above" in t or "over" in t:
        return "Above/Over"
    if "below" in t or "under" in t:
        return "Below/Under"
    if "reach" in t or "hit" in t:
        return "Reach/Hit"
    return ""


def parse_strike(text: str):
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
def fetch_deribit_summary():
    payload = safe_get(
        f"{DERIBIT_BASE}/public/get_book_summary_by_currency",
        params={"currency": "BTC", "kind": "option"},
    )
    return pd.DataFrame(payload.get("result", []))


def extract_market_rows(events):
    rows = []
    for event in events:
        event_title = event.get("title", "")
        event_slug = event.get("slug", "")
        event_id = event.get("id")
        markets = event.get("markets", []) or []

        for market in markets:
            question = market.get("question") or market.get("title") or ""
            combined_text = f"{event_title} || {question}"

            if not is_btc_text(combined_text):
                continue

            yes_mid = parse_yes_mid(market.get("outcomes"), market.get("outcomePrices"))

            row = {
                "event_id": event_id,
                "event_title": event_title,
                "event_slug": event_slug,
                "market_id": market.get("id"),
                "question": question,
                "combined_text": combined_text,
                "end_date": market.get("endDate"),
                "active": market.get("active"),
                "closed": market.get("closed"),
                "archived": market.get("archived"),
                "volume": market.get("volume") or market.get("volumeNum"),
                "liquidity": market.get("liquidity") or market.get("liquidityNum"),
                "yes_mid": yes_mid,
                "direction_guess": parse_direction(question),
                "strike_guess": parse_strike(question),
                "looks_like_price_question": looks_like_price_question(question),
                "market_slug": market.get("slug"),
                "outcomes": str(market.get("outcomes")),
                "outcome_prices": str(market.get("outcomePrices")),
            }
            rows.append(row)

    return pd.DataFrame(rows)


def main():
    st.title("BTC Polymarket Debugger")
    st.caption(
        "Debug build: show all live BTC-related Polymarket markets first, then inspect likely price-trigger contracts before tightening filters."
    )

    with st.sidebar:
        st.header("Controls")
        pages_to_pull = st.slider("Event pages to pull", min_value=1, max_value=5, value=3, step=1)
        events_per_page = st.slider("Events per page", min_value=50, max_value=200, value=100, step=50)
        show_only_price_like = st.checkbox("Show only likely BTC price-trigger questions", value=False)

    all_events = []
    for i in range(pages_to_pull):
        offset = i * events_per_page
        batch = fetch_active_events(limit=events_per_page, offset=offset)
        if not batch:
            break
        all_events.extend(batch)

    btc_df = extract_market_rows(all_events)

    deribit_df = fetch_deribit_summary()

    c1, c2, c3 = st.columns(3)
    c1.metric("Active events pulled", len(all_events))
    c2.metric("BTC-related markets found", len(btc_df))
    c3.metric("Deribit BTC options loaded", len(deribit_df))

    if btc_df.empty:
        st.warning("No BTC-related markets were found in the pulled event sample.")
        return

    raw_df = btc_df.copy()

    if show_only_price_like:
        raw_df = raw_df[raw_df["looks_like_price_question"] == True].copy()

    raw_df = raw_df.sort_values(
        by=["looks_like_price_question", "liquidity", "volume"],
        ascending=[False, False, False],
    )

    st.subheader("Raw BTC-related Polymarket markets")
    st.dataframe(
        raw_df[
            [
                "event_title",
                "question",
                "direction_guess",
                "strike_guess",
                "looks_like_price_question",
                "yes_mid",
                "liquidity",
                "volume",
                "end_date",
                "market_slug",
            ]
        ],
        use_container_width=True,
        hide_index=True,
    )

    likely_df = btc_df[btc_df["looks_like_price_question"] == True].copy()

    st.subheader("Likely BTC price-trigger subset")
    if likely_df.empty:
        st.info("No likely BTC price-trigger markets were detected in this sample.")
    else:
        likely_df = likely_df.sort_values(by=["liquidity", "volume"], ascending=[False, False])
        st.dataframe(
            likely_df[
                [
                    "event_title",
                    "question",
                    "direction_guess",
                    "strike_guess",
                    "yes_mid",
                    "liquidity",
                    "volume",
                    "end_date",
                    "market_slug",
                ]
            ],
            use_container_width=True,
            hide_index=True,
        )

    with st.expander("Debug notes"):
        st.markdown(
            """
- This app intentionally shows **all BTC-related live Polymarket markets** first.
- The purpose is to inspect the actual wording Polymarket is using right now.
- Once we confirm the real title patterns, we can tighten the parser for above/below/reach/hit contracts.
- Deribit is loaded only as a sanity check in this debugging build.
            """
        )

    with st.expander("Example raw titles"):
        sample_titles = btc_df["question"].dropna().astype(str).head(50).tolist()
        for t in sample_titles:
            st.write("-", t)


if __name__ == "__main__":
    main()
