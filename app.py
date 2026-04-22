import pandas as pd
import streamlit as st

st.set_page_config(
    page_title="BTC Polymarket Deribit Scanner",
    layout="wide"
)


@st.cache_data
def load_sample_data() -> pd.DataFrame:
    data = [
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

    df = pd.DataFrame(data)
    df["edge"] = df["deribit_proxy_prob"] - df["polymarket_yes_mid"]
    df["edge_bps"] = (df["edge"] * 10000).round(0).astype(int)
    df["view"] = df["edge"].apply(
        lambda x: "Polymarket cheap" if x > 0 else "Polymarket rich"
    )
    return df


def main():
    st.title("BTC Polymarket Deribit Scanner")
    st.caption(
        "Phase 1 prototype: BTC above/below markets only, using sample data and a placeholder Deribit terminal probability proxy."
    )

    df = load_sample_data()

    with st.sidebar:
        st.header("Filters")

        direction_filter = st.multiselect(
            "Direction",
            options=["Above", "Below"],
            default=["Above", "Below"],
        )

        max_spread = st.slider(
            "Max spread",
            min_value=0.01,
            max_value=0.10,
            value=0.05,
            step=0.01,
        )

        min_abs_edge = st.slider(
            "Minimum absolute edge",
            min_value=0.00,
            max_value=0.10,
            value=0.00,
            step=0.01,
        )

    filtered = df[
        (df["direction"].isin(direction_filter))
        & (df["spread"] <= max_spread)
        & (df["edge"].abs() >= min_abs_edge)
    ].copy()

    st.subheader("Market Table")

    display_df = filtered[
        [
            "title",
            "direction",
            "strike",
            "expiry",
            "polymarket_yes_mid",
            "deribit_proxy_prob",
            "edge",
            "edge_bps",
            "spread",
            "liquidity_flag",
            "view",
        ]
    ].copy()

    display_df["strike"] = display_df["strike"].map(lambda x: f"${x:,.0f}")
    display_df["polymarket_yes_mid"] = display_df["polymarket_yes_mid"].map(lambda x: f"{x:.1%}")
    display_df["deribit_proxy_prob"] = display_df["deribit_proxy_prob"].map(lambda x: f"{x:.1%}")
    display_df["edge"] = filtered["edge"].map(lambda x: f"{x:+.1%}")
    display_df["spread"] = display_df["spread"].map(lambda x: f"{x:.1%}")

    st.dataframe(display_df, use_container_width=True, hide_index=True)

    st.subheader("Summary")

    c1, c2, c3 = st.columns(3)

    c1.metric("Markets shown", len(filtered))

    avg_mid = filtered["polymarket_yes_mid"].mean() if len(filtered) else 0
    avg_edge = filtered["edge"].mean() if len(filtered) else 0

    c2.metric("Average Polymarket mid", f"{avg_mid:.1%}" if len(filtered) else "N/A")
    c3.metric("Average edge", f"{avg_edge:+.1%}" if len(filtered) else "N/A")

    st.subheader("How to Read This")
    st.markdown(
        """
- **Polymarket yes mid** is the sample market-implied probability.
- **Deribit proxy prob** is a placeholder for the terminal probability proxy you will later calculate from listed BTC options.
- **Edge** is Deribit proxy minus Polymarket yes mid.
- Positive edge means the Polymarket yes price looks cheaper than the Deribit proxy.
- Negative edge means the Polymarket yes price looks richer than the Deribit proxy.
        """
    )

    st.subheader("Build Roadmap")
    st.markdown(
        """
1. Replace the sample table with real BTC Polymarket market ingestion.
2. Keep only BTC contracts with clear terminal above/below wording.
3. Parse strike, expiry, and direction from title and rules.
4. Pull Deribit instruments and approximate terminal probabilities around strike.
5. Rank by edge, spread, and confidence.
        """
    )


if __name__ == "__main__":
    main()
