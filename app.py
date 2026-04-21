import pandas as pd
import streamlit as st

st.set_page_config(page_title="BTC Polymarket Deribit Scanner", layout="wide")


def load_sample_data() -> pd.DataFrame:
    rows = [
        {
            "title": "Will Bitcoin be above $95,000 on April 30, 2026?",
            "direction": "Above",
            "strike": 95000,
            "expiry": "2026-04-30 16:00 UTC",
            "polymarket_yes_mid": 0.58,
            "deribit_proxy_prob": 0.54,
            "spread": 0.03,
            "liquidity_flag": "Good",
        },
        {
            "title": "Will Bitcoin be above $100,000 on May 31, 2026?",
            "direction": "Above",
            "strike": 100000,
            "expiry": "2026-05-31 16:00 UTC",
            "polymarket_yes_mid": 0.34,
            "deribit_proxy_prob": 0.29,
            "spread": 0.04,
            "liquidity_flag": "Medium",
        },
        {
            "title": "Will Bitcoin be below $85,000 on May 15, 2026?",
            "direction": "Below",
            "strike": 85000,
            "expiry": "2026-05-15 16:00 UTC",
            "polymarket_yes_mid": 0.22,
            "deribit_proxy_prob": 0.27,
            "spread": 0.02,
            "liquidity_flag": "Good",
        },
        {
            "title": "Will Bitcoin be below $80,000 on June 30, 2026?",
            "direction": "Below",
            "strike": 80000,
            "expiry": "2026-06-30 16:00 UTC",
            "polymarket_yes_mid": 0.12,
            "deribit_proxy_prob": 0.18,
            "spread": 0.05,
            "liquidity_flag": "Low",
        },
        {
            "title": "Will Bitcoin be above $110,000 on June 30, 2026?",
            "direction": "Above",
            "strike": 110000,
            "expiry": "2026-06-30 16:00 UTC",
            "polymarket_yes_mid": 0.17,
            "deribit_proxy_prob": 0.21,
            "spread": 0.05,
            "liquidity_flag": "Medium",
        },
    ]

    df = pd.DataFrame(rows)
    df["edge"] = df["deribit_proxy_prob"] - df["polymarket_yes_mid"]
    df["edge_bps"] = (df["edge"] * 10000).round(0).astype(int)
    df["view"] = df["edge"].apply(lambda x: "Polymarket cheap" if x > 0 else "Polymarket rich")
    return df


def style_edge(val: float) -> str:
    if val > 0:
        return "color: #0a7f39; font-weight: 600;"
    if val < 0:
        return 
