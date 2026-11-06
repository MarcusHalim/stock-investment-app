with monthly_returns as (
    select
        eval_date,
        year_month,
        ticker,
        cast(stock_return_monthly as double) as stock_ret,
        cast(spy_return_monthly as double) as spy_ret,
        cast(spxl_return_monthly as double) as spxl_ret,
        cast(excess_return_monthly as double) as excess_ret,
        is_spy_up_month
    from {{ ref('fct_stock_returns_monthly') }}
),

lookback_horizons as (
    select 3 as lookback_window_years, 36 as window_months
    union all
    select 5 as lookback_window_years, 60 as window_months
    union all
    select 10 as lookback_window_years, 120 as window_months
),

windowed_pairs as (
    select
        curr.eval_date,
        curr.ticker,
        h.lookback_window_years,
        h.window_months,
        hist.eval_date as hist_date,
        hist.stock_ret,
        hist.spy_ret,
        hist.spxl_ret,
        hist.excess_ret,
        hist.is_spy_up_month
    from monthly_returns curr
    cross join lookback_horizons h
    inner join monthly_returns hist
        on curr.ticker = hist.ticker
        and curr.eval_date >= hist.eval_date
        and curr.eval_date - to_months(h.window_months) < hist.eval_date
),

cumulative_wealth as (
    select
        eval_date,
        ticker,
        lookback_window_years,
        window_months,
        hist_date,
        stock_ret,
        spy_ret,
        spxl_ret,
        excess_ret,
        is_spy_up_month,
        exp(
            sum(ln(greatest(0.0001, 1.0 + stock_ret))) over (
                partition by eval_date, ticker, lookback_window_years
                order by hist_date
                rows between unbounded preceding and current row
            )
        ) as wealth_index
    from windowed_pairs
),

drawdown_path as (
    select
        eval_date,
        ticker,
        lookback_window_years,
        window_months,
        hist_date,
        stock_ret,
        spy_ret,
        spxl_ret,
        excess_ret,
        is_spy_up_month,
        wealth_index,
        max(wealth_index) over (
            partition by eval_date, ticker, lookback_window_years
            order by hist_date
            rows between unbounded preceding and current row
        ) as peak_wealth
    from cumulative_wealth
),

aggregated_metrics as (
    select
        eval_date,
        ticker,
        lookback_window_years,
        window_months,
        count(*) as obs_months,
        avg(excess_ret) * 12.0 as ann_excess_return,
        stddev_samp(excess_ret) * sqrt(12.0) as tracking_error,
        sqrt(
            avg(
                case
                    when excess_ret < 0.0 then power(excess_ret, 2)
                    else 0.0
                end
            )
        ) * sqrt(12.0) as downside_semi_dev,
        min((wealth_index / nullif(peak_wealth, 0.0)) - 1.0) as max_dd,
        avg(case when is_spy_up_month = true then stock_ret end)
        / nullif(avg(case when is_spy_up_month = true then spy_ret end), 0.0) as up_capture,
        avg(case when is_spy_up_month = false then stock_ret end)
        / nullif(avg(case when is_spy_up_month = false then spy_ret end), 0.0) as down_capture,
        avg(case when is_spy_up_month = true then stock_ret end)
        / nullif(avg(case when is_spy_up_month = true then spxl_ret end), 0.0) as spxl_up_capture,
        power(
            exp(sum(ln(greatest(0.0001, 1.0 + stock_ret)))),
            12.0 / nullif(count(*), 0)
        ) - 1.0 as cagr_stock,
        power(
            exp(sum(ln(greatest(0.0001, 1.0 + spy_ret)))),
            12.0 / nullif(count(*), 0)
        ) - 1.0 as cagr_spy,
        power(
            exp(sum(ln(greatest(0.0001, 1.0 + spxl_ret)))),
            12.0 / nullif(count(*), 0)
        ) - 1.0 as cagr_spxl
    from drawdown_path
    group by
        eval_date,
        ticker,
        lookback_window_years,
        window_months
    having count(*) >= cast(window_months * 0.80 as integer)
)

select
    eval_date,
    ticker,
    lookback_window_years,
    round(
        cast(greatest(0.0001, downside_semi_dev) as decimal(10, 6)),
        6
    ) as downside_semi_deviation,
    round(
        cast(
            ann_excess_return / greatest(0.0001, downside_semi_dev) as decimal(10, 4)
        ),
        4
    ) as sortino_ratio,
    round(
        cast(
            ann_excess_return / greatest(0.0001, coalesce(tracking_error, 0.0001)) as decimal(10, 4)
        ),
        4
    ) as information_ratio,
    round(cast(coalesce(max_dd, 0.0) as decimal(10, 6)), 6) as max_drawdown,
    round(cast(coalesce(up_capture, 1.0) as decimal(10, 4)), 4) as up_market_capture,
    round(cast(coalesce(down_capture, 1.0) as decimal(10, 4)), 4) as down_market_capture,
    round(cast(coalesce(spxl_up_capture, 0.33) as decimal(10, 4)), 4) as spxl_up_capture_ratio,
    round(cast(coalesce(cagr_stock, 0.0) as decimal(10, 6)), 6) as annualized_cagr,
    round(cast(coalesce(cagr_spy, 0.0) as decimal(10, 6)), 6) as spy_annualized_cagr,
    round(cast(coalesce(cagr_spxl, 0.0) as decimal(10, 6)), 6) as spxl_annualized_cagr
from aggregated_metrics
order by eval_date, ticker, lookback_window_years
