with month_end_dates as (
    select
        calendar_date as eval_date,
        strftime(calendar_date, '%Y-%m') as year_month
    from {{ ref('stg_trading_calendar') }}
    where is_month_end_trading_day = true
),

monthly_stock_prices as (
    select
        m.year_month,
        m.eval_date,
        p.ticker,
        cast(p.total_return_close as double) as total_return_close,
        lag(cast(p.total_return_close as double)) over (
            partition by p.ticker
            order by m.eval_date
        ) as prev_total_return_close
    from month_end_dates m
    inner join {{ ref('stg_stock_prices_daily') }} p
        on m.eval_date = p.trade_date
    inner join {{ ref('stg_sp500_historical_membership') }} h
        on p.ticker = h.ticker
        and p.trade_date = h.trade_date
        and h.is_in_index = true
),

monthly_macro as (
    select
        strftime(r.trade_date, '%Y-%m') as year_month,
        max(
            case
                when r.trade_date = m.eval_date
                then cast(r.spy_close as double)
            end
        ) as spy_month_close,
        exp(
            sum(
                ln(
                    greatest(
                        0.0001,
                        1.0 + coalesce(cast(r.spxl_daily_return as double), 0.0)
                    )
                )
            )
        ) - 1.0 as spxl_compounded_return,
        avg(cast(r.risk_free_rate_annual as double)) as avg_rfr_annual
    from {{ ref('stg_macro_regimes') }} r
    inner join month_end_dates m
        on strftime(r.trade_date, '%Y-%m') = m.year_month
    group by strftime(r.trade_date, '%Y-%m')
),

monthly_macro_with_spy_lag as (
    select
        year_month,
        spy_month_close,
        lag(spy_month_close) over (order by year_month) as prev_spy_month_close,
        spxl_compounded_return,
        avg_rfr_annual
    from monthly_macro
),

calculated_returns as (
    select
        s.year_month,
        s.eval_date,
        s.ticker,
        (s.total_return_close / s.prev_total_return_close) - 1.0 as raw_stock_ret,
        (m.spy_month_close / m.prev_spy_month_close) - 1.0 as raw_spy_ret,
        m.spxl_compounded_return as raw_spxl_ret,
        coalesce(m.avg_rfr_annual, 0.025) / 12.0 as monthly_rfr
    from monthly_stock_prices s
    inner join monthly_macro_with_spy_lag m
        on s.year_month = m.year_month
    where
        s.prev_total_return_close > 0
        and m.prev_spy_month_close > 0
)

select
    year_month,
    eval_date,
    ticker,
    round(cast(raw_stock_ret as decimal(10, 6)), 6) as stock_return_monthly,
    round(cast(raw_spy_ret as decimal(10, 6)), 6) as spy_return_monthly,
    round(cast(raw_spxl_ret as decimal(10, 6)), 6) as spxl_return_monthly,
    round(cast(monthly_rfr as decimal(10, 6)), 6) as risk_free_rate_monthly,
    round(cast(raw_stock_ret - raw_spy_ret as decimal(10, 6)), 6) as excess_return_monthly,
    round(cast(raw_stock_ret - raw_spxl_ret as decimal(10, 6)), 6) as excess_vs_spxl_monthly,
    coalesce(raw_spy_ret >= 0.0, false) as is_spy_up_month
from calculated_returns
order by eval_date, ticker
