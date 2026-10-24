with windows as (
    select 3 as lookback_window_years, 36 as window_months
    union all
    select 5 as lookback_window_years, 60 as window_months
    union all
    select 10 as lookback_window_years, 120 as window_months
),

monthly_returns as (
    select
        year_month as eval_date,
        ticker,
        cast(stock_return_monthly as double) as r_s,
        cast(spy_return_monthly as double) as r_m,
        cast(excess_return_monthly as double) as r_excess,
        is_spy_up_month,
        row_number() over (
            partition by ticker
            order by year_month
        ) as month_seq
    from {{ ref('fct_stock_returns_monthly') }}
),

-- Self-join across the 3 lookback horizons (36, 60, and 120 months)
windowed_monthly as (
    select
        curr.eval_date,
        curr.ticker,
        w.lookback_window_years,
        w.window_months,
        count(hist.eval_date) as obs_months,
        -- Annualized excess return (arithmetic mean * 12 for stability across sign flips)
        avg(hist.r_excess) * 12.0 as annualized_excess_return,
        -- Annualized Tracking Error
        stddev_samp(hist.r_excess) * sqrt(12.0) as tracking_error,
        -- Annualized Downside Semi-Deviation below SPY (MAR = SPY return)
        sqrt(
            avg(
                case
                    when hist.r_excess < 0.0 then hist.r_excess * hist.r_excess
                    else 0.0
                end
            ) * 12.0
        ) as downside_semi_deviation,
        -- Up-Market Capture Ratio (stock avg return / SPY avg return in positive SPY months)
        avg(case when hist.is_spy_up_month = true then hist.r_s end)
            / nullif(avg(case when hist.is_spy_up_month = true then hist.r_m end), 0.0)
            as up_market_capture,
        -- Down-Market Capture Ratio (stock avg return / SPY avg return in negative SPY months)
        avg(case when hist.is_spy_up_month = false then hist.r_s end)
            / nullif(avg(case when hist.is_spy_up_month = false then hist.r_m end), 0.0)
            as down_market_capture
    from monthly_returns curr
    cross join windows w
    inner join monthly_returns hist
        on curr.ticker = hist.ticker
       and hist.month_seq between (curr.month_seq - w.window_months + 1) and curr.month_seq
    group by
        curr.eval_date,
        curr.ticker,
        w.lookback_window_years,
        w.window_months
    having count(hist.eval_date) = w.window_months
),

-- Compute monthly peak-to-trough drawdown curve from daily total_return_close
daily_drawdowns as (
    select
        ticker,
        cast(date_trunc('month', trade_date) as date) as eval_date,
        cast(total_return_close as double) as tr_close,
        max(cast(total_return_close as double)) over (
            partition by ticker
            order by trade_date
            rows between unbounded preceding and current row
        ) as running_peak
    from {{ ref('stg_stock_prices_daily') }}
),

monthly_min_dd as (
    select
        ticker,
        eval_date,
        min((tr_close - running_peak) / nullif(running_peak, 0.0)) as month_max_dd
    from daily_drawdowns
    group by ticker, eval_date
),

rolling_dd as (
    select
        m.eval_date,
        m.ticker,
        m.lookback_window_years,
        min(d.month_max_dd) as max_drawdown
    from windowed_monthly m
    inner join monthly_min_dd d
        on m.ticker = d.ticker
       and d.eval_date <= m.eval_date
       and d.eval_date > cast(m.eval_date - (m.lookback_window_years * 365) * interval '1 day' as date)
    group by
        m.eval_date,
        m.ticker,
        m.lookback_window_years
),

metrics_with_ratios as (
    select
        m.eval_date,
        m.ticker,
        m.lookback_window_years,
        m.annualized_excess_return,
        m.tracking_error,
        case
            when m.tracking_error > 1e-9
            then m.annualized_excess_return / m.tracking_error
        end as information_ratio,
        m.downside_semi_deviation,
        case
            when m.downside_semi_deviation > 1e-9
            then m.annualized_excess_return / m.downside_semi_deviation
        end as sortino_ratio,
        coalesce(d.max_drawdown, 0.0) as max_drawdown,
        m.up_market_capture,
        m.down_market_capture,
        -- 12-month (~50w) and 48-month (~200w) moving averages of Down-Market Capture
        avg(m.down_market_capture) over (
            partition by m.ticker, m.lookback_window_years
            order by m.eval_date
            rows between 11 preceding and current row
        ) as down_capture_sma_50w,
        avg(m.down_market_capture) over (
            partition by m.ticker, m.lookback_window_years
            order by m.eval_date
            rows between 47 preceding and current row
        ) as down_capture_sma_200w
    from windowed_monthly m
    left join rolling_dd d
        on m.eval_date = d.eval_date
       and m.ticker = d.ticker
       and m.lookback_window_years = d.lookback_window_years
)

select
    eval_date,
    ticker,
    lookback_window_years,
    round(cast(annualized_excess_return as decimal(10, 6)), 6) as annualized_excess_return,
    round(cast(tracking_error as decimal(10, 6)), 6) as tracking_error,
    round(cast(coalesce(information_ratio, 0.0) as decimal(10, 4)), 4) as information_ratio,
    round(cast(downside_semi_deviation as decimal(10, 6)), 6) as downside_semi_deviation,
    round(cast(coalesce(sortino_ratio, 0.0) as decimal(10, 4)), 4) as sortino_ratio,
    round(cast(max_drawdown as decimal(10, 6)), 6) as max_drawdown,
    round(cast(coalesce(up_market_capture, 1.0) as decimal(10, 4)), 4) as up_market_capture,
    round(cast(coalesce(down_market_capture, 1.0) as decimal(10, 4)), 4) as down_market_capture,
    round(cast(coalesce(down_capture_sma_50w, 1.0) as decimal(10, 4)), 4) as down_capture_sma_50w,
    round(cast(coalesce(down_capture_sma_200w, 1.0) as decimal(10, 4)), 4) as down_capture_sma_200w
from metrics_with_ratios
order by eval_date, ticker, lookback_window_years