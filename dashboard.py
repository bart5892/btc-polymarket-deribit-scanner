 (cd "$(git rev-parse --show-toplevel)" && git apply --3way <<'EOF' 
diff --git a/dashboard.py b/dashboard.py
new file mode 100644
index 0000000000000000000000000000000000000000..c868f11a18262774472e6727e4beef47409a3ab5
--- /dev/null
+++ b/dashboard.py
@@ -0,0 +1,450 @@
+import json
+import re
+import py_compile
+from datetime import datetime, timezone
+from pathlib import Path
+from typing import Dict, Optional
+from urllib.error import URLError, HTTPError
+from urllib.parse import urlencode
+from urllib.request import urlopen
+
+import altair as alt
+import numpy as np
+import pandas as pd
+import streamlit as st
+
+st.set_page_config(page_title="Polymarket vs Deribit Scanner", layout="wide")
+
+POLYMARKET_GAMMA_BASE = "https://gamma-api.polymarket.com"
+DERIBIT_API_BASE = "https://www.deribit.com/api/v2/public"
+ASSET_ALIASES = {
+    "Bitcoin": ["bitcoin", "btc"],
+    "Ethereum": ["ethereum", "eth"],
+    "Solana": ["solana", "sol"],
+    "XRP": ["xrp", "ripple"],
+}
+
+
+def _http_get_json(url: str, params: Optional[Dict[str, str]] = None, timeout: int = 20):
+    query = f"?{urlencode(params)}" if params else ""
+    final_url = f"{url}{query}"
+    with urlopen(final_url, timeout=timeout) as resp:
+        return json.loads(resp.read().decode("utf-8"))
+
+
+def _as_list(value):
+    if isinstance(value, list):
+        return value
+    if isinstance(value, str):
+        try:
+            parsed = json.loads(value)
+            return parsed if isinstance(parsed, list) else []
+        except json.JSONDecodeError:
+            return []
+    return []
+
+
+def _extract_yes_prob(market: dict) -> float:
+    # Common direct fields first.
+    for k in ["yesPrice", "yes_price", "probability", "lastTradePrice", "last_trade_price"]:
+        val = market.get(k)
+        if val is None:
+            continue
+        try:
+            v = float(val)
+            if 0 <= v <= 1:
+                return v
+        except (TypeError, ValueError):
+            pass
+
+    outcomes = _as_list(market.get("outcomes"))
+    outcome_prices = _as_list(market.get("outcomePrices"))
+
+    if outcomes and outcome_prices and len(outcomes) == len(outcome_prices):
+        # Standard Yes/No case.
+        for out, px in zip(outcomes, outcome_prices):
+            if str(out).strip().lower() == "yes":
+                try:
+                    return float(px)
+                except (TypeError, ValueError):
+                    break
+
+        # Fallback: for binary markets without explicit Yes labels, use max side prob.
+        if len(outcome_prices) == 2:
+            parsed = [float(x) for x in outcome_prices if str(x) not in {"", "None"}]
+            if len(parsed) == 2:
+                for v in parsed:
+                    if 0 <= v <= 1:
+                        return max(parsed)
+
+    return np.nan
+
+
+def _extract_strike(market: dict) -> float:
+    text_blobs = [str(market.get("question") or "")]
+    text_blobs.extend([str(x) for x in _as_list(market.get("outcomes"))])
+    text = " | ".join(text_blobs)
+
+    candidates = []
+    # Supports 65000, 65,000, 1.2, $70k, 70K.
+    for match in re.finditer(r"\$?(\d+(?:,\d{3})*(?:\.\d+)?)([kK]?)", text):
+        raw_num = match.group(1).replace(",", "")
+        suffix = match.group(2).lower()
+        try:
+            val = float(raw_num)
+        except ValueError:
+            continue
+        if suffix == "k":
+            val *= 1000
+
+        # Skip obvious years/tiny values that are unlikely strikes.
+        if 1900 <= val <= 2100:
+            continue
+        if val <= 0:
+            continue
+        candidates.append(val)
+
+    if not candidates:
+        return np.nan
+
+    # Take the largest candidate as most likely strike for "hit" markets.
+    return float(max(candidates))
+
+
+@st.cache_data(ttl=300, show_spinner=False)
+def fetch_polymarket_markets(asset: str, limit: int = 600):
+    params = {
+        "closed": "false",
+        "active": "true",
+        "archived": "false",
+        "limit": limit,
+    }
+    data = _http_get_json(f"{POLYMARKET_GAMMA_BASE}/markets", params=params)
+
+    aliases = ASSET_ALIASES.get(asset, [asset.lower()])
+    records = []
+    for m in data:
+        question = (m.get("question") or "").strip()
+        q_lower = question.lower()
+
+        if not any(alias in q_lower for alias in aliases):
+            continue
+
+        # Keep rows focused on price-target style markets but not overly strict.
+        if not any(kw in q_lower for kw in ["price", "hit", "above", "below", "between"]):
+            continue
+
+        end_dt = pd.to_datetime(m.get("endDate"), utc=True, errors="coerce")
+        volume = pd.to_numeric(m.get("volume"), errors="coerce")
+        yes_prob = _extract_yes_prob(m)
+        strike = _extract_strike(m)
+
+        records.append(
+            {
+                "market_id": m.get("id"),
+                "question": question,
+                "slug": m.get("slug"),
+                "end_date": end_dt,
+                "strike": strike,
+                "polymarket_yes": yes_prob,
+                "volume": volume,
+            }
+        )
+
+    df = pd.DataFrame(records)
+    if df.empty:
+        return df
+
+    # Keep rows even when strike is missing so scanner isn't empty.
+    df = df.dropna(subset=["polymarket_yes"])
+    return df.sort_values(["end_date", "volume"], ascending=[True, False])
+
+
+@st.cache_data(ttl=120, show_spinner=False)
+def fetch_deribit_book(currency: str):
+    params = {"currency": currency.upper(), "kind": "option"}
+    data = _http_get_json(f"{DERIBIT_API_BASE}/get_book_summary_by_currency", params=params)
+    result = data.get("result", [])
+    return pd.DataFrame(result)
+
+
+@st.cache_data(ttl=120, show_spinner=False)
+def fetch_deribit_index_price(currency: str):
+    idx_name = f"{currency.lower()}_usd"
+    data = _http_get_json(f"{DERIBIT_API_BASE}/get_index_price", params={"index_name": idx_name})
+    return float(data["result"]["index_price"])
+
+
+def _parse_expiry_from_instrument(instrument_name: str):
+    parts = instrument_name.split("-")
+    if len(parts) < 4:
+        return pd.NaT
+    try:
+        return pd.Timestamp(datetime.strptime(parts[1], "%d%b%y"), tz="UTC")
+    except ValueError:
+        return pd.NaT
+
+
+def derive_deribit_tail_probabilities(book_df: pd.DataFrame, spot_price: float, min_oi: float = 10.0):
+    if book_df.empty:
+        return pd.DataFrame()
+
+    df = book_df.copy()
+    df = df[df["instrument_name"].astype(str).str.endswith("-C")]
+    if df.empty:
+        return pd.DataFrame()
+
+    df["expiry"] = df["instrument_name"].astype(str).apply(_parse_expiry_from_instrument)
+    df["strike"] = pd.to_numeric(df.get("strike"), errors="coerce")
+    df["open_interest"] = pd.to_numeric(df.get("open_interest"), errors="coerce")
+    df["mark_price"] = pd.to_numeric(df.get("mark_price"), errors="coerce")
+    df["call_usd"] = df["mark_price"] * spot_price
+
+    df = df.dropna(subset=["expiry", "strike", "call_usd"])
+    df = df[df["open_interest"].fillna(0) >= min_oi]
+    if df.empty:
+        return pd.DataFrame()
+
+    rows = []
+    for expiry, group in df.groupby("expiry"):
+        g = group.sort_values("strike").copy()
+        if len(g) < 3:
+            continue
+
+        strikes = g["strike"].to_numpy()
+        calls = g["call_usd"].to_numpy()
+        k_mid = 0.5 * (strikes[:-1] + strikes[1:])
+        dk = strikes[1:] - strikes[:-1]
+        slope = (calls[:-1] - calls[1:]) / np.where(dk == 0, np.nan, dk)
+
+        for km, p in zip(k_mid, slope):
+            if np.isnan(p):
+                continue
+            rows.append(
+                {
+                    "expiry": expiry,
+                    "strike": float(km),
+                    "deribit_tail_prob": float(np.clip(p, 0.0, 1.0)),
+                }
+            )
+
+    out = pd.DataFrame(rows)
+    if out.empty:
+        return out
+    return out.sort_values(["expiry", "strike"])
+
+
+def nearest_deribit_prob(deribit_probs: pd.DataFrame, expiry: pd.Timestamp, strike: float):
+    if deribit_probs.empty or pd.isna(expiry) or pd.isna(strike):
+        return np.nan
+
+    expiry_diffs = (deribit_probs["expiry"] - expiry).abs()
+    nearest_expiry = deribit_probs.loc[expiry_diffs.idxmin(), "expiry"]
+    same_expiry = deribit_probs[deribit_probs["expiry"] == nearest_expiry]
+    if same_expiry.empty:
+        return np.nan
+
+    idx = (same_expiry["strike"] - strike).abs().idxmin()
+    return float(same_expiry.loc[idx, "deribit_tail_prob"])
+
+
+
+
+
+
+def get_local_diagnostics():
+    """Run lightweight self-checks so users without shell access can debug deploys."""
+    targets = [Path("app.py"), Path("dashboard.py")]
+    rows = []
+    has_critical_error = False
+
+    for target in targets:
+        info = {
+            "file": str(target),
+            "exists": target.exists(),
+            "diff_marker_detected": False,
+            "syntax_ok": False,
+            "message": "",
+        }
+
+        if not target.exists():
+            info["message"] = "File missing in runtime image."
+            has_critical_error = True
+            rows.append(info)
+            continue
+
+        content = target.read_text(encoding="utf-8", errors="replace")
+        head = "\n".join(content.splitlines()[:20])
+        if "diff --git" in head or "index " in head:
+            info["diff_marker_detected"] = True
+            info["message"] = "Git patch markers detected at top of file."
+            has_critical_error = True
+
+        try:
+            py_compile.compile(str(target), doraise=True)
+            info["syntax_ok"] = True
+            if not info["message"]:
+                info["message"] = "OK"
+        except py_compile.PyCompileError as e:
+            info["syntax_ok"] = False
+            info["message"] = f"Syntax check failed: {e.msg}"
+            has_critical_error = True
+
+        rows.append(info)
+
+    return pd.DataFrame(rows), has_critical_error
+
+def main():
+    st.title("📊 Polymarket vs Deribit Scanner Dashboard")
+    st.markdown(
+        """
+    Compare **Polymarket crypto price-target odds** with **Deribit risk-neutral tail probabilities**.
+
+    - Polymarket odds = current market-implied probabilities.
+    - Deribit probabilities = approximation from option call-spread slopes.
+    - Positive edge = Polymarket Yes > Deribit probability.
+    """
+    )
+
+    with st.sidebar:
+        st.header("Scanner Controls")
+        asset = st.selectbox("Asset", ["Bitcoin", "Ethereum", "Solana", "XRP"], index=0)
+        deribit_ccy = "BTC" if asset == "Bitcoin" else "ETH" if asset == "Ethereum" else None
+        min_volume = st.number_input("Min Polymarket volume", min_value=0.0, value=5000.0, step=1000.0)
+        min_oi = st.number_input("Min Deribit option OI", min_value=0.0, value=10.0, step=5.0)
+        max_days = st.slider("Max days to market expiry", min_value=1, max_value=365, value=180)
+        show_debug = st.checkbox("Show debug info", value=False)
+        refresh = st.button("🔄 Refresh")
+
+    if refresh:
+        fetch_polymarket_markets.clear()
+        fetch_deribit_book.clear()
+        fetch_deribit_index_price.clear()
+
+    diag_df, diag_has_error = get_local_diagnostics()
+    with st.sidebar.expander("🩺 Deployment Health Check", expanded=False):
+        st.caption("Runs local file + syntax checks (no shell required).")
+        st.dataframe(diag_df, use_container_width=True, hide_index=True)
+
+    if diag_has_error:
+        st.error(
+            "Deployment file integrity issue detected locally. "
+            "If you see 'diff --git' or 'index ... 100644', redeploy without build cache."
+        )
+
+    now = pd.Timestamp.now(tz="UTC")
+    error_msg = None
+
+    try:
+        poly_df = fetch_polymarket_markets(asset)
+        if not poly_df.empty:
+            poly_df = poly_df[poly_df["volume"].fillna(0) >= min_volume]
+            poly_df["days_to_expiry"] = (poly_df["end_date"] - now).dt.total_seconds() / 86400
+            poly_df = poly_df[(poly_df["days_to_expiry"] >= 0) & (poly_df["days_to_expiry"] <= max_days)]
+
+        if deribit_ccy is None:
+            deribit_df = pd.DataFrame()
+            spot = np.nan
+        else:
+            deribit_raw = fetch_deribit_book(deribit_ccy)
+            spot = fetch_deribit_index_price(deribit_ccy)
+            deribit_df = derive_deribit_tail_probabilities(deribit_raw, spot, min_oi=min_oi)
+
+    except (HTTPError, URLError, TimeoutError, KeyError, ValueError) as e:
+        error_msg = str(e)
+        poly_df = pd.DataFrame()
+        deribit_df = pd.DataFrame()
+        spot = np.nan
+
+    if error_msg:
+        st.warning(f"Live API fetch issue: {error_msg}")
+
+    if show_debug:
+        st.subheader("Debug")
+        st.write(
+            {
+                "asset": asset,
+                "polymarket_rows": 0 if poly_df.empty else int(len(poly_df)),
+                "deribit_rows": 0 if deribit_df.empty else int(len(deribit_df)),
+                "spot": None if pd.isna(spot) else float(spot),
+                "error": error_msg,
+            }
+        )
+        if not poly_df.empty:
+            st.dataframe(poly_df.head(20), use_container_width=True)
+
+    if poly_df.empty:
+        st.warning("No Polymarket markets matched the current filters.")
+    else:
+        scan = poly_df.copy()
+        scan["deribit_tail_prob"] = scan.apply(
+            lambda r: nearest_deribit_prob(deribit_df, r["end_date"], r["strike"]), axis=1
+        )
+        scan["edge"] = scan["polymarket_yes"] - scan["deribit_tail_prob"]
+        scan["edge_bps"] = scan["edge"] * 10000
+
+        cols = [
+            "question",
+            "strike",
+            "end_date",
+            "volume",
+            "polymarket_yes",
+            "deribit_tail_prob",
+            "edge",
+            "edge_bps",
+        ]
+        scan = scan[cols].sort_values("edge", ascending=False, na_position="last")
+
+        st.subheader("Scanner Table")
+        st.dataframe(
+            scan,
+            use_container_width=True,
+            hide_index=True,
+            column_config={
+                "strike": st.column_config.NumberColumn("Strike", format="$%.2f"),
+                "volume": st.column_config.NumberColumn("Volume", format="$%.0f"),
+                "polymarket_yes": st.column_config.NumberColumn("Polymarket Yes", format="%.3f"),
+                "deribit_tail_prob": st.column_config.NumberColumn("Deribit Tail Prob", format="%.3f"),
+                "edge": st.column_config.NumberColumn("Edge", format="%.3f"),
+                "edge_bps": st.column_config.NumberColumn("Edge (bps)", format="%.0f"),
+                "end_date": st.column_config.DatetimeColumn("Expiry (UTC)"),
+            },
+        )
+
+        st.subheader("Polymarket vs Deribit Probability")
+        chart_df = scan.dropna(subset=["deribit_tail_prob", "strike"]).copy()
+        if chart_df.empty:
+            st.info("No overlapping Deribit strike/expiry data to chart yet.")
+        else:
+            melted = chart_df.melt(
+                id_vars=["question", "strike", "end_date"],
+                value_vars=["polymarket_yes", "deribit_tail_prob"],
+                var_name="source",
+                value_name="probability",
+            )
+            chart = (
+                alt.Chart(melted)
+                .mark_circle(size=80)
+                .encode(
+                    x=alt.X("strike:Q", title="Strike"),
+                    y=alt.Y("probability:Q", scale=alt.Scale(domain=[0, 1]), title="Probability"),
+                    color=alt.Color("source:N", title="Source"),
+                    tooltip=[
+                        "question:N",
+                        alt.Tooltip("strike:Q", format=",.2f"),
+                        alt.Tooltip("end_date:T", title="Expiry"),
+                        alt.Tooltip("probability:Q", format=".3f"),
+                    ],
+                )
+                .properties(height=420)
+            )
+            st.altair_chart(chart, use_container_width=True)
+
+    st.caption(
+        f"Last updated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')} | "
+        "If rows are empty, lower filters and enable debug to inspect API payload handling."
+    )
+
+
+if __name__ == "__main__":
+    main()
 
EOF
)
