import ast
import json
import math
import re
from datetime import datetime, timezone
from typing import Optional

import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title="BTC Polymarket Deribit Scanner vNext", layout="wide")

GAMMA_BASE = "https://gamma-api.polymarket.com"
CLOB_BASE = "https://clob.polymarket.com"
DERIBIT_BASE = "https://www.deribit.com/api/v2"
DEFAULT_NOTIONAL_USD = 10000.0


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
            try:
                return json.loads(value)
            except Exception:
                return []
    return []


def first_numeric(value, default=None):
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def best_bid_ask_from_book(book):
    bids = book.get("bids", []) or []
    asks = book.get("asks", []) or []

    best_bid = None
    best_bid_size = None
    best_ask = None
    best_ask_size = None

    if bids:
        best_bid = first_numeric(bids[0].get("price"))
        best_bid_size = first_numeric(bids[0].get("size"))

    if asks:
        best_ask = first_numeric(asks[0].get("price"))
        best_ask_size = first_numeric(asks[0].get("size"))

    return best_bid, best_bid_size, best_ask, best_ask_size


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


def parse_token_ids(market) -> list:
    candidates = [
        market.get("clobTokenIds"),
        market.get("clobTokenIdsStr"),
        market.get("outcomeTokenIds"),
        market.get("tokenIds"),
    ]
    for c in candidates:
        parsed = parse_maybe_list(c)
        if parsed and len(parsed) >= 2:
            return [str(parsed[0]), str(parsed[1])]
    return []


def parse_outcomes(market) -> list:
    candidates = [
        market.get("outcomes"),
        market.get("outcomeNames"),
    ]
    for c in candidates:
        parsed = parse_maybe_list(c)
        if parsed and len(parsed) >= 2:
            return [str(x) for x in parsed]
    return ["Yes", "No"]


def map_yes_no_tokens(market):
    token_ids = parse_token_ids(market)
    outcomes = parse_outcomes(market)

    yes_token = None
    no_token = None

    if len(token_ids) >= 2 and len(outcomes) >= 2:
        for outcome, token in zip(outcomes, token_ids):
            o = str(outcome).strip().lower()
            if o == "yes":
                yes_token = token
            elif o == "no":
                no_token = token

    if yes_token is None and len(token_ids) >= 1:
        yes_token = token_ids[0]
    if no_token is None and len(token_ids) >= 2:
        no_token = token_ids[1]

    return yes_token, no_token


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


@st.cache_data(ttl=60)
def fetch_polymarket_book(token_id: str):
    if not token_id:
        return {}
    try:
        return safe_get(f"{CLOB_BASE}/book", params={"token_id": token_id})
    except Exception:
        return {}


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
            end_date = parse_end_date(market.get("endDate"))
            yes_token, no_token = map_yes_no_tokens(market)

            rows.append(
                {
                    "event_title": event_title,
                    "event_slug": event_slug,
                    "question": question,
                    "market_slug": market.get("slug"),
                    "market_id": market.get("id"),
                    "category": category,
                    "strike": strike,
                    "liquidity": pd.to_numeric(
                        market.get("liquidity") or market.get("liquidityNum"),
                        errors="coerce",
                    ),
                    "volume": pd.to_numeric(
                        market.get("volume") or market.get("volumeNum"),
                        errors="coerce",
                    ),
                    "end_date": end_date,
                    "looks_like_price_trigger": looks_like_price_trigger(question),
                    "yes_token_id": yes_token,
                    "no_token_id": no_token,
                }
            )

    df = pd.DataFrame(rows)
    if df.empty:
        return df

    df = df[df["looks_like_price_trigger"] == True].copy()
    df = df[df["strike"].notna()].copy()
    df = df[df["category"].notna()].copy()

    poly_rows = []
    for _, row in df.iterrows():
        yes_book = fetch_polymarket_book(row["yes_token_id"]) if pd.notna(row["yes_token_id"]) else {}
        no_book = fetch_polymarket_book(row["no_token_id"]) if pd.notna(row["no_token_id"]) else {}

        yes_bid, yes_bid_size, yes_ask, yes_ask_size = best_bid_ask_from_book(yes_book)
        no_bid, no_bid_size, no_ask, no_ask_size = best_bid_ask_from_book(no_book)

        out = row.to_dict()
        out.update(
            {
                "poly_yes_bid": yes_bid,
                "poly_yes_bid_size": yes_bid_size,
                "poly_yes_ask": yes_ask,
                "poly_yes_ask_size": yes_ask_size,
                "poly_no_bid": no_bid,
                "poly_no_bid_size": no_bid_size,
                "poly_no_ask": no_ask,
                "poly_no_ask_size": no_ask_size,
            }
        )
        poly_rows.append(out)

    return pd.DataFrame(poly_rows).reset_index(drop=True)


def build_deribit_df() -> pd.DataFrame:
    instruments = pd.DataFrame(fetch_deribit_instruments())
    summaries = pd.DataFrame(fetch_deribit_summaries())

    if instruments.empty or summaries.empty:
        return pd.DataFrame()

    keep_cols = ["instrument_name", "expiration_timestamp", "strike", "option_type"]
    instruments = instruments[[c for c in keep_cols if c in instruments.columns]].copy()

    merged = instruments.merge(summaries, on="instrument_name", how="left")

    merged["expiration_dt"] = pd.to_datetime(
        merged["expiration_timestamp"], unit="ms", utc=True, errors="coerce"
    )
    merged["strike"] = pd.to_numeric(merged["strike"], errors="coerce")
    merged["mid_price"] = pd.to_numeric(merged.get("mid_price"), errors="coerce")
    merged["bid_price"] = pd.to_numeric(merged.get("bid_price"), errors="coerce")
    merged["ask_price"] = pd.to_numeric(merged.get("ask_price"), errors="coerce")
    merged["underlying_price"] = pd.to_numeric(merged.get("underlying_price"), errors="coerce")
    merged["open_interest"] = pd.to_numeric(merged.get("open_interest"), errors="coerce")
    merged["volume"] = pd.to_numeric(merged.get("volume"), errors="coerce")

    return merged.reset_index(drop=True)


def empty_match():
    return {
        "matched_instrument": None,
        "matched_option_type": None,
        "matched_expiry": None,
        "matched_strike": None,
        "deribit_bid": None,
        "deribit_ask": None,
        "deribit_underlying": None,
        "expiry_gap_days": None,
        "strike_gap_pct": None,
        "match_score": None,
    }


def find_best_deribit_match(row, deribit_df):
    if deribit_df.empty or pd.isna(row["strike"]) or pd.isna(row["end_date"]):
        return empty_match()

    target_type = None
    if row["category"] in ["Above/Over", "Reach/Hit"]:
        target_type = "call"
    elif row["category"] == "Below/Under":
        target_type = "put"

    tmp = deribit_df.copy()
    if target_type is not None and "option_type" in tmp.columns:
        tmp = tmp[tmp["option_type"] == target_type].copy()

    if tmp.empty:
        return empty_match()

    target_end = row["end_date"]
    tmp["expiry_gap_days"] = (tmp["expiration_dt"] - target_end).abs().dt.total_seconds() / 86400.0
    tmp["strike_gap_pct"] = ((tmp["strike"] - row["strike"]).abs() / row["strike"]) * 100.0
    tmp["match_score"] = (
        tmp["expiry_gap_days"].fillna(9999) * 1.0
        + tmp["strike_gap_pct"].fillna(9999) * 2.0
    )

    tmp = tmp.sort_values(
        ["match_score", "expiry_gap_days", "strike_gap_pct"],
        ascending=[True, True, True],
    )

    if tmp.empty:
        return empty_match()

    best = tmp.iloc[0]
    return {
        "matched_instrument": best.get("instrument_name"),
        "matched_option_type": best.get("option_type"),
        "matched_expiry": best.get("expiration_dt"),
        "matched_strike": best.get("strike"),
        "deribit_bid": best.get("bid_price"),
        "deribit_ask": best.get("ask_price"),
        "deribit_underlying": best.get("underlying_price"),
        "expiry_gap_days": best.get("expiry_gap_days"),
        "strike_gap_pct": best.get("strike_gap_pct"),
        "match_score": best.get("match_score"),
    }


def choose_polymarket_leg(row):
    if row["category"] in ["Above/Over", "Reach/Hit", "Below/Under"]:
        return {
            "poly_side": "BUY YES",
            "poly_token_id": row.get("yes_token_id"),
            "poly_entry_price": row.get("poly_yes_ask"),
            "poly_exit_reference": row.get("poly_yes_bid"),
            "poly_price_label": "YES ask",
        }

    return {
        "poly_side": None,
        "poly_token_id": None,
        "poly_entry_price": None,
        "poly_exit_reference": None,
        "poly_price_label": None,
    }


def choose_deribit_leg(row):
    if row["category"] in ["Above/Over", "Reach/Hit"]:
        return {
            "deribit_side": "BUY CALL",
            "deribit_entry_price": row.get("deribit_ask"),
        }
    if row["category"] == "Below/Under":
        return {
            "deribit_side": "BUY PUT",
            "deribit_entry_price": row.get("deribit_ask"),
        }

    return {
        "deribit_side": None,
        "deribit_entry_price": None,
    }


def compute_trade_metrics(row, notional_usd):
    poly = choose_polymarket_leg(row)
    der = choose_deribit_leg(row)

    poly_entry = first_numeric(poly["poly_entry_price"])
    deribit_entry_btc = first_numeric(der["deribit_entry_price"])
    spot = first_numeric(row.get("deribit_underlying"))

    poly_shares = None
    poly_cost_usd = None
    poly_gross_payout_usd = None
    poly_net_payoff_usd = None

    if poly_entry is not None and poly_entry > 0:
        poly_shares = notional_usd / poly_entry
        poly_cost_usd = poly_shares * poly_entry
        poly_gross_payout_usd = poly_shares * 1.0
        poly_net_payoff_usd = poly_gross_payout_usd - poly_cost_usd

    deribit_btc_amount = None
    deribit_premium_usd = None
    deribit_contracts = None

    if deribit_entry_btc is not None and deribit_entry_btc > 0 and spot is not None and spot > 0:
        deribit_premium_usd = deribit_entry_btc * spot
        deribit_btc_amount = notional_usd / spot
        deribit_contracts = deribit_btc_amount

    total_cost_proxy = 0.0
    if poly_cost_usd is not None:
        total_cost_proxy += poly_cost_usd
    if deribit_premium_usd is not None and deribit_btc_amount is not None:
        total_cost_proxy += deribit_entry_btc * deribit_btc_amount * spot

    event_payoff_probability_proxy = None
    if poly_entry is not None:
        event_payoff_probability_proxy = 1.0 - poly_entry

    trade_expression = None
    if (
        poly["poly_side"]
        and poly_entry is not None
        and poly_shares is not None
        and der["deribit_side"]
        and deribit_entry_btc is not None
        and deribit_btc_amount is not None
        and row.get("matched_instrument")
    ):
        trade_expression = (
            f'{poly["poly_side"]} on Polymarket @ {poly_entry:.3f} '
            f'for ~{poly_shares:,.0f} shares; '
            f'{der["deribit_side"]} {row.get("matched_instrument")} @ {deribit_entry_btc:.4f} BTC '
            f'for ~{deribit_btc_amount:.4f} BTC notional'
        )

    return {
        "poly_side": poly["poly_side"],
        "poly_token_id": poly["poly_token_id"],
        "poly_entry_price": poly_entry,
        "poly_exit_reference": poly["poly_exit_reference"],
        "poly_shares_for_10k": poly_shares,
        "poly_cost_usd_for_10k": poly_cost_usd,
        "poly_gross_payout_usd_for_10k": poly_gross_payout_usd,
        "poly_net_payoff_usd_for_10k": poly_net_payoff_usd,
        "deribit_side": der["deribit_side"],
        "deribit_entry_price_btc": deribit_entry_btc,
        "deribit_option_premium_usd_per_btc": deribit_premium_usd,
        "deribit_contract_size_btc": 1.0 if deribit_entry_btc is not None else None,
        "deribit_contracts_for_10k": deribit_contracts,
        "combined_cost_proxy_usd_for_10k": total_cost_proxy if total_cost_proxy > 0 else None,
        "payoff_probability_proxy": event_payoff_probability_proxy,
        "trade_expression": trade_expression,
    }


def main():
    st.title("BTC Polymarket Deribit Scanner vNext")
    st.caption(
        "Uses sided pricing on Polymarket and Deribit, adds a $10,000 notional framework, "
        "and generates executable trade expressions. Ranking remains a proxy until a full "
        "terminal-event payoff model is added."
    )

    with st.sidebar:
        st.header("Filters")
        notional_usd = st.number_input(
            "USD notional for sizing",
            min_value=1000.0,
            value=DEFAULT_NOTIONAL_USD,
            step=1000.0,
        )
        min_liquidity = st.number_input(
            "Minimum liquidity", min_value=0.0, value=5000.0, step=1000.0
        )
        min_volume = st.number_input(
            "Minimum volume", min_value=0.0, value=1000.0, step=1000.0
        )
        categories = st.multiselect(
            "Contract type",
            options=["Above/Over", "Below/Under", "Reach/Hit"],
            default=["Above/Over", "Below/Under", "Reach/Hit"],
        )
        max_expiry_gap_days = st.slider(
            "Max Deribit expiry gap (days)", min_value=1, max_value=30, value=10
        )
        max_strike_gap_pct = st.slider(
            "Max Deribit strike gap (%)", min_value=1, max_value=50, value=15
        )

    with st.spinner("Loading Polymarket markets and order books..."):
        poly_df = build_polymarket_df()

    with st.spinner("Loading Deribit instruments and summaries..."):
        deribit_df = build_deribit_df()

    c1, c2, c3 = st.columns(3)
    c1.metric("Polymarket trigger markets", len(poly_df))
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

    if scanner_df.empty:
        st.info("No rows passed the current filters.")
        return

    metrics = scanner_df.apply(
        lambda row: pd.Series(compute_trade_metrics(row, notional_usd)),
        axis=1,
    )
    scanner_df = pd.concat([scanner_df, metrics], axis=1)

    scanner_df = scanner_df[scanner_df["poly_entry_price"].notna()].copy()
    scanner_df = scanner_df[scanner_df["deribit_entry_price_btc"].notna()].copy()

    if scanner_df.empty:
        st.info("No rows have both executable Polymarket and Deribit sided prices.")
        return

    scanner_df["rank_score"] = (
        scanner_df["payoff_probability_proxy"].fillna(-999) * 1000
        + scanner_df["poly_net_payoff_usd_for_10k"].fillna(-999999) / 100
        - scanner_df["match_score"].fillna(9999) * 5
        + scanner_df["liquidity"].fillna(0) / 100000
        + scanner_df["volume"].fillna(0) / 100000
    )

    scanner_df = scanner_df.sort_values(
        by=["rank_score", "payoff_probability_proxy", "poly_net_payoff_usd_for_10k"],
        ascending=[False, False, False],
    ).reset_index(drop=True)

    st.subheader("Top executable opportunities")

    top_display = scanner_df.head(15)[
        [
            "question",
            "category",
            "poly_side",
            "poly_entry_price",
            "poly_shares_for_10k",
            "poly_net_payoff_usd_for_10k",
            "deribit_side",
            "matched_instrument",
            "deribit_entry_price_btc",
            "deribit_contracts_for_10k",
            "payoff_probability_proxy",
            "trade_expression",
        ]
    ].copy()

    top_display["poly_entry_price"] = top_display["poly_entry_price"].map(
        lambda x: f"{x:.3f}" if pd.notna(x) else ""
    )
    top_display["poly_shares_for_10k"] = top_display["poly_shares_for_10k"].map(
        lambda x: f"{x:,.0f}" if pd.notna(x) else ""
    )
    top_display["poly_net_payoff_usd_for_10k"] = top_display["poly_net_payoff_usd_for_10k"].map(
        lambda x: f"${x:,.0f}" if pd.notna(x) else ""
    )
    top_display["deribit_entry_price_btc"] = top_display["deribit_entry_price_btc"].map(
        lambda x: f"{x:.4f}" if pd.notna(x) else ""
    )
    top_display["deribit_contracts_for_10k"] = top_display["deribit_contracts_for_10k"].map(
        lambda x: f"{x:.4f}" if pd.notna(x) else ""
    )
    top_display["payoff_probability_proxy"] = top_display["payoff_probability_proxy"].map(
        lambda x: f"{x:.1%}" if pd.notna(x) else ""
    )

    st.dataframe(top_display, use_container_width=True, hide_index=True)

    st.subheader("Full scanner table")

    display = scanner_df[
        [
            "question",
            "category",
            "strike",
            "end_date",
            "poly_yes_bid",
            "poly_yes_ask",
            "poly_no_bid",
            "poly_no_ask",
            "poly_side",
            "poly_entry_price",
            "poly_shares_for_10k",
            "poly_net_payoff_usd_for_10k",
            "matched_instrument",
            "matched_option_type",
            "matched_expiry",
            "matched_strike",
            "deribit_bid",
            "deribit_ask",
            "deribit_side",
            "deribit_entry_price_btc",
            "deribit_contracts_for_10k",
            "payoff_probability_proxy",
            "expiry_gap_days",
            "strike_gap_pct",
            "liquidity",
            "volume",
            "trade_expression",
        ]
    ].copy()

    display["strike"] = display["strike"].map(lambda x: f"${x:,.0f}" if pd.notna(x) else "")
    display["poly_yes_bid"] = display["poly_yes_bid"].map(lambda x: f"{x:.3f}" if pd.notna(x) else "")
    display["poly_yes_ask"] = display["poly_yes_ask"].map(lambda x: f"{x:.3f}" if pd.notna(x) else "")
    display["poly_no_bid"] = display["poly_no_bid"].map(lambda x: f"{x:.3f}" if pd.notna(x) else "")
    display["poly_no_ask"] = display["poly_no_ask"].map(lambda x: f"{x:.3f}" if pd.notna(x) else "")
    display["poly_entry_price"] = display["poly_entry_price"].map(lambda x: f"{x:.3f}" if pd.notna(x) else "")
    display["poly_shares_for_10k"] = display["poly_shares_for_10k"].map(
        lambda x: f"{x:,.0f}" if pd.notna(x) else ""
    )
    display["poly_net_payoff_usd_for_10k"] = display["poly_net_payoff_usd_for_10k"].map(
        lambda x: f"${x:,.0f}" if pd.notna(x) else ""
    )
    display["matched_strike"] = display["matched_strike"].map(
        lambda x: f"${x:,.0f}" if pd.notna(x) else ""
    )
    display["deribit_bid"] = display["deribit_bid"].map(lambda x: f"{x:.4f}" if pd.notna(x) else "")
    display["deribit_ask"] = display["deribit_ask"].map(lambda x: f"{x:.4f}" if pd.notna(x) else "")
    display["deribit_entry_price_btc"] = display["deribit_entry_price_btc"].map(
        lambda x: f"{x:.4f}" if pd.notna(x) else ""
    )
    display["deribit_contracts_for_10k"] = display["deribit_contracts_for_10k"].map(
        lambda x: f"{x:.4f}" if pd.notna(x) else ""
    )
    display["payoff_probability_proxy"] = display["payoff_probability_proxy"].map(
        lambda x: f"{x:.1%}" if pd.notna(x) else ""
    )
    display["expiry_gap_days"] = display["expiry_gap_days"].map(
        lambda x: f"{x:.1f}" if pd.notna(x) else ""
    )
    display["strike_gap_pct"] = display["strike_gap_pct"].map(
        lambda x: f"{x:.1f}%" if pd.notna(x) else ""
    )
    display["liquidity"] = display["liquidity"].map(lambda x: f"{x:,.0f}" if pd.notna(x) else "")
    display["volume"] = display["volume"].map(lambda x: f"{x:,.0f}" if pd.notna(x) else "")
    display["end_date"] = pd.to_datetime(
        display["end_date"], utc=True, errors="coerce"
    ).dt.strftime("%Y-%m-%d %H:%M UTC")
    display["matched_expiry"] = pd.to_datetime(
        display["matched_expiry"], utc=True, errors="coerce"
    ).dt.strftime("%Y-%m-%d %H:%M UTC")

    st.dataframe(display, use_container_width=True, hide_index=True)

    st.subheader("Method notes")
    st.markdown(
        """
- Polymarket uses YES/NO outcome tokens for binary questions, and order books are queried by token ID.
- The app uses executable sided prices from Polymarket order books and Deribit option bid/ask fields instead of midpoint pricing.
- BTC inverse options on Deribit use a 1 BTC contract size, so the sizing field is expressed in BTC notional terms.
- The $10,000 payoff column is currently a binary-contract-style payoff proxy for the Polymarket leg.
- Ranking is still a proxy and should be upgraded later to a true event-aligned terminal payoff model for the Deribit leg.
        """
    )


if __name__ == "__main__":
    main()
