"""Idempotent Bronze Layer ingestion script for local DuckDB development.

Pulls raw unadjusted OHLCV + splits/dividends from yfinance, scrapes S&P 500
historical constituent changes from Wikipedia, ingests SPY, VIX, SPXL, and ^IRX
macro indicators, and initializes governed multi-strategy metadata tables.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from urllib.request import Request, urlopen

import duckdb
import pandas as pd
import yfinance as yf

DB_PATH = Path("data/stock_app_dev.duckdb")
WIKI_SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
START_DATE = "2000-01-01"


def init_bronze_schema(con: duckdb.DuckDBPyConnection) -> None:
    """Initialize Bronze schema and tables with non-destructive migration."""
    con.execute("CREATE SCHEMA IF NOT EXISTS bronze;")

    con.execute(
        """
        CREATE TABLE IF NOT EXISTS bronze.bronze_stock_prices_daily (
            ticker VARCHAR NOT NULL,
            trade_date DATE NOT NULL,
            open_price DECIMAL(12, 4),
            high_price DECIMAL(12, 4),
            low_price DECIMAL(12, 4),
            close_price DECIMAL(12, 4) NOT NULL,
            volume BIGINT,
            split_factor DECIMAL(10, 6),
            dividend_amount DECIMAL(10, 4),
            _ingested_at TIMESTAMP NOT NULL,
            PRIMARY KEY (ticker, trade_date)
        );
        """
    )

    con.execute(
        """
        CREATE TABLE IF NOT EXISTS bronze.bronze_sp500_constituents (
            ticker VARCHAR NOT NULL,
            company_name VARCHAR,
            sector VARCHAR,
            sub_industry VARCHAR,
            date_added DATE NOT NULL,
            date_removed DATE,
            _ingested_at TIMESTAMP NOT NULL,
            PRIMARY KEY (ticker, date_added)
        );
        """
    )

    con.execute(
        """
        CREATE TABLE IF NOT EXISTS bronze.bronze_macro_indicators (
            trade_date DATE PRIMARY KEY,
            vix_close DECIMAL(8, 4),
            spy_close DECIMAL(12, 4) NOT NULL,
            spxl_close DECIMAL(12, 4),
            risk_free_rate_annual DECIMAL(8, 6),
            _ingested_at TIMESTAMP NOT NULL
        );
        """
    )
    con.execute(
        """
        ALTER TABLE bronze.bronze_macro_indicators
        ADD COLUMN IF NOT EXISTS spxl_close DECIMAL(12, 4);
        """
    )
    con.execute(
        """
        ALTER TABLE bronze.bronze_macro_indicators
        ADD COLUMN IF NOT EXISTS risk_free_rate_annual DECIMAL(8, 6);
        """
    )

    con.execute(
        """
        CREATE TABLE IF NOT EXISTS bronze.bronze_manual_executions (
            execution_id VARCHAR PRIMARY KEY,
            strategy_id VARCHAR DEFAULT 'STRAT_DEFAULT_BARBELL',
            trade_date DATE NOT NULL,
            ticker VARCHAR NOT NULL,
            action VARCHAR NOT NULL,
            shares_executed DECIMAL(12, 4) NOT NULL,
            fill_price DECIMAL(12, 4) NOT NULL,
            notes VARCHAR,
            _created_at TIMESTAMP NOT NULL
        );
        """
    )
    con.execute(
        """
        ALTER TABLE bronze.bronze_manual_executions
        ADD COLUMN IF NOT EXISTS strategy_id VARCHAR DEFAULT 'STRAT_DEFAULT_BARBELL';
        """
    )
    con.execute(
        """
        UPDATE bronze.bronze_manual_executions
        SET strategy_id = 'STRAT_DEFAULT_BARBELL'
        WHERE strategy_id IS NULL;
        """
    )

    con.execute(
        """
        CREATE TABLE IF NOT EXISTS bronze.bronze_strategies (
            strategy_id VARCHAR PRIMARY KEY,
            strategy_name VARCHAR NOT NULL,
            strategy_description VARCHAR NOT NULL,
            is_active BOOLEAN NOT NULL,
            portfolio_weight DECIMAL(8, 6) NOT NULL,
            lookback_window_years INTEGER NOT NULL,
            rebalance_band_pct DECIMAL(8, 6) NOT NULL,
            sector_cap_excess_pct DECIMAL(8, 6) NOT NULL,
            corr_cluster_threshold DECIMAL(8, 6) NOT NULL,
            halt_on_high_vol BOOLEAN NOT NULL,
            _updated_at TIMESTAMP NOT NULL
        );
        """
    )

    con.execute(
        """
        CREATE TABLE IF NOT EXISTS bronze.bronze_strategy_buckets (
            strategy_id VARCHAR NOT NULL,
            bucket_id VARCHAR NOT NULL,
            bucket_name VARCHAR NOT NULL,
            priority_rank INTEGER NOT NULL,
            bucket_target_share DECIMAL(8, 6) NOT NULL,
            min_tenure_years DECIMAL(6, 2) NOT NULL,
            min_sortino_entry DECIMAL(10, 4) NOT NULL,
            min_sortino_exit DECIMAL(10, 4) NOT NULL,
            max_down_capture_entry DECIMAL(10, 4) NOT NULL,
            max_down_capture_exit DECIMAL(10, 4) NOT NULL,
            min_up_capture_entry DECIMAL(10, 4) NOT NULL,
            min_up_capture_exit DECIMAL(10, 4) NOT NULL,
            min_info_ratio_entry DECIMAL(10, 4) NOT NULL,
            min_info_ratio_exit DECIMAL(10, 4) NOT NULL,
            max_drawdown_entry DECIMAL(10, 6) NOT NULL,
            max_drawdown_exit DECIMAL(10, 6) NOT NULL,
            weighting_scheme VARCHAR NOT NULL,
            route_to_spy_in_bear BOOLEAN NOT NULL,
            _updated_at TIMESTAMP NOT NULL,
            PRIMARY KEY (strategy_id, bucket_id)
        );
        """
    )


def seed_default_strategies(con: duckdb.DuckDBPyConnection) -> None:
    """Seed out-of-the-box baseline strategies if bronze_strategies is empty."""
    rows = con.execute("SELECT COUNT(*) FROM bronze.bronze_strategies;").fetchall()
    existing_count = int(rows[0][0]) if rows else 0
    if existing_count > 0:
        return

    now_ts = datetime.now(UTC).replace(tzinfo=None)

    strategies_df = pd.DataFrame(
        [
            {
                "strategy_id": "STRAT_DEFAULT_BARBELL",
                "strategy_name": "Core Multi-Factor Barbell",
                "strategy_description": (
                    "Balances high up-capture momentum breakouts (Bucket A) with "
                    "mature low-downside-capture quality compounders (Bucket B)."
                ),
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
                "strategy_description": (
                    "Low-volatility capital preservation strategy targeting mature "
                    "equities with strict downside protection and high Sortino efficiency."
                ),
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

    con.register("df_strategies_seed", strategies_df)
    con.execute(
        """
        INSERT INTO bronze.bronze_strategies
        SELECT * FROM df_strategies_seed;
        """
    )
    con.unregister("df_strategies_seed")

    con.register("df_buckets_seed", buckets_df)
    con.execute(
        """
        INSERT INTO bronze.bronze_strategy_buckets
        SELECT * FROM df_buckets_seed;
        """
    )
    con.unregister("df_buckets_seed")


def fetch_sp500_constituents() -> pd.DataFrame:
    """Scrape current S&P 500 constituents from Wikipedia."""
    req = Request(
        WIKI_SP500_URL,
        headers={"User-Agent": "Mozilla/5.0 (compatible; StockAppIngest/2.0)"},
    )
    with urlopen(req, timeout=30) as resp:
        html_text = resp.read().decode("utf-8")

    tables = pd.read_html(StringIO(html_text))
    current_df = tables[0].copy()

    current_df["ticker"] = (
        current_df["Symbol"].astype(str).str.strip().str.replace(".", "-", regex=False)
    )
    current_df["company_name"] = current_df["Security"].astype(str).str.strip()
    current_df["sector"] = current_df["GICS Sector"].astype(str).str.strip()
    current_df["sub_industry"] = current_df["GICS Sub-Industry"].astype(str).str.strip()
    current_df["date_added"] = pd.to_datetime(
        current_df["Date added"], errors="coerce"
    ).dt.date
    current_df["date_added"] = current_df["date_added"].fillna(
        datetime(2000, 1, 1).date()
    )
    current_df["date_removed"] = None
    current_df["_ingested_at"] = datetime.now(UTC).replace(tzinfo=None)

    cols = [
        "ticker",
        "company_name",
        "sector",
        "sub_industry",
        "date_added",
        "date_removed",
        "_ingested_at",
    ]
    return current_df[cols].drop_duplicates(subset=["ticker", "date_added"])


def ingest_macro_indicators(con: duckdb.DuckDBPyConnection) -> int:
    """Ingest SPY, VIX, SPXL (total return), and ^IRX (13w T-Bill yield) into Bronze."""
    spy_df = yf.Ticker("SPY").history(start=START_DATE, auto_adjust=True)
    vix_df = yf.Ticker("^VIX").history(start=START_DATE, auto_adjust=False)
    spxl_df = yf.Ticker("SPXL").history(start=START_DATE, auto_adjust=True)
    irx_df = yf.Ticker("^IRX").history(start=START_DATE, auto_adjust=False)

    spy_series = spy_df[["Close"]].rename(columns={"Close": "spy_close"})
    spy_series.index = pd.to_datetime(spy_series.index).tz_localize(None).date

    vix_series = vix_df[["Close"]].rename(columns={"Close": "vix_close"})
    vix_series.index = pd.to_datetime(vix_series.index).tz_localize(None).date

    spxl_series = spxl_df[["Close"]].rename(columns={"Close": "spxl_close"})
    spxl_series.index = pd.to_datetime(spxl_series.index).tz_localize(None).date

    irx_series = irx_df[["Close"]].rename(columns={"Close": "irx_close"})
    irx_series.index = pd.to_datetime(irx_series.index).tz_localize(None).date

    merged = (
        spy_series.join(vix_series, how="left")
        .join(spxl_series, how="left")
        .join(irx_series, how="left")
    )
    merged.index.name = "trade_date"
    merged = merged.reset_index()

    merged["vix_close"] = merged["vix_close"].ffill().fillna(18.0)
    merged["risk_free_rate_annual"] = (
        merged["irx_close"].ffill().fillna(2.5) / 100.0
    ).clip(lower=0.0, upper=0.25)
    merged["_ingested_at"] = datetime.now(UTC).replace(tzinfo=None)

    out_df = merged[
        [
            "trade_date",
            "vix_close",
            "spy_close",
            "spxl_close",
            "risk_free_rate_annual",
            "_ingested_at",
        ]
    ].dropna(subset=["spy_close"])

    con.register("df_macro_ingest", out_df)
    con.execute("DELETE FROM bronze.bronze_macro_indicators;")
    con.execute(
        """
        INSERT INTO bronze.bronze_macro_indicators (
            trade_date,
            vix_close,
            spy_close,
            spxl_close,
            risk_free_rate_annual,
            _ingested_at
        )
        SELECT
            trade_date,
            vix_close,
            spy_close,
            spxl_close,
            risk_free_rate_annual,
            _ingested_at
        FROM df_macro_ingest;
        """
    )
    con.unregister("df_macro_ingest")
    return len(out_df)


def ingest_stock_prices(con: duckdb.DuckDBPyConnection, tickers: list[str]) -> int:
    """Ingest raw unadjusted OHLCV, splits, and dividends for target tickers."""
    records: list[pd.DataFrame] = []
    now_ts = datetime.now(UTC).replace(tzinfo=None)

    for ticker in tickers:
        try:
            hist = yf.Ticker(ticker).history(start=START_DATE, auto_adjust=False)
            if hist.empty:
                continue
            df = hist.reset_index()
            date_col = "Date" if "Date" in df.columns else df.columns[0]
            df["trade_date"] = pd.to_datetime(df[date_col]).dt.tz_localize(None).dt.date
            df["ticker"] = ticker
            df["open_price"] = df["Open"]
            df["high_price"] = df["High"]
            df["low_price"] = df["Low"]
            df["close_price"] = df["Close"]
            df["volume"] = df["Volume"].fillna(0).astype("int64")
            df["split_factor"] = (
                df.get("Stock Splits", pd.Series(0.0, index=df.index))
                .replace(0.0, 1.0)
                .fillna(1.0)
            )
            df["dividend_amount"] = df.get(
                "Dividends", pd.Series(0.0, index=df.index)
            ).fillna(0.0)
            df["_ingested_at"] = now_ts

            cols = [
                "ticker",
                "trade_date",
                "open_price",
                "high_price",
                "low_price",
                "close_price",
                "volume",
                "split_factor",
                "dividend_amount",
                "_ingested_at",
            ]
            records.append(df[cols].dropna(subset=["close_price"]))
        except (KeyError, ValueError, TypeError, RuntimeError, OSError) as exc:
            print(f"[WARN] Skipping {ticker} due to error: {exc}")

    if not records:
        return 0

    combined_df = pd.concat(records, ignore_index=True).drop_duplicates(
        subset=["ticker", "trade_date"]
    )
    con.register("df_prices_ingest", combined_df)
    con.execute("DELETE FROM bronze.bronze_stock_prices_daily;")
    con.execute(
        """
        INSERT INTO bronze.bronze_stock_prices_daily
        SELECT * FROM df_prices_ingest;
        """
    )
    con.unregister("df_prices_ingest")
    return len(combined_df)


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments for sample vs. full universe ingestion."""
    parser = argparse.ArgumentParser(
        description="Ingest Bronze Layer tables into local DuckDB."
    )
    parser.add_argument(
        "--sample",
        type=int,
        default=30,
        help="Number of S&P 500 tickers to ingest for fast dev quickstart.",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Ingest all S&P 500 constituents.",
    )
    return parser.parse_args()


def main() -> None:
    """Execute idempotent Bronze ingestion."""
    args = parse_args()
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    with duckdb.connect(str(DB_PATH)) as con:
        init_bronze_schema(con)
        seed_default_strategies(con)

        constituents_df = fetch_sp500_constituents()
        con.register("df_constituents_ingest", constituents_df)
        con.execute("DELETE FROM bronze.bronze_sp500_constituents;")
        con.execute(
            """
            INSERT INTO bronze.bronze_sp500_constituents
            SELECT * FROM df_constituents_ingest;
            """
        )
        con.unregister("df_constituents_ingest")

        macro_count = ingest_macro_indicators(con)
        all_tickers = sorted(constituents_df["ticker"].unique().tolist())
        target_tickers = all_tickers if args.full else all_tickers[: args.sample]
        price_count = ingest_stock_prices(con, target_tickers)

        print(
            f"[OK] Bronze Ingest Complete | Constituents: {len(constituents_df)} | "
            f"Macro Rows: {macro_count} | Price Rows ({len(target_tickers)} tickers): {price_count}"
        )


if __name__ == "__main__":
    main()
