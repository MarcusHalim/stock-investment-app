"""Deterministic Plain-Language Narrative & Diagnostic Engine.

Translates quantitative stock metrics, custom bucket configurations,
historical backtest results, bucket attribution breakdowns, multi-strategy
blends, and 3-sigma residual anomalies into clear plain-English guidance
at $0 compute cost.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

METRIC_DEFINITIONS: dict[str, dict[str, str]] = {
    "sortino_ratio": {
        "title": "Sortino Ratio (vs. SPY)",
        "definition": (
            "Annualized active excess return over SPY divided strictly by "
            "annualized downside semi-deviation (harmful negative volatility)."
        ),
        "question": (
            "How much excess return am I earning for every unit of harmful "
            "downside crash risk I take on?"
        ),
    },
    "downside_semi_deviation": {
        "title": "Downside Semi-Deviation",
        "definition": (
            "Annualized standard deviation of strictly negative monthly active "
            "returns relative to SPY (ignoring upside volatility spikes)."
        ),
        "question": (
            "When this stock underperforms the S&P 500, how severe and "
            "volatile are those underperformance drops?"
        ),
    },
    "up_market_capture": {
        "title": "Up-Market Capture Ratio",
        "definition": (
            "Average monthly return of the stock during months when SPY is "
            "positive, divided by SPY's average return in those same bull months."
        ),
        "question": (
            "When the broader market rallies, does this stock amplify the "
            "upside (>100%) or lag behind (<100%)?"
        ),
    },
    "down_market_capture": {
        "title": "Down-Market Capture Ratio",
        "definition": (
            "Average monthly return of the stock during months when SPY is "
            "negative, divided by SPY's average loss in those same bear months."
        ),
        "question": (
            "When the market sells off, does this stock protect capital (<100% "
            "of SPY's loss) or fall harder than the index (>100%)?"
        ),
    },
    "information_ratio": {
        "title": "Information Ratio",
        "definition": (
            "Annualized active excess return over SPY divided by total "
            "tracking error (standard deviation of active return differences)."
        ),
        "question": (
            "How consistently does this equity beat the S&P 500 month after "
            "month without erratic swings?"
        ),
    },
    "max_drawdown": {
        "title": "Maximum Drawdown",
        "definition": (
            "The worst peak-to-trough percentage decline experienced by the "
            "equity over the selected rolling lookback horizon."
        ),
        "question": (
            "What was the worst historical pain a holder had to endure before "
            "the stock found a bottom?"
        ),
    },
    "spxl_up_capture_ratio": {
        "title": "3x SPXL Upside Capture Ratio",
        "definition": (
            "Percentage of a 3x daily-leveraged S&P 500 ETF's (SPXL) bull-month "
            "gains captured by this unleveraged single equity."
        ),
        "question": (
            "How close does this stock get to 3x leveraged index upside "
            "without paying leveraged ETF borrowing costs or daily reset decay?"
        ),
    },
}


def explain_stock_metric(metric_key: str, value: float) -> dict[str, str]:
    """Return Definition, Core Question, and Plain-Language Verdict for a metric."""
    meta = METRIC_DEFINITIONS.get(
        metric_key,
        {
            "title": metric_key,
            "definition": "Quantitative performance and risk indicator.",
            "question": "How does this metric shape risk/return behavior?",
        },
    )

    verdict = "Within normal market baseline parameters."
    if metric_key == "sortino_ratio":
        if value >= 0.75:
            verdict = (
                f"Exceptional ({value:.2f}): Generates elite active return per "
                "unit of downside risk—prime candidate for core or aggressive buckets."
            )
        elif value >= 0.35:
            verdict = (
                f"Solid ({value:.2f}): Outperforms SPY on a downside-adjusted "
                "basis, rewarding holders for the risk taken."
            )
        elif value >= 0.0:
            verdict = (
                f"Modest ({value:.2f}): Slightly positive excess return over SPY, "
                "but upside barely compensates for downside pullbacks."
            )
        else:
            verdict = (
                f"Underperforming ({value:.2f}): Failed to keep pace with SPY "
                "over this window; holding SPY directly was more efficient."
            )
    elif metric_key == "downside_semi_deviation":
        pct = value * 100.0
        if value <= 0.10:
            verdict = (
                f"Low Downside Volatility ({pct:.1f}%): Very stable relative to "
                "SPY; receives a larger Risk-Parity position weight."
            )
        elif value <= 0.18:
            verdict = (
                f"Moderate Downside Volatility ({pct:.1f}%): Typical single-stock "
                "downside fluctuation relative to the index."
            )
        else:
            verdict = (
                f"High Downside Volatility ({pct:.1f}%): Prone to sharp active "
                "drawdowns; Risk-Parity sizing will automatically shrink its weight."
            )
    elif metric_key == "up_market_capture":
        pct = value * 100.0
        if value >= 1.20:
            verdict = (
                f"High-Octane Upside Amplifier ({pct:.0f}%): Captures {pct:.0f}% "
                "of SPY's gains during bull months, acting as an offensive growth engine."
            )
        elif value >= 0.90:
            verdict = (
                f"Market-Pacing Upside ({pct:.0f}%): Participates in roughly "
                "the same upside as the S&P 500 during rallies."
            )
        else:
            verdict = (
                f"Defensive / Lagging Upside ({pct:.0f}%): Trails SPY during bull "
                "rallies; must provide strong downside protection to justify holding."
            )
    elif metric_key == "down_market_capture":
        pct = value * 100.0
        if value <= 0.85:
            verdict = (
                f"Strong Capital Shield ({pct:.0f}%): Suffers only {pct:.0f}% of "
                "SPY's losses during market selloffs—ideal defensive compounder."
            )
        elif value <= 1.05:
            verdict = (
                f"Market-Inline Downside ({pct:.0f}%): Drops roughly in line with "
                "the S&P 500 during market corrections."
            )
        else:
            verdict = (
                f"Elevated Crash Sensitivity ({pct:.0f}%): Falls harder than SPY "
                "during selloffs; requires high upside capture to compensate."
            )
    elif metric_key == "information_ratio":
        if value >= 0.35:
            verdict = (
                f"Highly Consistent Outperformer ({value:.2f}): Beats SPY steadily "
                "with disciplined tracking error."
            )
        elif value >= 0.0:
            verdict = (
                f"Mild Active Edge ({value:.2f}): Positive active return, though "
                "month-to-month outperformance can be lumpy."
            )
        else:
            verdict = (
                f"Negative Active Edge ({value:.2f}): Underperformed the passive "
                "SPY benchmark over this lookback horizon."
            )
    elif metric_key == "max_drawdown":
        pct = value * 100.0
        if value >= -0.22:
            verdict = (
                f"Resilient Drawdown Floor ({pct:.1f}%): Contained historical "
                "peak-to-trough losses remarkably well."
            )
        elif value >= -0.38:
            verdict = (
                f"Standard Equity Drawdown ({pct:.1f}%): Experienced normal bear "
                "market pullbacks comparable to the broader index."
            )
        else:
            verdict = (
                f"Deep Historical Drawdown ({pct:.1f}%): Suffered severe peak-to-trough "
                "compression; requires macro circuit-breaker protection."
            )
    elif metric_key == "spxl_up_capture_ratio":
        pct = value * 100.0
        verdict = (
            f"Captures {pct:.0f}% of 3x Leveraged SPXL bull-month gains without "
            "incurring margin borrowing costs or daily leveraged volatility decay."
        )

    return {
        "title": meta["title"],
        "definition": meta["definition"],
        "question": meta["question"],
        "verdict": verdict,
    }


def synthesize_stock_profile(row: pd.Series, window_years: int) -> str:
    """Generate an executive plain-language summary of a single equity's profile."""
    ticker = str(row.get("ticker", "Equity"))
    sector = str(row.get("sector", "S&P 500"))
    sortino = float(row.get("sortino_ratio", 0.0))
    up_cap = float(row.get("up_market_capture", 1.0)) * 100.0
    down_cap = float(row.get("down_market_capture", 1.0)) * 100.0
    max_dd = float(row.get("max_drawdown", 0.0)) * 100.0
    cagr = float(row.get("annualized_cagr", 0.0)) * 100.0
    spy_cagr = float(row.get("spy_annualized_cagr", 0.0)) * 100.0
    spxl_cagr = float(row.get("spxl_annualized_cagr", 0.0)) * 100.0

    if up_cap >= 115.0 and down_cap > 95.0:
        archetype = "Aggressive Upside Growth Equity"
    elif down_cap <= 90.0 and sortino >= 0.30:
        archetype = "Defensive Low-Volatility Compounder"
    elif up_cap >= 110.0 and down_cap <= 90.0:
        archetype = "Asymmetric All-Weather Outperformer"
    elif sortino < 0.0:
        archetype = "Benchmark Laggard / Mean-Reversion Candidate"
    else:
        archetype = "Core Market-Correlated Constituent"

    spread_desc = (
        "positive asymmetric capture spread"
        if up_cap > down_cap
        else "negative capture spread"
    )

    return (
        f"**{ticker} ({sector})** profiles over the **{window_years}-Year** rolling window as an "
        f"**{archetype}**. It compounded at **{cagr:.1f}% CAGR** (vs. **{spy_cagr:.1f}%** for 1x SPY "
        f"and **{spxl_cagr:.1f}%** for 3x Leveraged SPXL). During market rallies, {ticker} captured "
        f"**{up_cap:.0f}%** of SPY's gains while absorbing **{down_cap:.0f}%** of SPY's losses during "
        f"pullbacks—resulting in a **{spread_desc} ({up_cap - down_cap:+.0f}% net spread)**, a "
        f"Sortino Ratio of **{sortino:.2f}**, and a worst peak-to-trough drawdown of **{max_dd:.1f}%**."
    )


def translate_bucket_hypothesis(
    bucket_name: str,
    bucket_share: float,
    min_tenure: float,
    min_sortino_entry: float,
    min_sortino_exit: float,
    max_down_entry: float,
    min_up_entry: float,
    min_ir_entry: float,
    max_dd_entry: float,
    weighting_scheme: str,
    route_to_spy_in_bear: bool,
    qualifying_count: int,
) -> str:
    """Translate custom bucket slider thresholds into a plain-language hypothesis."""
    sizing_text = (
        "Inverse Downside Volatility (Risk Parity), giving larger weights to steadier stocks"
        if weighting_scheme == "RISK_PARITY"
        else "Equal Weighting (1/N across correlation-adjusted clusters)"
    )
    bear_text = (
        "automatically rotates 100% into SAFE_HARBOR_SPY cash proxy when S&P 500 Breadth drops below 30%"
        if route_to_spy_in_bear
        else "remains invested in its defensive equities even during BEAR_CONTRACTION regimes"
    )
    down_text = (
        f"loses no more than {max_down_entry * 100:.0f}% of SPY's drop during selloffs"
        if max_down_entry < 2.0
        else "accepts unconstrained downside capture in exchange for maximum offensive upside"
    )

    return (
        f"📌 **{bucket_name} ({bucket_share * 100:.0f}% Target Sleeve Share) Hypothesis:** "
        f"This protocol filters for S&P 500 companies with at least **{min_tenure:.0f} years** of "
        f"public trading history that achieve a Sortino Ratio $\\ge$ **{min_sortino_entry:.2f}** "
        f"(holding until Sortino drops below **{min_sortino_exit:.2f}** via hysteresis buffering), "
        f"capture at least **{min_up_entry * 100:.0f}%** of bull-market upside, and ensure the stock "
        f"{down_text} (with historical Max Drawdown no worse than **{max_dd_entry * 100:.0f}%** and "
        f"Information Ratio $\\ge$ **{min_ir_entry:.2f}**). Qualifying positions are sized via "
        f"**{sizing_text}**, and this sleeve **{bear_text}**. "
        f"**Current Live Qualification:** **{qualifying_count}** S&P 500 equities currently pass these rules."
    )


def narrate_backtest_attribution(
    summary_stats: dict[str, Any],
    bucket_attr_df: pd.DataFrame,
) -> str:
    """Generate a statistical verdict comparing strategy & bucket performance vs SPY & 3x SPXL."""
    strat_cagr = float(summary_stats.get("strat_cagr", 0.0)) * 100.0
    spy_cagr = float(summary_stats.get("spy_cagr", 0.0)) * 100.0
    spxl_cagr = float(summary_stats.get("spxl_cagr", 0.0)) * 100.0
    strat_dd = float(summary_stats.get("strat_max_dd", 0.0)) * 100.0
    spy_dd = float(summary_stats.get("spy_max_dd", 0.0)) * 100.0
    spxl_dd = float(summary_stats.get("spxl_max_dd", 0.0)) * 100.0
    strat_sortino = float(summary_stats.get("strat_sortino", 0.0))
    spy_sortino = float(summary_stats.get("spy_sortino", 0.0))
    spxl_sortino = float(summary_stats.get("spxl_sortino", 0.0))

    cagr_ratio_spxl = (strat_cagr / spxl_cagr * 100.0) if spxl_cagr > 0 else 100.0
    dd_reduction_spxl = abs(spxl_dd) - abs(strat_dd)

    verdict_lines = [
        "### 🔍 Executive Backtest & Risk Verdict",
        (
            f"- **Benchmark Comparison (1x SPY vs. 3x Leveraged SPXL):** Your strategy compounded at "
            f"**{strat_cagr:.2f}% CAGR** (vs. **{spy_cagr:.2f}%** for SPY and **{spxl_cagr:.2f}%** for 3x SPXL), "
            f"achieving a Sortino Ratio of **{strat_sortino:.2f}** (vs. **{spy_sortino:.2f}** SPY / "
            f"**{spxl_sortino:.2f}** SPXL)."
        ),
        (
            f"- **Leveraged Risk Yardstick:** Relative to 3x Leveraged SPXL, your strategy captured "
            f"**{cagr_ratio_spxl:.1f}%** of the annualized return while avoiding **{dd_reduction_spxl:.1f} "
            f"percentage points** of catastrophic drawdown pain (**{strat_dd:.1f}%** strategy max drawdown vs. "
            f"**{spxl_dd:.1f}%** for SPXL and **{spy_dd:.1f}%** for SPY)."
        ),
    ]

    if not bucket_attr_df.empty and len(bucket_attr_df) >= 1:
        best_sortino_row = bucket_attr_df.sort_values(
            by="bucket_sortino", ascending=False
        ).iloc[0]
        best_cagr_row = bucket_attr_df.sort_values(
            by="standalone_cagr", ascending=False
        ).iloc[0]

        b_sort_name = str(best_sortino_row["bucket_id"])
        b_cagr_name = str(best_cagr_row["bucket_id"])

        if b_sort_name == b_cagr_name:
            verdict_lines.append(
                f"- **Bucket Attribution Winner:** **{b_sort_name}** dominated both raw compounding "
                f"(**{float(best_cagr_row['standalone_cagr']) * 100:.2f}% CAGR**) and risk-adjusted "
                f"efficiency (**Sortino {float(best_sortino_row['bucket_sortino']):.2f}**), contributing "
                f"**{float(best_sortino_row['return_contribution_share']) * 100:.1f}%** of total portfolio gains."
            )
        else:
            verdict_lines.append(
                f"- **Crucial Bucket Attribution Insight (Hypothesis Check):** While **{b_cagr_name}** "
                f"generated the highest raw return (**{float(best_cagr_row['standalone_cagr']) * 100:.2f}% CAGR**, "
                f"Max DD **{float(best_cagr_row['bucket_max_dd']) * 100:.1f}%**), **{b_sort_name}** "
                f"statistically outperformed on a risk-adjusted basis (**Sortino "
                f"{float(best_sortino_row['bucket_sortino']):.2f}** vs. "
                f"**{float(best_cagr_row['bucket_sortino']):.2f}**), proving that lower downside volatility "
                f"delivered superior compounding efficiency per unit of risk."
            )

    return "\n".join(verdict_lines)


def narrate_portfolio_blend(
    blend_stats: dict[str, float],
    overlap_tickers: list[str],
    top_sector: str,
    top_sector_weight: float,
) -> str:
    """Explain the risk/return profile and overlap of a blended multi-strategy portfolio."""
    cagr = blend_stats.get("blended_cagr", 0.0) * 100.0
    sortino = blend_stats.get("blended_sortino", 0.0)
    max_dd = blend_stats.get("blended_max_dd", 0.0) * 100.0
    ulcer = blend_stats.get("blended_ulcer_index", 0.0) * 100.0

    overlap_msg = (
        f"Cross-strategy overlap detected in **{len(overlap_tickers)} tickers** "
        f"({', '.join(overlap_tickers[:6])}{'...' if len(overlap_tickers) > 6 else ''}); "
        "Tab 5 automatically nets these positions so you never trade against yourself."
        if overlap_tickers
        else "Zero ticker overlap across your active strategies—100% orthogonal holdings."
    )

    return (
        f"⚖️ **Blended Master Portfolio Verdict:** Combining your active strategies at the selected "
        f"capital weights yields a historical **{cagr:.2f}% CAGR**, a blended Sortino Ratio of "
        f"**{sortino:.2f}**, a Maximum Drawdown of **{max_dd:.1f}%**, and an Ulcer Drawdown Pain Index "
        f"of **{ulcer:.2f}%**. Your largest aggregate sector exposure is **{top_sector}** at "
        f"**{top_sector_weight * 100:.1f}%** of total capital. {overlap_msg}"
    )


def narrate_trade_rationale(row: pd.Series) -> str:
    """Provide a plain-language explanation for a single Order Sheet row."""
    action = str(row.get("recommended_action", "HOLD"))
    drift = float(row.get("relative_weight_drift", 0.0)) * 100.0
    actual_w = float(row.get("actual_portfolio_weight", 0.0)) * 100.0
    target_w = float(row.get("target_portfolio_weight", 0.0)) * 100.0
    regime = str(row.get("active_macro_regime", "BULL_STABLE"))

    if action == "PAUSED_BY_MACRO_FILTER":
        return (
            f"Macro Circuit Breaker ({regime}) is active; rebalancing orders are paused "
            "to prevent trading into high volatility or falling market breadth."
        )
    if action == "BUY":
        if actual_w == 0.0:
            return (
                f"New position entry required to reach {target_w:.2f}% netted target weight "
                "across active strategies."
            )
        return (
            f"Underweight by {drift:.1f}% relative to target ({actual_w:.2f}% actual vs. "
            f"{target_w:.2f}% target), breaching lower tolerance band."
        )
    if action == "SELL":
        if target_w == 0.0:
            return "Full exit recommended: equity no longer qualifies for any active strategy bucket."
        return (
            f"Overweight by +{drift:.1f}% relative to target ({actual_w:.2f}% actual vs. "
            f"{target_w:.2f}% target), breaching upper tolerance band."
        )
    return (
        f"Within ±20% noise-filtering tolerance band ({drift:+.1f}% relative drift); "
        "hold position to avoid unnecessary turnover and tax drag."
    )


def narrate_anomaly_alert(row: pd.Series) -> str:
    """Translate a -3 sigma residual Z-score shock into plain-English incident guidance."""
    ticker = str(row.get("ticker", "Equity"))
    t_date = str(row.get("trade_date", ""))
    beta = float(row.get("rolling_90d_beta", 1.0))
    residual = float(row.get("daily_residual", 0.0)) * 100.0
    z_score = float(row.get("residual_z_score", 0.0))

    return (
        f"🚨 **Idiosyncratic Breakdown Alert — {ticker} ({t_date} | Z-Score: {z_score:.2f}σ):** "
        f"After stripping out broad S&P 500 market movement using {ticker}'s 90-day Beta of "
        f"**{beta:.2f}**, the stock suffered an unexplained company-specific drop of "
        f"**{residual:.2f}%** in a single session. Because this exceeds 3 standard deviations of "
        f"historical residual noise, it signals a company-specific event (earnings miss, guidance cut, "
        f"or regulatory shock) rather than a market dip. **Protocol:** Quarantine from automated "
        f"BUY top-ups until fundamental catalyst is reviewed."
    )
