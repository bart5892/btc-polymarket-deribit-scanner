import re
from typing import List, Optional

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


@st.cache_data(ttl=120)
def fetch_polymarket_events(limit: int = 200) -> list:
    return safe_get(
        f"{GAMMA_BASE}/events",
        params={
            "active": "true",
            "closed": "false",
            "limit": limit,
        },
    )


def extract_markets_from_events(events: list) -> list:
    markets = []
    for event in events:
        event_title = event.get("title", "")
        for market in event.get("markets", []) or []:
            item = {
                "event_title": event_title,
                "market_id": market.get("id"),
                "question": market.get("question") or market.get("title") or "",
                "end_date_iso": market.get("endDate"),
                "volume": market.get("volume") or market.get("volumeNum"),
                "liquidity": market.get("liquidity") or market.get("liquidityNum"),
                "slug": market.get("slug"),
                "outcomes": market.get("outcomes"),
                "outcome_prices": market.get("outcomePrices"),
            }
            markets.append(item)
    return markets


def parse_yes_mid(outcomes, outcome_prices) -> Optional[float]:
    try:
        if isinstance(outcomes, str):
            outcomes = eval(outcomes)
        if isinstance(outcome_prices, str):
            outcome_prices = eval(outcome_prices)
        if not outcomes or not outcome_prices:
            return None
        for outcome, price in zip(outcomes, outcome_prices):
            if str(outcome).strip().lower() == "yes":
                return float(price)
    except Exception:
        return None
    return None


def is_btc_market(text: str) -> bool:
    t = (text or "").lower()
    return "bitcoin" in t or "btc" in t


def is_above_below_market(text: str) -> bool:
    t = (text or "").lower()
    return ("above" in t or "below" in t) and "$" in t


def parse_direction(text: str) -> Optional[str]:
    t = (text or "").lower()
    if "above" in t:
        return "Above"
    if "below" in t:
        return "Below"
    return None


def parse_strike(text: str) -> Optional[float]:
    if not text:
        return None
    m = re.search(r"\$([\d,]+(?:\.\d+)?)", text)
    if not m:
        return None
    return float(m.group(1).replace(",", ""))


@st.cache_data(ttl=120)
def load_live_polymarket_btc_markets() -> pd.DataFrame:
    events = fetch_polymarket_events(limit=200)
    raw_markets = extract_markets_from_events(events)

    rows = []
    for m in raw_markets:
        question = m["question"]
        if not is_btc_market(question) and not is_btc_market(m["event_title"]):
            continue
        if not is_above_below_market(question):
            continue

        direction = parse_direction(question)
        strike = parse_strike(question)
        yes_mid = parse_yes_mid(m["outcomes"], m["outcome_prices"])

        if direction is None or strike is None:
            continue

        rows.append(
            {
                "title": question,
                "direction": direction,
                "strike": strike,
                "expiry": m["end_date_iso"],
                "polymarket_yes_mid": yes_mid,
                "volume": m["volume"],
                "liquidity": m["liquidity"],
                "slug": m["slug"],
            }
        )

    df = pd.DataFrame(rows)

    if df.empty:
        return df

    df = df.sort_values(by=["expiry", "strike"], ascending=[True, True]).reset_index(drop=True)
    return df


@st.cache_data(ttl=120)
def load_deribit_option_feed() -> pd.DataFrame:
    payload = safe_get(
        f"{DERIBIT_BASE}/public/get_book_summary_by_currency",
        params={"currency": "BTC", "kind": "option"},
    )
    results = payload.get("result", [])

    rows = []
    for x in results:
        rows.append(
            {
                "instrument_name": x.get("instrument_name"),
                "bid_price": x.get("bid_price"),
                "ask_price": x.get("ask_price"),
                "mid_price": x.get("mid_price"),
                "open_interest": x.get("open_interest"),
                "volume": x.get("volume"),
                "underlying_price": x.get("underlying_price"),
            }
        )

    return pd.DataFrame(rows)


def nearest_deribit_mid(strike: float, deribit_df: pd.DataFrame) -> Optional[float]:
    if deribit_df.empty or strike is None:
        return None

    tmp = deribit_df.copy()
    tmp = tmp[tmp["instrument_name"].astype(str).str.contains("-C|-P", regex=True, na=False)]
    if tmp.empty:
        return None

    extracted = tmp["instrument_name"].str.extract(r"BTC-\d{1,2}[A-Z]{3}\d{2}-(\d+)-([CP])")
    tmp["parsed_strike"] = pd.to_numeric(extracted[0], errors="coerce")
    tmp = tmp.dropna(subset=["parsed_strike"])
    if tmp.empty:
        return None

    tmp["distance"] = (tmp["parsed_strike"] - strike).abs()
    tmp = tmp.sort_values("distance")
    row = tmp.iloc[0]

    if pd.notna(row["mid_price"]):
        return float(row["mid_price"])
    if pd.notna(row["bid_price"]) and pd.notna(row["ask_price"]):
        return float((row["bid_price"] + row["ask_price"]) / 2)
    return None


def main():
    st.title("BTC Polymarket Deribit Scanner")
    st.caption(
        "Live BTC-related Polymarket markets plus a live Deribit options feed. Current Deribit column is a live placeholder feed, not yet a true terminal probability model."
    )

    left, right = st.columns([2, 1])

    with st.spinner("Loading live Polymarket BTC markets..."):
        poly_df = load_live_polymarket_btc_markets()

    with st.spinner("Loading live Deribit BTC options feed..."):
        deribit_df = load_deribit_option_feed()

    with st.sidebar:
        st.header("Filters")
        direction_filter = st.multiselect(
            "Direction",
            options=["Above", "Below"],
            default=["Above", "Below"],
        )
        min_liquidity = st.number_input("Minimum liquidity", min_value=0.0, value=0.0, step=100.0)
        min_volume = st.number_input("Minimum volume", min_value=0.0, value=0.0, step=100.0)

    if poly_df.empty:
        st.warning("No live BTC above/below Polymarket markets were found with the current filters.")
        return

    poly_df["polymarket_yes_mid"] = pd.to_numeric(poly_df["polymarket_yes_mid"], errors="coerce")
    poly_df["liquidity"] = pd.to_numeric(poly_df["liquidity"], errors="coerce")
    poly_df["volume"] = pd.to_numeric(poly_df["volume"], errors="coerce")

    poly_df["deribit_live_mid"] = poly_df["strike"].apply(lambda x: nearest_deribit_mid(x, deribit_df))

    filtered = poly_df[
        poly_df["direction"].isin(direction_filter)
        & (poly_df["liquidity"].fillna(0) >= min_liquidity)
        & (poly_df["volume"].fillna(0) >= min_volume)
    ].copy()

    filtered["live_gap_placeholder"] = filtered["deribit_live_mid"] - filtered["polymarket_yes_mid"]

    display = filtered[
        [
            "title",
            "direction",
            "strike",
            "expiry",
            "polymarket_yes_mid",
            "deribit_live_mid",
            "live_gap_placeholder",
            "volume",
            "liquidity",
            "slug",
        ]
    ].copy()

    display["strike"] = display["strike"].map(lambda x: f"${x:,.0f}" if pd.notna(x) else "")
    display["polymarket_yes_mid"] = display["polymarket_yes_mid"].map(lambda x: f"{x:.1%}" if pd.notna(x) else "")
    display["deribit_live_mid"] = display["deribit_live_mid"].map(lambda x: f"{x:.4f}" if pd.notna(x) else "")
    display["live_gap_placeholder"] = display["live_gap_placeholder"].map(lambda x: f"{x:+.4f}" if pd.notna(x) else "")
    display["volume"] = display["volume"].map(lambda x: f"{x:,.0f}" if pd.notna(x) else "")
    display["liquidity"] = display["liquidity"].map(lambda x: f"{x:,.0f}" if pd.notna(x) else "")

    with left:
        st.subheader("Live market table")
        st.dataframe(display, use_container_width=True, hide_index=True)

        st.subheader("How to read this")
        st.markdown(
            """
- **Polymarket yes mid** is live Polymarket market pricing derived from the Yes outcome price.
- **Deribit live mid** is currently a live placeholder value from the nearest BTC option instrument by strike.
- **Live gap placeholder** is not yet a valid probability edge; it is only a temporary live-feed comparison column.
- The next step is to replace this placeholder with a true terminal probability proxy from Deribit.
            """
        )

    with right:
        st.subheader("Summary")
        st.metric("Live Polymarket BTC markets", len(filtered))
        st.metric("Deribit BTC options loaded", len(deribit_df))
        st.metric(
            "Avg Polymarket yes mid",
            f"{filtered['polymarket_yes_mid'].mean():.1%}" if len(filtered) and filtered["polymarket_yes_mid"].notna().any() else "N/A",
        )

        st.subheader("Next build")
        st.markdown(
            """
1. Tighten title parsing for only true BTC above/below contracts.
2. Map each market to the nearest Deribit expiry.
3. Build terminal digital probability approximation.
4. Replace placeholder live gap with modeled probability edge.
            """
        )


if __name__ == "__main__":
    main()
