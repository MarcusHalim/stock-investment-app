with daily_paired as (
    select
        p.trade_date,
        p.ticker,
        cast(p.daily_return as double) as stock_ret,
        cast(m.spy_daily_return as double) as spy_ret
    from {{ ref('stg_stock_prices_daily') }} as p
    inner join {{ ref('stg_macro_regimes') }} as m
        on p.trade_date = m.trade_date
    where
        p.daily_return is not null
        and m.spy_daily_return is not null
),

rolling_moments as (
    select
        trade_date,
        ticker,
        stock_ret,
        spy_ret,
        count(*) over (
            partition by ticker
            order by trade_date
            rows between 89 preceding and current row
        ) as obs_90d,
        avg(spy_ret) over (
            partition by ticker
            order by trade_date
            rows between 89 preceding and current row
        ) as avg_x,
        avg(stock_ret) over (
            partition by ticker
            order by trade_date
            rows between 89 preceding and current row
        ) as avg_y,
        avg(spy_ret * stock_ret) over (
            partition by ticker
            order by trade_date
            rows between 89 preceding and current row
        ) as avg_xy,
        avg(spy_ret * spy_ret) over (
            partition by ticker
            order by trade_date
            rows between 89 preceding and current row
        ) as avg_xx
    from daily_paired
),

regression_params as (
    select
        trade_date,
        ticker,
        stock_ret,
        spy_ret,
        obs_90d,
        case
            when obs_90d = 90 and (avg_xx - (avg_x * avg_x)) > 1e-12
                then (avg_xy - (avg_x * avg_y)) / (avg_xx - (avg_x * avg_x))
        end as beta_90d,
        avg_y,
        avg_x
    from rolling_moments
),

residuals_calc as (
    select
        trade_date,
        ticker,
        obs_90d,
        beta_90d,
        case
            when beta_90d is not null
                then avg_y - (beta_90d * avg_x)
        end as alpha_90d,
        case
            when beta_90d is not null
                then (avg_y - (beta_90d * avg_x)) + (beta_90d * spy_ret)
        end as expected_ret,
        case
            when beta_90d is not null
                then stock_ret - ((avg_y - (beta_90d * avg_x)) + (beta_90d * spy_ret))
        end as daily_residual
    from regression_params
),

residual_stats as (
    select
        trade_date,
        ticker,
        beta_90d,
        alpha_90d,
        expected_ret,
        daily_residual,
        count(daily_residual) over (
            partition by ticker
            order by trade_date
            rows between 89 preceding and current row
        ) as res_obs_90d,
        stddev_samp(daily_residual) over (
            partition by ticker
            order by trade_date
            rows between 89 preceding and current row
        ) as residual_90d_stddev
    from residuals_calc
    where beta_90d is not null
)

select
    trade_date,
    ticker,
    round(cast(beta_90d as decimal(10, 4)), 4) as rolling_90d_beta,
    round(cast(alpha_90d as decimal(10, 6)), 6) as rolling_90d_alpha,
    round(cast(expected_ret as decimal(10, 6)), 6) as expected_daily_return,
    round(cast(daily_residual as decimal(10, 6)), 6) as daily_residual,
    round(cast(residual_90d_stddev as decimal(10, 6)), 6) as residual_90d_stddev,
    round(
        cast(
            case
                when res_obs_90d >= 30 and residual_90d_stddev > 1e-9
                    then daily_residual / residual_90d_stddev
                else 0.0
            end as decimal(10, 4)
        ),
        4
    ) as residual_z_score,
    coalesce(
        res_obs_90d >= 30
        and residual_90d_stddev > 1e-9
        and (daily_residual / residual_90d_stddev) < -3.0,
        false
    ) as is_anomaly_alert
from residual_stats
