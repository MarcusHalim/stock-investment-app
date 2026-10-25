"""
Phase 1 Local Bronze Ingestion Script (DuckDB + yfinance + Wikipedia).
Idempotently creates the 4 Bronze tables and loads initial development data.
"""

import uuid
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path

import duckdb
import pandas as pd
import requests
import yfinance as yf

# ==========================================
# CONFIGURATION
# ==========================================
DB_PATH = Path("data/stock_app_dev.duckdb")
START_DATE = "2000-01-01"

# Set SAMPLE_LIMIT = 30 for a fast ~20-second initial test run.
# Set SAMPLE_LIMIT = None when you are ready to pull all 500+ S&P 500 tickers (~5-8 mins).
SAMPLE_LIMIT = None


def init_bronze_schema(con: duckdb.DuckDBPyConnection) -> None:
    """Creates the bronze schema and the 4 raw staging tables if they do not exist."""
    con.execute("CREATE SCHEMA IF NOT EXISTS bronze;")

    con.execute("""
        CREATE TABLE IF NOT EXISTS bronze.bronze_stock_prices_daily (
            ticker VARCHAR,
            trade_date DATE,
            open_price DECIMAL(12, 4),
            high_price DECIMAL(12, 4),
            low_price DECIMAL(12, 4),
            close_price DECIMAL(12, 4),
            volume BIGINT,
            split_factor DECIMAL(10, 6),
            dividend_amount DECIMAL(10, 4),
            _ingested_at TIMESTAMP,
            PRIMARY KEY (ticker, trade_date)
        );
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS bronze.bronze_sp500_constituents (
            ticker VARCHAR,
            company_name VARCHAR,
            sector VARCHAR,
            sub_industry VARCHAR,
            date_added DATE,
            date_removed DATE,
            _ingested_at TIMESTAMP,
            PRIMARY KEY (ticker, date_added)
        );
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS bronze.bronze_macro_indicators (
            trade_date DATE PRIMARY KEY,
            vix_close DECIMAL(8, 4),
            spy_close DECIMAL(12, 4),
            _ingested_at TIMESTAMP
        );
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS bronze.bronze_manual_executions (
            execution_id VARCHAR PRIMARY KEY,
            trade_date DATE,
            ticker VARCHAR,
            action VARCHAR,
            shares_executed DECIMAL(12, 4),
            fill_price DECIMAL(12, 4),
            notes VARCHAR,
            _created_at TIMESTAMP
        );
    """)
    print("[OK] Bronze schema and 4 tables verified.")


def ingest_sp500_constituents(con: duckdb.DuckDBPyConnection) -> list[str]:
    """Scrapes Wikipedia for S&P 500 constituents and loads bronze_sp500_constituents."""
    print("Fetching S&P 500 constituents from Wikipedia...")
    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()

    tables = pd.read_html(StringIO(response.text))
    current_df = tables[0]

    df = pd.DataFrame()
    # Normalize Yahoo Finance ticker format (e.g., BRK.B -> BRK-B)
    df["ticker"] = (
        current_df["Symbol"].astype(str).str.replace(".", "-", regex=False).str.strip()
    )
    df["company_name"] = current_df["Security"].astype(str).str.strip()
    df["sector"] = current_df["GICS Sector"].astype(str).str.strip()
    df["sub_industry"] = current_df["GICS Sub-Industry"].astype(str).str.strip()

    # Default missing/unparseable date_added to 1990-01-01 so PK is never NULL
    df["date_added"] = pd.to_datetime(current_df["Date added"], errors="coerce").dt.date
    df["date_added"] = df["date_added"].fillna(pd.to_datetime("1990-01-01").date())
    df["date_removed"] = None
    df["_ingested_at"] = datetime.now(UTC).replace(tzinfo=None)

    df = df.drop_duplicates(subset=["ticker", "date_added"])

    con.register("df_constituents", df)
    con.execute("""
        INSERT OR REPLACE INTO bronze.bronze_sp500_constituents
        SELECT
            ticker,
            company_name,
            sector,
            sub_industry,
            date_added,
            CAST(date_removed AS DATE) AS date_removed,
            _ingested_at
        FROM df_constituents;
    """)
    con.unregister("df_constituents")

    tickers = df["ticker"].unique().tolist()
    print(f"[OK] Loaded {len(df)} S&P 500 constituent records.")
    return tickers


def ingest_macro_indicators(con: duckdb.DuckDBPyConnection) -> None:
    """Downloads SPY and ^VIX daily closes into bronze_macro_indicators."""
    print(f"Downloading SPY and ^VIX macro indicators from {START_DATE}...")
    raw = yf.download(
        ["SPY", "^VIX"], start=START_DATE, auto_adjust=False, progress=False
    )

    close_df = raw["Close"].reset_index()
    close_df = close_df.rename(
        columns={"Date": "trade_date", "SPY": "spy_close", "^VIX": "vix_close"}
    )
    close_df["trade_date"] = pd.to_datetime(close_df["trade_date"]).dt.date
    close_df = close_df.dropna(subset=["spy_close"])
    close_df["_ingested_at"] = datetime.now(UTC).replace(tzinfo=None)

    con.register("df_macro", close_df)
    con.execute("""
        INSERT OR REPLACE INTO bronze.bronze_macro_indicators
        SELECT
            trade_date,
            ROUND(CAST(vix_close AS DECIMAL(8, 4)), 4),
            ROUND(CAST(spy_close AS DECIMAL(12, 4)), 4),
            _ingested_at
        FROM df_macro;
    """)
    con.unregister("df_macro")
    print(f"[OK] Loaded {len(close_df)} daily rows into bronze_macro_indicators.")


def ingest_stock_prices(con: duckdb.DuckDBPyConnection, tickers: list[str]) -> None:
    """Downloads unadjusted daily OHLCV, splits, and dividends for constituent tickers."""
    target_tickers = tickers[:SAMPLE_LIMIT] if SAMPLE_LIMIT else tickers
    print(
        f"Downloading daily prices, splits, and dividends for {len(target_tickers)} tickers "
        f"(SAMPLE_LIMIT={SAMPLE_LIMIT})..."
    )

    raw = yf.download(
        target_tickers,
        start=START_DATE,
        auto_adjust=False,
        actions=True,
        group_by="ticker",
        threads=True,
        progress=True,
    )

    frames = []
    ingested_at = datetime.now(UTC).replace(tzinfo=None)

    for ticker in target_tickers:
        try:
            t_df = raw[ticker].copy() if len(target_tickers) > 1 else raw.copy()
            t_df = t_df.dropna(subset=["Close"]).reset_index()
            if t_df.empty:
                continue

            t_df["ticker"] = ticker
            t_df["trade_date"] = pd.to_datetime(t_df["Date"]).dt.date
            t_df["open_price"] = t_df["Open"]
            t_df["high_price"] = t_df["High"]
            t_df["low_price"] = t_df["Low"]
            t_df["close_price"] = t_df["Close"]
            t_df["volume"] = t_df["Volume"].fillna(0).astype("int64")

            # Normalize yfinance Stock Splits (0.0 means no split -> convert to 1.0 multiplier)
            if "Stock Splits" in t_df.columns:
                splits = t_df["Stock Splits"].fillna(0.0)
                t_df["split_factor"] = splits.apply(
                    lambda x: 1.0 if x == 0.0 else float(x)
                )
            else:
                t_df["split_factor"] = 1.0

            if "Dividends" in t_df.columns:
                t_df["dividend_amount"] = t_df["Dividends"].fillna(0.0)
            else:
                t_df["dividend_amount"] = 0.0

            t_df["_ingested_at"] = ingested_at

            frames.append(
                t_df[
                    [
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
                ]
            )
        except (KeyError, ValueError, TypeError) as exc:
            print(f"[WARN] Skipping {ticker} due to error: {exc}")

    if not frames:
        print("[WARN] No stock price frames were collected.")
        return

    full_df = pd.concat(frames, ignore_index=True)

    con.register("df_prices", full_df)
    con.execute("""
        INSERT OR REPLACE INTO bronze.bronze_stock_prices_daily
        SELECT
            ticker,
            trade_date,
            ROUND(CAST(open_price AS DECIMAL(12, 4)), 4),
            ROUND(CAST(high_price AS DECIMAL(12, 4)), 4),
            ROUND(CAST(low_price AS DECIMAL(12, 4)), 4),
            ROUND(CAST(close_price AS DECIMAL(12, 4)), 4),
            CAST(volume AS BIGINT),
            ROUND(CAST(split_factor AS DECIMAL(10, 6)), 6),
            ROUND(CAST(dividend_amount AS DECIMAL(10, 4)), 4),
            _ingested_at
        FROM df_prices;
    """)
    con.unregister("df_prices")
    print(
        f"[OK] Loaded {len(full_df):,} daily price rows into bronze_stock_prices_daily."
    )


def seed_initial_manual_execution(con: duckdb.DuckDBPyConnection) -> None:
    """Seeds a single baseline record in bronze_manual_executions if the table is empty."""
    count = con.execute(
        "SELECT COUNT(*) FROM bronze.bronze_manual_executions;"
    ).fetchone()[0]
    if count == 0:
        con.execute(
            """
            INSERT INTO bronze.bronze_manual_executions VALUES (
                ?, DATE '2026-01-02', 'SPY', 'BUY', 10.0000, 585.0000,
                'Initial safe-harbor benchmark seed trade', ?
            );
            """,
            [str(uuid.uuid4()), datetime.now(UTC).replace(tzinfo=None)],
        )
        print("[OK] Seeded 1 initial trade in bronze_manual_executions.")
    else:
        print(f"[OK] bronze_manual_executions already contains {count} row(s).")


def verify_counts(con: duckdb.DuckDBPyConnection) -> None:
    """Prints a summary table of row counts across all 4 Bronze tables."""
    print("\n--- BRONZE LAYER SUMMARY ---")
    tables = [
        "bronze_sp500_constituents",
        "bronze_macro_indicators",
        "bronze_stock_prices_daily",
        "bronze_manual_executions",
    ]
    for tbl in tables:
        cnt = con.execute(f"SELECT COUNT(*) FROM bronze.{tbl};").fetchone()[0]
        print(f"{tbl:<30} : {cnt:>10,} rows")


def main() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(DB_PATH)) as con:
        init_bronze_schema(con)
        tickers = ingest_sp500_constituents(con)
        ingest_macro_indicators(con)
        ingest_stock_prices(con, tickers)
        seed_initial_manual_execution(con)
        verify_counts(con)


if __name__ == "__main__":
    main()
