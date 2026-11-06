with raw_strategies as (
    select
        upper(trim(strategy_id)) as strategy_id,
        trim(strategy_name) as strategy_name,
        trim(strategy_description) as strategy_description,
        cast(is_active as boolean) as is_active,
        cast(portfolio_weight as double) as raw_portfolio_weight,
        cast(lookback_window_years as integer) as lookback_window_years,
        cast(rebalance_band_pct as decimal(8, 6)) as rebalance_band_pct,
        cast(sector_cap_excess_pct as decimal(8, 6)) as sector_cap_excess_pct,
        cast(corr_cluster_threshold as decimal(8, 6)) as corr_cluster_threshold,
        cast(halt_on_high_vol as boolean) as halt_on_high_vol,
        _updated_at
    from {{ source('bronze', 'bronze_strategies') }}
    where strategy_id is not null
),

active_weight_sum as (
    select
        sum(case when is_active = true then raw_portfolio_weight else 0.0 end) as total_active_weight
    from raw_strategies
)

select
    s.strategy_id,
    s.strategy_name,
    s.strategy_description,
    s.is_active,
    round(cast(s.raw_portfolio_weight as decimal(8, 6)), 6) as configured_portfolio_weight,
    round(
        cast(
            case
                when s.is_active = true and coalesce(w.total_active_weight, 0.0) > 0.0
                then s.raw_portfolio_weight / w.total_active_weight
                else 0.0
            end as decimal(8, 6)
        ),
        6
    ) as normalized_portfolio_weight,
    s.lookback_window_years,
    s.rebalance_band_pct,
    s.sector_cap_excess_pct,
    s.corr_cluster_threshold,
    s.halt_on_high_vol,
    s._updated_at
from raw_strategies s
cross join active_weight_sum w
order by s.strategy_id
