"""Deterministic mock data seeder for ephemeral CI/CD runners.

Populates DuckDB Bronze tables (including SPXL, ^IRX, strategy_id, and
multi-strategy metadata tables) without external network calls.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

DB_PATH = Path("data/stock_app_dev.duckdb")


def seed_ci_database() -> None:
    """Create and populate deterministic Bronze tables for CI testing."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(42)
    now_ts = datetime(2026, 1, 1, 12, 0, 0)

    dates = pd.bdate_range(start="2015-01-01", end="2025-12-31")
    n_days = len(dates)

    spy_rets = rng.normal(0.0004, 0.009, size=n_days)
    spy_close = 200.0 * np.cumprod(1.0 + spy_rets)
    rfr_annual = np.full(n_days, 0.03)
    spxl_rets = 3.0 * spy_rets - 2.0 * (rfr_annual / 252.0) - (0.0095 / 252.0)
    spxl_close = 50.0 * np.cumprod(1.0 + spxl_rets)
    vix_close = np.clip(rng.normal(18.0, 4.0, size=n_days), 10.0, 45.0)

    macro_df = pd.DataFrame(
        {
            "trade_date": dates.date,
            "vix_close": np.round(vix_close, 4),
            "spy_close": np.round(spy_close, 4),
            "spxl_close": np.round(spxl_close, 4),
            "risk_free_rate_annual": np.round(rfr_annual, 6),
            "_ingested_at": now_ts,
        }
    )

    tickers_meta = [
        ("AAPL", "Apple Inc.", "Information Technology", "Technology Hardware"),
        ("MSFT", "Microsoft Corp.", "Information Technology", "Systems Software"),
        ("NVDA", "NVIDIA Corp.", "Information Technology", "Semiconductors"),
        ("JNJ", "Johnson & Johnson", "Health Care", "Pharmaceuticals"),
        ("PG", "Procter & Gamble", "Consumer Staples", "Household Products"),
        ("JPM", "JPMorgan Chase", "Financials", "Diversified Banks"),
    ]

    constituents_df = pd.DataFrame(
        [
            {
                "ticker": t,
                "company_name": name,
                "sector": sec,
                "sub_industry": sub,
                "date_added": date(2000, 1, 1),
                "date_removed": None,
                "_ingested_at": now_ts,
            }
            for t, name, sec, sub in tickers_meta
        ]
    )

    price_frames: list[pd.DataFrame] = []
    for idx, (ticker, _, _, _) in enumerate(tickers_meta):
        beta = 1.35 if idx < 3 else 0.65
        alpha = 0.0003 if idx < 3 else 0.0002
        idio_noise = rng.normal(0.0, 0.006 if idx < 3 else 0.004, size=n_days)
        stock_rets = alpha + beta * spy_rets + idio_noise
        closes = 100.0 * np.cumprod(1.0 + stock_rets)

        price_frames.append(
            pd.DataFrame(
                {
                    "ticker": ticker,
                    "trade_date": dates.date,
                    "open_price": np.round(closes * 0.998, 4),
                    "high_price": np.round(closes * 1.005, 4),
                    "low_price": np.round(closes * 0.995, 4),
                    "close_price": np.round(closes, 4),
                    "volume": 1_000_000,
                    "split_factor": 1.0,
                    "dividend_amount": 0.0,
                    "_ingested_at": now_ts,
                }
            )
        )

    prices_df = pd.concat(price_frames, ignore_index=True)

    executions_df = pd.DataFrame(
        [
            {
                "execution_id": "CI_EXEC_001",
                "strategy_id": "STRAT_DEFAULT_BARBELL",
                "trade_date": date(2025, 1, 15),
                "ticker": "MSFT",
                "action": "BUY",
                "shares_executed": 10.0,
                "fill_price": 400.0,
                "notes": "Initial CI seed trade",
                "_created_at": now_ts,
            }
        ]
    )

    strategies_df = pd.DataFrame(
        [
            {
                "strategy_id": "STRAT_DEFAULT_BARBELL",
                "strategy_name": "Core Multi-Factor Barbell",
                "strategy_description": "Default two-bucket barbell strategy.",
                "is_active": True,
                "portfolio_weight": 0.70,
                "lookback_window_years": 5,
                "rebalance_band_pct": 0.20,
                "sector_cap_excess_pct": 0.05,
                "corr_cluster_threshold": 0.75,
                "halt_on_high_vol": True,
                "_updated_at": now_ts,
            },
            {
                "strategy_id": "STRAT_DEFENSIVE_QUALITY",
                "strategy_name": "Defensive Capital Compounder",
                "strategy_description": "Low-volatility capital preservation strategy.",
                "is_active": True,
                "portfolio_weight": 0.30,
                "lookback_window_years": 5,
                "rebalance_band_pct": 0.20,
                "sector_cap_excess_pct": 0.05,
                "corr_cluster_threshold": 0.75,
                "halt_on_high_vol": True,
                "_updated_at": now_ts,
            },
        ]
    )

    buckets_df = pd.DataFrame(
        [
            {
                "strategy_id": "STRAT_DEFAULT_BARBELL",
                "bucket_id": "BUCKET_B_STEADY",
                "bucket_name": "Steady Low-Vol Compounders",
                "priority_rank": 1,
                "bucket_target_share": 0.60,
                "min_tenure_years": 10.0,
                "min_sortino_entry": 0.35,
                "min_sortino_exit": 0.15,
                "max_down_capture_entry": 0.90,
                "max_down_capture_exit": 1.00,
                "min_up_capture_entry": 0.00,
                "min_up_capture_exit": 0.00,
                "min_info_ratio_entry": -9.99,
                "min_info_ratio_exit": -9.99,
                "max_drawdown_entry": -0.50,
                "max_drawdown_exit": -0.55,
                "weighting_scheme": "RISK_PARITY",
                "route_to_spy_in_bear": False,
                "_updated_at": now_ts,
            },
            {
                "strategy_id": "STRAT_DEFAULT_BARBELL",
                "bucket_id": "BUCKET_A_AGGRESSIVE",
                "bucket_name": "Aggressive Upside Growth",
                "priority_rank": 2,
                "bucket_target_share": 0.40,
                "min_tenure_years": 5.0,
                "min_sortino_entry": 0.00,
                "min_sortino_exit": -0.50,
                "max_down_capture_entry": 9.99,
                "max_down_capture_exit": 9.99,
                "min_up_capture_entry": 1.15,
                "min_up_capture_exit": 1.05,
                "min_info_ratio_entry": 0.25,
                "min_info_ratio_exit": 0.05,
                "max_drawdown_entry": -0.99,
                "max_drawdown_exit": -0.99,
                "weighting_scheme": "RISK_PARITY",
                "route_to_spy_in_bear": True,
                "_updated_at": now_ts,
            },
            {
                "strategy_id": "STRAT_DEFENSIVE_QUALITY",
                "bucket_id": "BUCKET_DEF_CORE",
                "bucket_name": "Fortress Low-Drawdown Core",
                "priority_rank": 1,
                "bucket_target_share": 1.00,
                "min_tenure_years": 10.0,
                "min_sortino_entry": 0.50,
                "min_sortino_exit": 0.25,
                "max_down_capture_entry": 0.80,
                "max_down_capture_exit": 0.90,
                "min_up_capture_entry": 0.70,
                "min_up_capture_exit": 0.60,
                "min_info_ratio_entry": 0.10,
                "min_info_ratio_exit": -0.10,
                "max_drawdown_entry": -0.35,
                "max_drawdown_exit": -0.45,
                "weighting_scheme": "RISK_PARITY",
                "route_to_spy_in_bear": False,
                "_updated_at": now_ts,
            },
        ]
    )

    with duckdb.connect(str(DB_PATH)) as con:
        con.execute("CREATE SCHEMA IF NOT EXISTS bronze;")
        con.register("df_macro", macro_df)
        con.register("df_const", constituents_df)
        con.register("df_prices", prices_df)
        con.register("df_exec", executions_df)
        con.register("df_strat", strategies_df)
        con.register("df_buckets", buckets_df)

        con.execute(
            "CREATE OR REPLACE TABLE bronze.bronze_macro_indicators AS SELECT * FROM df_macro;"
        )
        con.execute(
            "CREATE OR REPLACE TABLE bronze.bronze_sp500_constituents AS SELECT * FROM df_const;"
        )
        con.execute(
            "CREATE OR REPLACE TABLE bronze.bronze_stock_prices_daily AS SELECT * FROM df_prices;"
        )
        con.execute(
            "CREATE OR REPLACE TABLE bronze.bronze_manual_executions AS SELECT * FROM df_exec;"
        )
        con.execute(
            "CREATE OR REPLACE TABLE bronze.bronze_strategies AS SELECT * FROM df_strat;"
        )
        con.execute(
            "CREATE OR REPLACE TABLE bronze.bronze_strategy_buckets AS SELECT * FROM df_buckets;"
        )


if __name__ == "__main__":
    seed_ci_database()
