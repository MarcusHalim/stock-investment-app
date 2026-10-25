"""
Deterministic Bronze Layer Mock Seeder for CI/CD Integration Testing.
Populates 6 years of synthetic daily data so dbt build can validate all 3y and 5y models.
"""

import uuid
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb
import pandas as pd
from ingest_bronze_local import init_bronze_schema

DB_PATH = Path("data/stock_app_dev.duckdb")


def seed_ci_database() -> None:
    """Seeds synthetic Bronze data into a fresh DuckDB instance for CI testing."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    now_ts = datetime.now(UTC).replace(tzinfo=None)

    # Generate business days across 6+ years (ensures >60 months for 5-year rolling windows)
    b_days = pd.date_range(start="2019-01-01", end="2026-02-01", freq="B")

    with duckdb.connect(str(DB_PATH)) as con:
        init_bronze_schema(con)

        # 1. Seed bronze_sp500_constituents
        constituents_df = pd.DataFrame(
            [
                {
                    "ticker": "AAPL",
                    "company_name": "Apple Inc.",
                    "sector": "Information Technology",
                    "sub_industry": "Technology Hardware",
                    "date_added": date(1990, 1, 1),
                    "date_removed": None,
                    "_ingested_at": now_ts,
                },
                {
                    "ticker": "JNJ",
                    "company_name": "Johnson & Johnson",
                    "sector": "Health Care",
                    "sub_industry": "Pharmaceuticals",
                    "date_added": date(1990, 1, 1),
                    "date_removed": None,
                    "_ingested_at": now_ts,
                },
            ]
        )
        con.register("df_const", constituents_df)
        con.execute("""
            INSERT OR REPLACE INTO bronze.bronze_sp500_constituents
            SELECT
                ticker,
                company_name,
                sector,
                sub_industry,
                date_added,
                CAST(date_removed AS DATE),
                _ingested_at
            FROM df_const;
        """)
        con.unregister("df_const")

        # 2. Seed bronze_macro_indicators
        macro_rows = []
        spy_price = 250.0
        for i, dt in enumerate(b_days):
            # Alternate slight up/down drift so both Up-Market and Down-Market months exist
            daily_drift = 0.0006 if (i % 7 != 0) else -0.0025
            spy_price = round(spy_price * (1.0 + daily_drift), 4)
            macro_rows.append(
                {
                    "trade_date": dt.date(),
                    "vix_close": 16.5000,
                    "spy_close": spy_price,
                    "_ingested_at": now_ts,
                }
            )
        macro_df = pd.DataFrame(macro_rows)
        con.register("df_macro", macro_df)
        con.execute(
            "INSERT OR REPLACE INTO bronze.bronze_macro_indicators SELECT * FROM df_macro;"
        )
        con.unregister("df_macro")

        # 3. Seed bronze_stock_prices_daily
        price_rows = []
        for ticker, base_p, mult in [("AAPL", 100.0, 1.25), ("JNJ", 130.0, 0.75)]:
            curr_p = base_p
            for i, dt in enumerate(b_days):
                base_ret = 0.0007 if (i % 7 != 0) else -0.0020
                stock_ret = base_ret * mult
                curr_p = round(curr_p * (1.0 + stock_ret), 4)
                price_rows.append(
                    {
                        "ticker": ticker,
                        "trade_date": dt.date(),
                        "open_price": curr_p,
                        "high_price": round(curr_p * 1.005, 4),
                        "low_price": round(curr_p * 0.995, 4),
                        "close_price": curr_p,
                        "volume": 1000000,
                        "split_factor": 1.0,
                        "dividend_amount": 0.0,
                        "_ingested_at": now_ts,
                    }
                )
        prices_df = pd.DataFrame(price_rows)
        con.register("df_prices", prices_df)
        con.execute(
            "INSERT OR REPLACE INTO bronze.bronze_stock_prices_daily SELECT * FROM df_prices;"
        )
        con.unregister("df_prices")

        # 4. Seed bronze_manual_executions
        con.execute(
            """
            INSERT OR REPLACE INTO bronze.bronze_manual_executions VALUES (
                ?, DATE '2026-01-02', 'SPY', 'BUY', 10.0000, 500.0000,
                'CI mock seed trade', ?
            );
            """,
            [str(uuid.uuid4()), now_ts],
        )

    print("[OK] CI mock Bronze database seeded successfully.")


if __name__ == "__main__":
    seed_ci_database()
