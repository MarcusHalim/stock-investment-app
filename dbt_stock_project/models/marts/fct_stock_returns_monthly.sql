with month_end_dates as (
    select
        calendar_date as trade_date,
        cast(date_trunc('month', calendar_date) as date) as year_month
    from {{ ref('stg_trading_calendar') }}
    where is_month_end_trading_day = true
),

monthly_spy as (
    select
        m.year_month,
        m.trade_date,
        cast(r.spy_close as double) as spy_close,
        lag(cast(r.spy_close as double)) over (order by m.year_month) as prev_spy_close,
        lag(m.year_month) over (order by m.year_month) as prev_spy_month
    from month_end_dates as m
    inner join {{ ref('stg_macro_regimes') }} as r
        on m.trade_date = r.trade_date
),

spy_returns as (
    select
        year_month,
        trade_date,
        case
            when
                prev_spy_close > 0
                and (year_month - prev_spy_month) <= 35
                then (spy_close / prev_spy_close) - 1.0
        end as spy_return_monthly
    from monthly_spy
),

monthly_stock as (
    select
        m.year_month,
        p.ticker,
        cast(p.total_return_close as double) as total_return_close,
        lag(cast(p.total_return_close as double)) over (
            partition by p.ticker
            order by m.year_month
        ) as prev_total_return_close,
        lag(m.year_month) over (
            partition by p.ticker
            order by m.year_month
        ) as prev_stock_month
    from month_end_dates as m
    inner join {{ ref('stg_stock_prices_daily') }} as p
        on m.trade_date = p.trade_date
),

stock_returns as (
    select
        year_month,
        ticker,
        case
            when
                prev_total_return_close > 0
                and (year_month - prev_stock_month) <= 35
                then (total_return_close / prev_total_return_close) - 1.0
        end as stock_return_monthly
    from monthly_stock
)

select
    s.year_month,
    s.ticker,
    round(cast(s.stock_return_monthly as decimal(10, 6)), 6) as stock_return_monthly,
    round(cast(b.spy_return_monthly as decimal(10, 6)), 6) as spy_return_monthly,
    round(
        cast(s.stock_return_monthly - b.spy_return_monthly as decimal(10, 6)),
        6
    ) as excess_return_monthly,
    coalesce(b.spy_return_monthly > 0, false) as is_spy_up_month
from stock_returns as s
inner join spy_returns as b
    on s.year_month = b.year_month
where
    s.stock_return_monthly is not null
    and b.spy_return_monthly is not null
order by s.year_month, s.ticker
