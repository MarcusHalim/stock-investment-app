with macro_base as (
    select
        trade_date,
        cast(spy_close as double) as spy_close,
        cast(vix_close as double) as vix_close,
        cast(spxl_close as double) as spxl_close,
        coalesce(cast(risk_free_rate_annual as double), 0.025) as rfr_annual,
        lag(cast(spy_close as double)) over (order by trade_date) as prev_spy_close,
        lag(cast(spxl_close as double)) over (order by trade_date) as prev_spxl_close,
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
    from {{ ref('stg_stock_prices_daily') }} p
    inner join {{ ref('stg_sp500_historical_membership') }} m
        on p.ticker = m.ticker
        and p.trade_date = m.trade_date
        and m.is_in_index = true
    group by p.trade_date
),

return_series as (
    select
        b.trade_date,
        b.spy_close,
        b.spxl_close,
        b.vix_close,
        b.rfr_annual,
        b.spy_window_obs,
        b.raw_spy_sma_200d,
        case
            when b.prev_spy_close > 0
            then (b.spy_close / b.prev_spy_close) - 1.0
        end as raw_spy_ret,
        case
            when b.spxl_close > 0 and b.prev_spxl_close > 0
            then (b.spxl_close / b.prev_spxl_close) - 1.0
        end as actual_spxl_ret,
        d.eligible_constituents,
        d.above_sma_count
    from macro_base b
    left join daily_breadth d
        on b.trade_date = d.trade_date
),

combined_macro as (
    select
        trade_date,
        round(cast(spy_close as decimal(12, 4)), 4) as spy_close,
        round(cast(raw_spy_ret as decimal(10, 6)), 6) as spy_daily_return,
        -- Option A2: Hybrid spliced actual SPXL post-Nov 2008 + synthetic 3x daily reset pre-Nov 2008
        round(
            cast(
                coalesce(
                    actual_spxl_ret,
                    case
                        when raw_spy_ret is not null
                        then greatest(
                            -0.95,
                            (3.0 * raw_spy_ret)
                            - (2.0 * (rfr_annual / 252.0))
                            - (0.0095 / 252.0)
                        )
                    end
                ) as decimal(10, 6)
            ),
            6
        ) as spxl_daily_return,
        round(cast(rfr_annual as decimal(8, 6)), 6) as risk_free_rate_annual,
        case
            when spy_window_obs = 200
            then round(cast(raw_spy_sma_200d as decimal(12, 4)), 4)
        end as spy_sma_200d,
        round(cast(vix_close as decimal(8, 4)), 4) as vix_close,
        case
            when eligible_constituents > 0
            then round(
                cast(above_sma_count as double) / cast(eligible_constituents as double),
                4
            )
        end as market_breadth_pct
    from return_series
)

select
    trade_date,
    spy_close,
    spy_daily_return,
    spxl_daily_return,
    risk_free_rate_annual,
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
