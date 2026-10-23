with active_trading_days as (
    select
        calendar_date as trade_date
    from {{ ref('stg_trading_calendar') }}
    where is_trading_day = true
),

constituents as (
    select
        ticker,
        company_name,
        sector,
        date_added,
        date_removed
    from {{ source('bronze', 'bronze_sp500_constituents') }}
),

first_ingest_dates as (
    select
        ticker,
        min(trade_date) as first_price_date
    from {{ source('bronze', 'bronze_stock_prices_daily') }}
    group by ticker
),

-- Expand point-in-time membership across all active trading days
pit_membership as (
    select
        c.trade_date,
        idx.ticker,
        idx.company_name,
        idx.sector,
        case
            when c.trade_date >= idx.date_added
                 and (idx.date_removed is null or c.trade_date <= idx.date_removed)
            then true
            else false
        end as is_in_index
    from active_trading_days c
    inner join constituents idx
        on c.trade_date >= idx.date_added
       and (idx.date_removed is null or c.trade_date <= idx.date_removed)
),

-- Deduplicate in case a ticker has overlapping constituent intervals
deduped_membership as (
    select
        trade_date,
        ticker,
        max(company_name) as company_name,
        max(sector) as sector,
        bool_or(is_in_index) as is_in_index
    from pit_membership
    group by trade_date, ticker
)

select
    m.trade_date,
    m.ticker,
    m.company_name,
    m.sector,
    m.is_in_index,
    round(
        cast(
            coalesce(
                (m.trade_date - f.first_price_date) / 365.25,
                0.0
            ) as decimal(6, 2)
        ),
        2
    ) as years_since_first_ingest
from deduped_membership m
left join first_ingest_dates f
    on m.ticker = f.ticker