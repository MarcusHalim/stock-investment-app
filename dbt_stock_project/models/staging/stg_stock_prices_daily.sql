with active_trading_days as (
    select
        calendar_date as trade_date,
        trading_day_seq
    from {{ ref('stg_trading_calendar') }}
    where is_trading_day = true
),

raw_prices as (
    select
        ticker,
        trade_date,
        cast(close_price as double) as raw_close_price,
        cast(volume as bigint) as volume,
        case
            when split_factor is null or split_factor <= 0 then 1.0
            else cast(split_factor as double)
        end as split_factor,
        coalesce(cast(dividend_amount as double), 0.0) as dividend_amount
    from {{ source('bronze', 'bronze_stock_prices_daily') }}
    where
        close_price is not null
        and close_price > 0
),

-- Align prices strictly to valid exchange trading days and get prior close for dividend math
aligned_prices as (
    select
        p.ticker,
        p.trade_date,
        p.raw_close_price,
        p.volume,
        p.split_factor,
        p.dividend_amount,
        lag(p.raw_close_price) over (
            partition by p.ticker
            order by p.trade_date
        ) as prev_raw_close
    from raw_prices as p
    inner join active_trading_days as c
        on p.trade_date = c.trade_date
),

-- Calculate backward cumulative split & dividend adjustment multipliers
adjustment_factors as (
    select
        ticker,
        trade_date,
        raw_close_price,
        volume,
        -- Cumulative product of all FUTURE splits (strictly after current trade_date)
        exp(
            coalesce(
                sum(ln(split_factor)) over (
                    partition by ticker
                    order by trade_date desc
                    rows between unbounded preceding and 1 preceding
                ),
                0.0
            )
        ) as cum_future_split_factor,
        -- Cumulative product of all FUTURE dividend reinvestment factors
        exp(
            coalesce(
                sum(
                    ln(
                        case
                            when
                                dividend_amount > 0
                                and prev_raw_close is not null
                                and prev_raw_close > dividend_amount
                                then 1.0 - (dividend_amount / prev_raw_close)
                            else 1.0
                        end
                    )
                ) over (
                    partition by ticker
                    order by trade_date desc
                    rows between unbounded preceding and 1 preceding
                ),
                0.0
            )
        ) as cum_future_div_multiplier
    from aligned_prices
),

adjusted_series as (
    select
        ticker,
        trade_date,
        raw_close_price,
        volume,
        raw_close_price / cum_future_split_factor as split_adj_close_price,
        (raw_close_price / cum_future_split_factor) * cum_future_div_multiplier as total_return_close
    from adjustment_factors
),

window_metrics as (
    select
        ticker,
        trade_date,
        round(cast(raw_close_price as decimal(12, 4)), 4) as raw_close_price,
        round(cast(split_adj_close_price as decimal(12, 4)), 4) as split_adj_close_price,
        round(cast(total_return_close as decimal(12, 4)), 4) as total_return_close,
        lag(total_return_close) over (
            partition by ticker
            order by trade_date
        ) as prev_total_return_close,
        count(split_adj_close_price) over (
            partition by ticker
            order by trade_date
            rows between 199 preceding and current row
        ) as window_200d_obs,
        avg(split_adj_close_price) over (
            partition by ticker
            order by trade_date
            rows between 199 preceding and current row
        ) as raw_sma_200d,
        coalesce(volume > 0, false) as is_actively_traded
    from adjusted_series
)

select
    ticker,
    trade_date,
    raw_close_price,
    split_adj_close_price,
    total_return_close,
    round(
        cast(
            case
                when prev_total_return_close > 0
                    then (total_return_close / prev_total_return_close) - 1.0
            end as decimal(10, 6)
        ),
        6
    ) as daily_return,
    case
        when window_200d_obs = 200
            then round(cast(raw_sma_200d as decimal(12, 4)), 4)
    end as sma_200d,
    case
        when window_200d_obs = 200
            then split_adj_close_price > raw_sma_200d
    end as is_above_200d_sma,
    is_actively_traded
from window_metrics
