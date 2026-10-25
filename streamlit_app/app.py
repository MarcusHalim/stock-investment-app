"""
Quantitative Barbell Advisory Engine - Streamlit Application.
Supports both Local DuckDB (Dev) and Streamlit in Snowflake (Prod).
"""

import subprocess
import uuid
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb
import pandas as pd
import plotly.express as px
import streamlit as st

# ==========================================
# PAGE CONFIGURATION
# ==========================================
st.set_page_config(
    page_title="Quantitative Barbell Advisory Engine",
    page_icon="📈",
    layout="wide",
)

DB_PATH = Path("data/stock_app_dev.duckdb")


# ==========================================
# DUAL-ENGINE DATABASE ADAPTER (DuckDB / SiS)
# ==========================================
def get_snowflake_session():
    """Returns active Snowpark session if running inside Streamlit in Snowflake."""
    try:
        from snowflake.snowpark.context import get_active_session

        return get_active_session()
    except (ImportError, RuntimeError):
        return None


@st.cache_data(ttl=60)
def run_query(sql: str) -> pd.DataFrame:
    """Executes a read-only SQL query against Snowflake (Prod) or DuckDB (Local Dev)."""
    sf_session = get_snowflake_session()
    if sf_session is not None:
        return sf_session.sql(sql).to_pandas()

    with duckdb.connect(str(DB_PATH), read_only=True) as con:
        return con.execute(sql).df()


def insert_manual_trade(
    trade_date: date,
    ticker: str,
    action: str,
    shares_executed: float,
    fill_price: float,
    notes: str,
) -> None:
    """Appends a new trade execution row into bronze.bronze_manual_executions."""
    exec_id = str(uuid.uuid4())
    created_at = datetime.now(UTC).replace(tzinfo=None)
    clean_ticker = ticker.strip().upper()
    clean_notes = notes.strip()

    sf_session = get_snowflake_session()
    if sf_session is not None:
        sf_session.sql(
            """
            INSERT INTO bronze.bronze_manual_executions
            (execution_id, trade_date, ticker, action, shares_executed, fill_price, notes, _created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            params=[
                exec_id,
                str(trade_date),
                clean_ticker,
                action,
                shares_executed,
                fill_price,
                clean_notes,
                str(created_at),
            ],
        ).collect()
    else:
        with duckdb.connect(str(DB_PATH), read_only=False) as con:
            con.execute(
                """
                INSERT INTO bronze.bronze_manual_executions VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    exec_id,
                    trade_date,
                    clean_ticker,
                    action,
                    shares_executed,
                    fill_price,
                    clean_notes,
                    created_at,
                ],
            )
    st.cache_data.clear()


def trigger_local_dbt_reconciliation() -> tuple[bool, str]:
    """Runs dbt on portfolio trade log and actual_vs_target models to refresh holdings."""
    cmd = [
        "dbt",
        "run",
        "--select",
        "stg_portfolio_trade_log",
        "fct_portfolio_actual_vs_target",
        "--project-dir",
        "dbt_stock_project",
        "--profiles-dir",
        "dbt_stock_project",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    st.cache_data.clear()
    return (result.returncode == 0), (result.stdout + "\n" + result.stderr)


# ==========================================
# SIDEBAR: AIR-GAPPED CAPITAL & CONTROLS
# ==========================================
with st.sidebar:
    st.header("🔒 Air-Gapped Settings")
    st.caption(
        "Your portfolio capital is stored strictly in browser memory (session_state) "
        "and is never saved to DuckDB or Snowflake."
    )
    total_capital = st.number_input(
        "Hypothetical / Total Portfolio Capital ($)",
        min_value=1000.0,
        value=100000.0,
        step=5000.0,
        format="%.2f",
    )

    st.divider()
    st.subheader("⚙️ Pipeline Reconciliation")
    if st.button("🔄 Reconcile Portfolio (Run dbt)", use_container_width=True):
        with st.spinner("Running dbt portfolio reconciliation..."):
            ok, logs = trigger_local_dbt_reconciliation()
            if ok:
                st.success("Portfolio weights reconciled!")
            else:
                st.error("dbt run encountered an error.")
                st.code(logs)


# ==========================================
# TOP BANNER: MACRO REGIME CIRCUIT BREAKER
# ==========================================
st.title("📈 Quantitative Barbell Advisory Engine")

macro_df = run_query("""
    SELECT
        trade_date,
        spy_close,
        spy_daily_return,
        spy_sma_200d,
        vix_close,
        market_breadth_pct,
        regime_status
    FROM silver.stg_macro_regimes
    ORDER BY trade_date DESC
    LIMIT 1
""")

if not macro_df.empty:
    latest_macro = macro_df.iloc[0]
    regime = latest_macro["regime_status"]
    breadth_val = (
        f"{latest_macro['market_breadth_pct'] * 100:.1f}%"
        if pd.notna(latest_macro["market_breadth_pct"])
        else "N/A"
    )

    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Latest Market Date", str(latest_macro["trade_date"])[:10])
    col2.metric(
        "SPY Close",
        f"${latest_macro['spy_close']:.2f}",
        f"{latest_macro['spy_daily_return'] * 100:+.2f}%"
        if pd.notna(latest_macro["spy_daily_return"])
        else None,
    )
    col3.metric(
        "SPY 200d SMA",
        f"${latest_macro['spy_sma_200d']:.2f}"
        if pd.notna(latest_macro["spy_sma_200d"])
        else "N/A",
    )
    col4.metric("CBOE VIX Level", f"{latest_macro['vix_close']:.2f}")
    col5.metric("S&P 500 Breadth (>200d SMA)", breadth_val)

    if regime == "BULL_STABLE":
        st.success(
            "🟢 **ACTIVE MACRO REGIME: BULL_STABLE** — Normal factor operations enabled. "
            "Barbell allocations active across Aggressive (Bucket A) and Steady (Bucket B)."
        )
    elif regime == "HIGH_VOLATILITY":
        st.error(
            "🔴 **ACTIVE MACRO REGIME: HIGH_VOLATILITY (VIX > 30)** — Volatility circuit breaker "
            "triggered. All algorithmic rebalancing is PAUSED to avoid panic whipsaws."
        )
    else:
        st.warning(
            "🟠 **ACTIVE MACRO REGIME: BEAR_CONTRACTION (Breadth < 30%)** — Market breadth circuit "
            "breaker triggered. New Aggressive Bucket buying is halted and routed to SPY Safe Harbor."
        )

st.divider()

# ==========================================
# APPLICATION TABS
# ==========================================
tab_orders, tab_buckets, tab_alerts, tab_logger = st.tabs(
    [
        "🎯 Action Center & Order Sheet",
        "⚖️ Barbell Bucket Explorer",
        "🚨 Idiosyncratic Risk & Drift Alerts",
        "✍️ Log Manual Trade Execution",
    ]
)

# ------------------------------------------
# TAB 1: ACTION CENTER & ORDER SHEET
# ------------------------------------------
with tab_orders:
    st.subheader("Portfolio Drift & Advisory Order Sheet")
    st.caption(
        "Rebalances are only triggered when a position breaches its ±20% relative tolerance band."
    )

    orders_df = run_query("""
        SELECT
            as_of_date,
            ticker,
            current_shares_held,
            latest_close_price,
            position_market_value,
            actual_portfolio_weight,
            target_portfolio_weight,
            relative_weight_drift,
            is_tolerance_breached,
            recommended_action,
            suggested_trade_weight_diff
        FROM gold.fct_portfolio_actual_vs_target
        ORDER BY abs(suggested_trade_weight_diff) DESC, ticker
    """)

    if not orders_df.empty:
        # Compute air-gapped dollar and share recommendations using session total_capital
        orders_df["target_dollar_value"] = (
            orders_df["target_portfolio_weight"].astype(float) * total_capital
        ).round(2)
        orders_df["suggested_dollar_trade"] = (
            orders_df["suggested_trade_weight_diff"].astype(float) * total_capital
        ).round(2)
        orders_df["est_shares_to_trade"] = orders_df.apply(
            lambda r: (
                round(r["suggested_dollar_trade"] / float(r["latest_close_price"]), 2)
                if float(r["latest_close_price"]) > 0
                else 0.0
            ),
            axis=1,
        )

        st.dataframe(
            orders_df,
            use_container_width=True,
            hide_index=True,
        )

        csv_bytes = orders_df.to_csv(index=False).encode("utf-8")
        st.download_button(
            label="📥 Export Action Order Sheet to CSV",
            data=csv_bytes,
            file_name=f"advisory_order_sheet_{date.today().isoformat()}.csv",
            mime="text/csv",
        )
    else:
        st.info("No target allocations found in gold.fct_portfolio_actual_vs_target.")

# ------------------------------------------
# TAB 2: BARBELL BUCKET EXPLORER
# ------------------------------------------
with tab_buckets:
    st.subheader("Multi-Factor Barbell Classification & 5-Year Risk Metrics")

    buckets_df = run_query("""
        SELECT
            b.eval_date,
            b.ticker,
            b.sector,
            b.ipo_tenure_tier,
            b.prior_bucket,
            b.current_bucket,
            b.is_structural_drift_flag,
            r.sortino_ratio,
            r.information_ratio,
            r.up_market_capture,
            r.down_market_capture,
            r.max_drawdown,
            r.downside_semi_deviation,
            t.final_target_weight,
            t.correlation_cluster_id
        FROM gold.dim_stock_bucket_classification b
        INNER JOIN gold.fct_stock_risk_metrics r
            ON b.eval_date = r.eval_date
           AND b.ticker = r.ticker
           AND r.lookback_window_years = 5
        LEFT JOIN gold.fct_portfolio_target_allocations t
            ON b.eval_date = t.eval_date
           AND b.ticker = t.ticker
        WHERE b.eval_date = (SELECT MAX(eval_date) FROM gold.dim_stock_bucket_classification)
        ORDER BY b.current_bucket, r.sortino_ratio DESC
    """)

    if not buckets_df.empty:
        # Ensure numeric types and create a strictly positive marker_size column for Plotly
        buckets_df["sortino_ratio"] = buckets_df["sortino_ratio"].astype(float)
        buckets_df["marker_size"] = buckets_df["sortino_ratio"].clip(lower=0.0) + 0.1

        c1, c2 = st.columns([2, 1])
        with c1:
            fig_scatter = px.scatter(
                buckets_df,
                x="down_market_capture",
                y="up_market_capture",
                color="current_bucket",
                size="marker_size",
                hover_name="ticker",
                hover_data={
                    "marker_size": False,
                    "sector": True,
                    "sortino_ratio": ":.4f",
                    "information_ratio": ":.4f",
                    "max_drawdown": ":.2%",
                    "final_target_weight": ":.2%",
                },
                title="Up-Market vs. Down-Market Capture Ratio (5-Year Rolling vs. SPY)",
            )
            fig_scatter.add_hline(y=1.0, line_dash="dash", line_color="gray")
            fig_scatter.add_vline(x=1.0, line_dash="dash", line_color="gray")
            st.plotly_chart(fig_scatter, use_container_width=True)

        with c2:
            alloc_summary = (
                buckets_df.dropna(subset=["final_target_weight"])
                .groupby("current_bucket", as_index=False)["final_target_weight"]
                .sum()
            )
            if not alloc_summary.empty:
                fig_pie = px.pie(
                    alloc_summary,
                    names="current_bucket",
                    values="final_target_weight",
                    title="Active Equity Bucket Breakdown",
                )
                st.plotly_chart(fig_pie, use_container_width=True)

        st.dataframe(
            buckets_df.drop(columns=["marker_size"]),
            use_container_width=True,
            hide_index=True,
        )

# ------------------------------------------
# TAB 3: IDIOSYNCRATIC RISK & DRIFT ALERTS
# ------------------------------------------
with tab_alerts:
    col_a, col_b = st.columns(2)

    with col_a:
        st.subheader("🚨 Statistical Irregularity (Residual Z-Score < -3.0)")
        st.caption(
            "Isolates company-specific structural drops disconnected from SPY's movement "
            "(Last 60 Trading Days)."
        )
        anomalies_df = run_query("""
            SELECT
                trade_date,
                ticker,
                rolling_90d_beta,
                expected_daily_return,
                daily_residual,
                residual_z_score
            FROM gold.fct_stock_drift_and_residuals
            WHERE is_anomaly_alert = TRUE
              AND trade_date >= (
                  SELECT MAX(trade_date) - INTERVAL '60 days'
                  FROM gold.fct_stock_drift_and_residuals
              )
            ORDER BY trade_date DESC, residual_z_score ASC
        """)
        if not anomalies_df.empty:
            st.dataframe(anomalies_df, use_container_width=True, hide_index=True)
        else:
            st.success("No 3-sigma negative residual anomalies in the last 60 days.")

    with col_b:
        st.subheader("⚠️ Long-Term Bucket Drift Warnings")
        st.caption(
            "Equities where 50-week Down-Market Capture has degraded >10% above its 200-week baseline."
        )
        drift_df = run_query("""
            SELECT
                eval_date,
                ticker,
                sector,
                prior_bucket,
                current_bucket,
                is_structural_drift_flag
            FROM gold.dim_stock_bucket_classification
            WHERE eval_date = (SELECT MAX(eval_date) FROM gold.dim_stock_bucket_classification)
              AND (is_structural_drift_flag = TRUE OR prior_bucket != current_bucket)
            ORDER BY ticker
        """)
        if not drift_df.empty:
            st.dataframe(drift_df, use_container_width=True, hide_index=True)
        else:
            st.success("No bucket transitions or structural drift flags this month.")

# ------------------------------------------
# TAB 4: LOG MANUAL TRADE EXECUTION
# ------------------------------------------
with tab_logger:
    st.subheader("✍️ Log Filled Brokerage Execution (Append-Only Write-Back)")
    st.caption(
        "Appends filled trade records strictly to bronze.bronze_manual_executions. "
        "Never mutates Gold tables directly."
    )

    col_form, col_hist = st.columns([1, 2])

    with col_form, st.form("manual_trade_form", clear_on_submit=True):
        f_date = st.date_input("Execution Date", value=date.today())
        f_ticker = st.text_input("Ticker Symbol (e.g., AAPL, MSFT, SPY)", value="SPY")
        f_action = st.selectbox("Action", options=["BUY", "SELL"])
        f_shares = st.number_input(
            "Shares Executed", min_value=0.0001, value=10.0, step=1.0, format="%.4f"
        )
        f_price = st.number_input(
            "Fill Price per Share ($)",
            min_value=0.01,
            value=585.0,
            step=1.0,
            format="%.4f",
        )
        f_notes = st.text_input(
            "Execution Notes / Reasoning", value="Monthly tolerance-band rebalance"
        )

        submitted = st.form_submit_button("💾 Append Trade to Bronze Log")
        if submitted:
            if not f_ticker.strip():
                st.error("Please provide a valid ticker symbol.")
            else:
                insert_manual_trade(
                    trade_date=f_date,
                    ticker=f_ticker,
                    action=f_action,
                    shares_executed=float(f_shares),
                    fill_price=float(f_price),
                    notes=f_notes,
                )
                st.success(
                    f"Logged {f_action} {f_shares} shares of {f_ticker.upper()} @ ${f_price:.2f}! "
                    "Click 'Reconcile Portfolio (Run dbt)' in the sidebar to update weights."
                )

    with col_hist:
        st.markdown("**Recent Logged Executions (`bronze.bronze_manual_executions`)**")
        exec_history_df = run_query("""
            SELECT
                execution_id,
                trade_date,
                ticker,
                action,
                shares_executed,
                fill_price,
                notes,
                _created_at
            FROM bronze.bronze_manual_executions
            ORDER BY _created_at DESC
        """)
        st.dataframe(exec_history_df, use_container_width=True, hide_index=True)
