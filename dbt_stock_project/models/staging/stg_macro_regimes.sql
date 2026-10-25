with macro_base as (
    select
        trade_date,
        cast(spy_close as double) as spy_close,
        cast(vix_close as double) as vix_close,
        lag(cast(spy_close as double)) over (order by trade_date) as prev_spy_close,
        count(spy_close) over (
            order by trade_date
            rows between 199 preceding and current row
        ) as spy_window_obs,
        avg(cast(spy_close as double)) over (
            order by trade_date
            rows between 199 preceding and current row
        ) as raw_spy_sma_200d
    from {{ source('bronze', 'bronze_macro_indicators') }}
    where spy_close is not null
),

-- Calculate daily Market Breadth (% of active S&P 500 members above their 200-day SMA)
daily_breadth as (
    select
        p.trade_date,
        count(case when p.sma_200d is not null then 1 end) as eligible_constituents,
        sum(case when p.is_above_200d_sma = true then 1 else 0 end) as above_sma_count
    from {{ ref('stg_stock_prices_daily') }} as p
    inner join {{ ref('stg_sp500_historical_membership') }} as m
        on
            p.ticker = m.ticker
            and p.trade_date = m.trade_date
            and m.is_in_index = true
    group by p.trade_date
),

combined_macro as (
    select
        b.trade_date,
        round(cast(b.spy_close as decimal(12, 4)), 4) as spy_close,
        round(
            cast(
                case
                    when b.prev_spy_close > 0
                        then (b.spy_close / b.prev_spy_close) - 1.0
                end as decimal(10, 6)
            ),
            6
        ) as spy_daily_return,
        case
            when b.spy_window_obs = 200
                then round(cast(b.raw_spy_sma_200d as decimal(12, 4)), 4)
        end as spy_sma_200d,
        round(cast(b.vix_close as decimal(8, 4)), 4) as vix_close,
        case
            when d.eligible_constituents > 0
                then round(
                    cast(d.above_sma_count as double) / cast(d.eligible_constituents as double),
                    4
                )
        end as market_breadth_pct
    from macro_base as b
    left join daily_breadth as d
        on b.trade_date = d.trade_date
)

select
    trade_date,
    spy_close,
    spy_daily_return,
    spy_sma_200d,
    vix_close,
    cast(market_breadth_pct as decimal(6, 4)) as market_breadth_pct,
    case
        when vix_close > 30.0 then 'HIGH_VOLATILITY'
        when market_breadth_pct is not null and market_breadth_pct < 0.30 then 'BEAR_CONTRACTION'
        else 'BULL_STABLE'
    end as regime_status
from combined_macro
order by trade_date
