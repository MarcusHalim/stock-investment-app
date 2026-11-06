"""Vectorized Multi-Strategy Backtester, Bucket Attribution & Blending Engine.

Simulates historical performance of saved or custom ad-hoc strategy rules
against 1x SPY and 3x Leveraged SPXL (spliced actual + pre-2008 synthetic),
computes Underwater Drawdown & Ulcer Index metrics, decomposes return/risk
contribution by individual bucket, and models multi-strategy Efficient Frontiers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd


@dataclass
class CustomBucketSpec:
    """Specification for an interactive custom strategy bucket in Tab 2/3."""

    bucket_id: str
    bucket_name: str
    priority_rank: int
    bucket_target_share: float
    min_tenure_years: float
    min_sortino_entry: float
    min_sortino_exit: float
    max_down_capture_entry: float
    max_down_capture_exit: float
    min_up_capture_entry: float
    min_up_capture_exit: float
    min_info_ratio_entry: float
    max_drawdown_entry: float
    weighting_scheme: str
    route_to_spy_in_bear: bool


def compute_series_metrics(
    monthly_rets: pd.Series,
    spy_rets: pd.Series,
) -> dict[str, float]:
    """Calculate CAGR, Volatility, Sortino, Info Ratio, Max DD, and Ulcer Index."""
    clean = pd.DataFrame({"ret": monthly_rets, "spy": spy_rets}).dropna()
    if clean.empty:
        return {
            "cagr": 0.0,
            "ann_vol": 0.0,
            "sortino": 0.0,
            "info_ratio": 0.0,
            "max_dd": 0.0,
            "ulcer_index": 0.0,
            "total_return": 0.0,
        }

    n_months = len(clean)
    wealth = (1.0 + clean["ret"]).cumprod()
    total_ret = float(wealth.iloc[-1] - 1.0)
    cagr = float(wealth.iloc[-1] ** (12.0 / max(1, n_months)) - 1.0)
    ann_vol = float(clean["ret"].std(ddof=1) * np.sqrt(12.0)) if n_months > 1 else 0.0

    excess = clean["ret"] - clean["spy"]
    ann_excess = float(excess.mean() * 12.0)
    downside_sq = np.where(excess < 0.0, excess**2, 0.0)
    downside_semi_dev = float(np.sqrt(np.mean(downside_sq)) * np.sqrt(12.0))
    tracking_err = float(excess.std(ddof=1) * np.sqrt(12.0)) if n_months > 1 else 0.0

    sortino = ann_excess / max(0.0001, downside_semi_dev)
    info_ratio = ann_excess / max(0.0001, tracking_err)

    peaks = wealth.cummax()
    drawdowns = (wealth / peaks) - 1.0
    max_dd = float(drawdowns.min())
    ulcer_index = float(np.sqrt(np.mean(drawdowns**2)))

    return {
        "cagr": cagr,
        "ann_vol": ann_vol,
        "sortino": sortino,
        "info_ratio": info_ratio,
        "max_dd": max_dd,
        "ulcer_index": ulcer_index,
        "total_return": total_ret,
    }


def run_saved_strategy_backtest(
    allocations_df: pd.DataFrame,
    monthly_returns_df: pd.DataFrame,
    start_date: str | None = None,
    end_date: str | None = None,
    slippage_bps: float = 5.0,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Backtest a persisted Gold strategy using point-in-time target allocations.

    Applies lag-1 alignment: target weights determined at eval_date T earn the
    subsequent month's return at eval_date T+1 (zero lookahead bias).
    """
    if allocations_df.empty or monthly_returns_df.empty:
        return pd.DataFrame(), pd.DataFrame(), {}

    alloc = allocations_df.copy()
    rets = monthly_returns_df.copy()
    alloc["eval_date"] = pd.to_datetime(alloc["eval_date"])
    rets["eval_date"] = pd.to_datetime(rets["eval_date"])

    eval_dates = sorted(rets["eval_date"].unique())
    date_map = {eval_dates[i]: eval_dates[i + 1] for i in range(len(eval_dates) - 1)}
    alloc["return_date"] = alloc["eval_date"].map(date_map)
    alloc = alloc.dropna(subset=["return_date"])

    if start_date:
        alloc = alloc[alloc["return_date"] >= pd.to_datetime(start_date)]
    if end_date:
        alloc = alloc[alloc["return_date"] <= pd.to_datetime(end_date)]

    if alloc.empty:
        return pd.DataFrame(), pd.DataFrame(), {}

    bench_by_date = (
        rets.groupby("eval_date", as_index=False)[
            ["spy_return_monthly", "spxl_return_monthly"]
        ]
        .first()
        .rename(columns={"eval_date": "return_date"})
    )

    merged = alloc.merge(
        rets[
            [
                "eval_date",
                "ticker",
                "stock_return_monthly",
                "spy_return_monthly",
                "spxl_return_monthly",
            ]
        ],
        left_on=["return_date", "ticker"],
        right_on=["eval_date", "ticker"],
        how="left",
    )

    merged = merged.drop(columns=["spy_return_monthly", "spxl_return_monthly"]).merge(
        bench_by_date, on="return_date", how="left"
    )
    merged["asset_return"] = np.where(
        merged["ticker"] == "SPY",
        merged["spy_return_monthly"],
        merged["stock_return_monthly"].fillna(merged["spy_return_monthly"]),
    )
    merged["weighted_return"] = merged["strategy_target_weight"].astype(float) * merged[
        "asset_return"
    ].astype(float)

    slippage_rate = (slippage_bps / 10000.0) * 0.25

    monthly_port = (
        merged.groupby("return_date", as_index=False)
        .agg(
            strategy_return=("weighted_return", "sum"),
            spy_return=("spy_return_monthly", "first"),
            spxl_return=("spxl_return_monthly", "first"),
        )
        .sort_values("return_date")
    )
    monthly_port["strategy_return"] = monthly_port["strategy_return"] - slippage_rate

    for col, prefix in [
        ("strategy_return", "strat"),
        ("spy_return", "spy"),
        ("spxl_return", "spxl"),
    ]:
        wealth = (1.0 + monthly_port[col]).cumprod()
        peaks = wealth.cummax()
        monthly_port[f"{prefix}_wealth"] = wealth
        monthly_port[f"{prefix}_drawdown"] = (wealth / peaks) - 1.0

    # Bucket-level attribution
    bucket_monthly = (
        merged.groupby(["return_date", "assigned_bucket"], as_index=False)
        .agg(
            bucket_weight=("strategy_target_weight", "sum"),
            bucket_weighted_ret=("weighted_return", "sum"),
            spy_return=("spy_return_monthly", "first"),
        )
        .sort_values(["assigned_bucket", "return_date"])
    )
    bucket_monthly["standalone_bucket_ret"] = np.where(
        bucket_monthly["bucket_weight"] > 0.0001,
        bucket_monthly["bucket_weighted_ret"] / bucket_monthly["bucket_weight"],
        0.0,
    )

    total_weighted_gain = float(bucket_monthly["bucket_weighted_ret"].sum())
    attr_rows: list[dict[str, Any]] = []
    for b_id, grp in bucket_monthly.groupby("assigned_bucket"):
        b_metrics = compute_series_metrics(
            grp["standalone_bucket_ret"], grp["spy_return"]
        )
        contrib_sum = float(grp["bucket_weighted_ret"].sum())
        contrib_share = (
            contrib_sum / total_weighted_gain
            if abs(total_weighted_gain) > 0.0001
            else 0.0
        )
        attr_rows.append(
            {
                "bucket_id": str(b_id),
                "avg_allocation_weight": float(grp["bucket_weight"].mean()),
                "standalone_cagr": b_metrics["cagr"],
                "bucket_ann_vol": b_metrics["ann_vol"],
                "bucket_sortino": b_metrics["sortino"],
                "bucket_max_dd": b_metrics["max_dd"],
                "return_contribution_share": contrib_share,
            }
        )

    bucket_attr_df = pd.DataFrame(attr_rows).sort_values(
        by="avg_allocation_weight", ascending=False
    )

    s_metrics = compute_series_metrics(
        monthly_port["strategy_return"], monthly_port["spy_return"]
    )
    spy_metrics = compute_series_metrics(
        monthly_port["spy_return"], monthly_port["spy_return"]
    )
    spxl_metrics = compute_series_metrics(
        monthly_port["spxl_return"], monthly_port["spy_return"]
    )

    summary_stats = {
        "strat_cagr": s_metrics["cagr"],
        "strat_vol": s_metrics["ann_vol"],
        "strat_sortino": s_metrics["sortino"],
        "strat_ir": s_metrics["info_ratio"],
        "strat_max_dd": s_metrics["max_dd"],
        "strat_ulcer": s_metrics["ulcer_index"],
        "spy_cagr": spy_metrics["cagr"],
        "spy_vol": spy_metrics["ann_vol"],
        "spy_sortino": spy_metrics["sortino"],
        "spy_max_dd": spy_metrics["max_dd"],
        "spy_ulcer": spy_metrics["ulcer_index"],
        "spxl_cagr": spxl_metrics["cagr"],
        "spxl_vol": spxl_metrics["ann_vol"],
        "spxl_sortino": spxl_metrics["sortino"],
        "spxl_max_dd": spxl_metrics["max_dd"],
        "spxl_ulcer": spxl_metrics["ulcer_index"],
    }

    return monthly_port, bucket_attr_df, summary_stats


def simulate_adhoc_strategy(
    risk_metrics_df: pd.DataFrame,
    monthly_returns_df: pd.DataFrame,
    macro_regimes_df: pd.DataFrame,
    bucket_specs: list[CustomBucketSpec],
    start_date: str | None = None,
    end_date: str | None = None,
    slippage_bps: float = 5.0,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Simulate a custom ad-hoc strategy directly from Feature Store metrics."""
    if risk_metrics_df.empty or not bucket_specs:
        return pd.DataFrame(), pd.DataFrame(), {}

    df = risk_metrics_df.copy()
    df["eval_date"] = pd.to_datetime(df["eval_date"])
    df["assigned_bucket"] = "UNASSIGNED"
    df["bucket_share"] = 0.0
    df["weighting_scheme"] = "RISK_PARITY"
    df["route_to_spy_in_bear"] = False

    total_share = sum(max(0.01, b.bucket_target_share) for b in bucket_specs)

    for b in sorted(bucket_specs, key=lambda x: x.priority_rank):
        norm_share = max(0.01, b.bucket_target_share) / total_share
        mask = (
            (df["assigned_bucket"] == "UNASSIGNED")
            & (df["years_tenure"].astype(float) >= b.min_tenure_years)
            & (df["sortino_ratio"].astype(float) >= b.min_sortino_entry)
            & (df["down_market_capture"].astype(float) <= b.max_down_capture_entry)
            & (df["up_market_capture"].astype(float) >= b.min_up_capture_entry)
            & (df["information_ratio"].astype(float) >= b.min_info_ratio_entry)
            & (df["max_drawdown"].astype(float) >= b.max_drawdown_entry)
        )
        df.loc[mask, "assigned_bucket"] = b.bucket_id
        df.loc[mask, "bucket_share"] = norm_share
        df.loc[mask, "weighting_scheme"] = b.weighting_scheme
        df.loc[mask, "route_to_spy_in_bear"] = b.route_to_spy_in_bear

    qualified = df[df["assigned_bucket"] != "UNASSIGNED"].copy()
    if qualified.empty:
        return pd.DataFrame(), pd.DataFrame(), {}

    macro = macro_regimes_df.copy()
    macro["eval_date"] = pd.to_datetime(macro["trade_date"])
    qualified = qualified.merge(
        macro[["eval_date", "regime_status"]], on="eval_date", how="left"
    )

    qualified["effective_bucket_share"] = np.where(
        (qualified["regime_status"] == "BEAR_CONTRACTION")
        & (qualified["route_to_spy_in_bear"].astype(bool)),
        0.0,
        qualified["bucket_share"],
    )

    qualified["inv_vol_unit"] = np.where(
        qualified["weighting_scheme"] == "EQUAL_WEIGHT",
        1.0,
        1.0 / np.maximum(0.0001, qualified["downside_semi_deviation"].astype(float)),
    )

    bucket_sums = qualified.groupby(["eval_date", "assigned_bucket"])[
        "inv_vol_unit"
    ].transform("sum")
    qualified["strategy_target_weight"] = np.where(
        bucket_sums > 0.0,
        qualified["effective_bucket_share"] * (qualified["inv_vol_unit"] / bucket_sums),
        0.0,
    )

    all_dates = pd.DataFrame({"eval_date": sorted(df["eval_date"].unique())})
    date_sums = (
        qualified.groupby("eval_date", as_index=False)["strategy_target_weight"]
        .sum()
        .rename(columns={"strategy_target_weight": "eq_sum"})
    )
    spy_rows = all_dates.merge(date_sums, on="eval_date", how="left").fillna(
        {"eq_sum": 0.0}
    )
    spy_rows["strategy_target_weight"] = np.maximum(0.0, 1.0 - spy_rows["eq_sum"])
    spy_rows = spy_rows[spy_rows["strategy_target_weight"] > 0.0001].copy()
    spy_rows["ticker"] = "SPY"
    spy_rows["assigned_bucket"] = "SAFE_HARBOR_SPY"

    synthetic_alloc = pd.concat(
        [
            qualified[
                ["eval_date", "ticker", "assigned_bucket", "strategy_target_weight"]
            ],
            spy_rows[
                ["eval_date", "ticker", "assigned_bucket", "strategy_target_weight"]
            ],
        ],
        ignore_index=True,
    )

    return run_saved_strategy_backtest(
        allocations_df=synthetic_alloc,
        monthly_returns_df=monthly_returns_df,
        start_date=start_date,
        end_date=end_date,
        slippage_bps=slippage_bps,
    )


def compute_blended_portfolio_analytics(
    strategy_curves: dict[str, pd.DataFrame],
    strategy_weights: dict[str, float],
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, float]]:
    """Compute blended return curve, correlation matrix, and Efficient Frontier."""
    if not strategy_curves:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), {}

    merged_rets: pd.DataFrame | None = None
    for s_id, curve_df in strategy_curves.items():
        if curve_df.empty:
            continue
        sub = curve_df[
            ["return_date", "strategy_return", "spy_return", "spxl_return"]
        ].copy()
        sub = sub.rename(columns={"strategy_return": s_id})
        if merged_rets is None:
            merged_rets = sub
        else:
            merged_rets = merged_rets.merge(
                sub[["return_date", s_id]], on="return_date", how="inner"
            )

    if merged_rets is None or merged_rets.empty:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), {}

    strat_cols = [c for c in strategy_curves if c in merged_rets.columns]
    total_w = sum(max(0.0, strategy_weights.get(c, 0.0)) for c in strat_cols)
    norm_w = {
        c: (
            max(0.0, strategy_weights.get(c, 0.0)) / total_w
            if total_w > 0
            else 1.0 / len(strat_cols)
        )
        for c in strat_cols
    }

    merged_rets["blended_return"] = 0.0
    for c in strat_cols:
        merged_rets["blended_return"] += merged_rets[c] * norm_w[c]

    for col, prefix in [
        ("blended_return", "blend"),
        ("spy_return", "spy"),
        ("spxl_return", "spxl"),
    ]:
        w_idx = (1.0 + merged_rets[col]).cumprod()
        p_idx = w_idx.cummax()
        merged_rets[f"{prefix}_wealth"] = w_idx
        merged_rets[f"{prefix}_drawdown"] = (w_idx / p_idx) - 1.0

    corr_cols = [*strat_cols, "spy_return", "spxl_return"]
    corr_matrix = merged_rets[corr_cols].corr()

    # Simulate Efficient Frontier combinations
    frontier_rows: list[dict[str, float | str]] = []
    if len(strat_cols) >= 2:
        s1, s2 = strat_cols[0], strat_cols[1]
        for w1 in np.linspace(0.0, 1.0, 11):
            w2 = 1.0 - w1
            sim_ret = merged_rets[s1] * w1 + merged_rets[s2] * w2
            m = compute_series_metrics(sim_ret, merged_rets["spy_return"])
            frontier_rows.append(
                {
                    "mix_label": f"{int(w1 * 100)}% {s1} / {int(w2 * 100)}% {s2}",
                    "cagr_pct": m["cagr"] * 100.0,
                    "ann_vol_pct": m["ann_vol"] * 100.0,
                    "sortino": m["sortino"],
                    "max_dd_pct": m["max_dd"] * 100.0,
                }
            )
    else:
        s1 = strat_cols[0]
        m = compute_series_metrics(merged_rets[s1], merged_rets["spy_return"])
        frontier_rows.append(
            {
                "mix_label": f"100% {s1}",
                "cagr_pct": m["cagr"] * 100.0,
                "ann_vol_pct": m["ann_vol"] * 100.0,
                "sortino": m["sortino"],
                "max_dd_pct": m["max_dd"] * 100.0,
            }
        )

    frontier_df = pd.DataFrame(frontier_rows)
    b_metrics = compute_series_metrics(
        merged_rets["blended_return"], merged_rets["spy_return"]
    )

    blend_stats = {
        "blended_cagr": b_metrics["cagr"],
        "blended_vol": b_metrics["ann_vol"],
        "blended_sortino": b_metrics["sortino"],
        "blended_max_dd": b_metrics["max_dd"],
        "blended_ulcer_index": b_metrics["ulcer_index"],
    }

    return merged_rets, corr_matrix, frontier_df, blend_stats
