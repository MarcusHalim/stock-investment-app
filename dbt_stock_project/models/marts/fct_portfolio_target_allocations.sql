with strategies as (
    select
        strategy_id,
        is_active,
        cast(normalized_portfolio_weight as double) as strategy_portfolio_weight,
        cast(rebalance_band_pct as double) as rebalance_band_pct,
        cast(sector_cap_excess_pct as double) as sector_cap_excess_pct,
        cast(corr_cluster_threshold as double) as corr_cluster_threshold
    from {{ ref('dim_strategies') }}
),

buckets as (
    select
        strategy_id,
        bucket_id,
        cast(bucket_target_share as double) as bucket_target_share,
        weighting_scheme,
        route_to_spy_in_bear
    from {{ ref('dim_strategy_buckets') }}
),

eligible_candidates as (
    select
        c.strategy_id,
        c.eval_date,
        c.ticker,
        c.sector,
        c.current_bucket as assigned_bucket,
        greatest(0.0001, cast(c.downside_semi_deviation as double)) as downside_vol,
        s.is_active,
        s.strategy_portfolio_weight,
        s.rebalance_band_pct,
        s.sector_cap_excess_pct,
        s.corr_cluster_threshold,
        b.bucket_target_share,
        b.weighting_scheme,
        b.route_to_spy_in_bear
    from {{ ref('dim_stock_bucket_classification') }} c
    inner join strategies s
        on c.strategy_id = s.strategy_id
    inner join buckets b
        on c.strategy_id = b.strategy_id
        and c.current_bucket = b.bucket_id
    where c.current_bucket != 'UNASSIGNED'
),

distinct_eval_dates as (
    select distinct
        eval_date
    from {{ ref('dim_stock_bucket_classification') }}
),

eval_dates_per_strategy as (
    select distinct
        s.strategy_id,
        s.is_active,
        s.strategy_portfolio_weight,
        s.rebalance_band_pct,
        m.eval_date
    from strategies s
    cross join distinct_eval_dates m
),

daily_returns_1y as (
    select
        p.ticker,
        p.trade_date,
        (
            cast(p.total_return_close as double)
            / nullif(
                lag(cast(p.total_return_close as double)) over (
                    partition by p.ticker
                    order by p.trade_date
                ),
                0.0
            )
        ) - 1.0 as daily_ret
    from {{ ref('stg_stock_prices_daily') }} p
),

candidate_daily_window as (
    select
        c.strategy_id,
        c.eval_date,
        c.assigned_bucket,
        c.corr_cluster_threshold,
        c.ticker,
        d.trade_date,
        d.daily_ret
    from eligible_candidates c
    inner join daily_returns_1y d
        on c.ticker = d.ticker
        and c.eval_date >= d.trade_date
        and c.eval_date - interval 365 day < d.trade_date
    where d.daily_ret is not null
),

high_corr_pairs as (
    select
        a.strategy_id,
        a.eval_date,
        a.assigned_bucket,
        a.ticker as ticker_a,
        b.ticker as ticker_b
    from candidate_daily_window a
    inner join candidate_daily_window b
        on a.strategy_id = b.strategy_id
        and a.eval_date = b.eval_date
        and a.assigned_bucket = b.assigned_bucket
        and a.trade_date = b.trade_date
        and a.ticker <= b.ticker
    group by
        a.strategy_id,
        a.eval_date,
        a.assigned_bucket,
        a.corr_cluster_threshold,
        a.ticker,
        b.ticker
    having
        a.ticker = b.ticker
        or (
            count(*) >= 60
            and corr(a.daily_ret, b.daily_ret) > a.corr_cluster_threshold
        )
),

cluster_assignment as (
    select
        strategy_id,
        eval_date,
        assigned_bucket,
        ticker_b as ticker,
        min(ticker_a) as correlation_cluster_id
    from high_corr_pairs
    group by
        strategy_id,
        eval_date,
        assigned_bucket,
        ticker_b
),

cluster_sizes as (
    select
        strategy_id,
        eval_date,
        assigned_bucket,
        correlation_cluster_id,
        count(*) as cluster_member_count
    from cluster_assignment
    group by
        strategy_id,
        eval_date,
        assigned_bucket,
        correlation_cluster_id
),

macro_at_eval as (
    select
        trade_date as eval_date,
        regime_status
    from {{ ref('stg_macro_regimes') }}
),

raw_risk_units as (
    select
        c.strategy_id,
        c.eval_date,
        c.ticker,
        c.sector,
        c.assigned_bucket,
        c.is_active,
        c.strategy_portfolio_weight,
        c.rebalance_band_pct,
        c.sector_cap_excess_pct,
        coalesce(cl.correlation_cluster_id, c.ticker) as correlation_cluster_id,
        coalesce(cs.cluster_member_count, 1) as cluster_member_count,
        case
            when coalesce(m.regime_status, 'BULL_STABLE') = 'BEAR_CONTRACTION'
                 and c.route_to_spy_in_bear = true
            then 0.0
            else c.bucket_target_share
        end as active_bucket_share,
        case
            when c.weighting_scheme = 'EQUAL_WEIGHT'
            then 1.0 / cast(coalesce(cs.cluster_member_count, 1) as double)
            else (1.0 / c.downside_vol) / cast(coalesce(cs.cluster_member_count, 1) as double)
        end as cluster_adj_inv_vol
    from eligible_candidates c
    left join cluster_assignment cl
        on c.strategy_id = cl.strategy_id
        and c.eval_date = cl.eval_date
        and c.assigned_bucket = cl.assigned_bucket
        and c.ticker = cl.ticker
    left join cluster_sizes cs
        on cl.strategy_id = cs.strategy_id
        and cl.eval_date = cs.eval_date
        and cl.assigned_bucket = cs.assigned_bucket
        and cl.correlation_cluster_id = cs.correlation_cluster_id
    left join macro_at_eval m
        on c.eval_date = m.eval_date
),

bucket_normalized as (
    select
        *,
        coalesce(
            active_bucket_share
            * (
                cluster_adj_inv_vol
                / nullif(
                    sum(cluster_adj_inv_vol) over (
                        partition by strategy_id, eval_date, assigned_bucket
                    ),
                    0.0
                )
            ),
            0.0
        ) as uncapped_weight
    from raw_risk_units
),

benchmark_sector_weights as (
    select
        trade_date as eval_date,
        coalesce(sector, 'Unclassified') as sector,
        cast(count(*) as double) / nullif(
            cast(sum(count(*)) over (partition by trade_date) as double),
            0.0
        ) as sp500_sector_weight
    from {{ ref('stg_sp500_historical_membership') }}
    where is_in_index = true
    group by trade_date, coalesce(sector, 'Unclassified')
),

sector_totals as (
    select
        b.strategy_id,
        b.eval_date,
        b.sector,
        b.sector_cap_excess_pct,
        sum(b.uncapped_weight) as model_sector_weight,
        coalesce(s.sp500_sector_weight, 0.10) + b.sector_cap_excess_pct as max_sector_cap
    from bucket_normalized b
    left join benchmark_sector_weights s
        on b.eval_date = s.eval_date
        and b.sector = s.sector
    group by
        b.strategy_id,
        b.eval_date,
        b.sector,
        b.sector_cap_excess_pct,
        s.sp500_sector_weight
),

sector_capped_equities as (
    select
        b.strategy_id,
        b.eval_date,
        b.ticker,
        b.sector,
        b.assigned_bucket,
        b.is_active,
        b.strategy_portfolio_weight,
        b.rebalance_band_pct,
        b.correlation_cluster_id,
        b.uncapped_weight,
        case
            when st.model_sector_weight > st.max_sector_cap and st.model_sector_weight > 0.0
            then b.uncapped_weight * (st.max_sector_cap / st.model_sector_weight)
            else b.uncapped_weight
        end as capped_equity_weight
    from bucket_normalized b
    inner join sector_totals st
        on b.strategy_id = st.strategy_id
        and b.eval_date = st.eval_date
        and b.sector = st.sector
    where b.uncapped_weight > 0.0
),

residual_safe_harbor as (
    select
        e.strategy_id,
        e.eval_date,
        'SPY' as ticker,
        'Broad Market ETF' as sector,
        'SAFE_HARBOR_SPY' as assigned_bucket,
        e.is_active,
        e.strategy_portfolio_weight,
        e.rebalance_band_pct,
        'SPY_CLUSTER' as correlation_cluster_id,
        greatest(0.0, 1.0 - coalesce(sum(c.uncapped_weight), 0.0)) as uncapped_weight,
        greatest(0.0, 1.0 - coalesce(sum(c.capped_equity_weight), 0.0)) as capped_equity_weight
    from eval_dates_per_strategy e
    left join sector_capped_equities c
        on e.strategy_id = c.strategy_id
        and e.eval_date = c.eval_date
    group by
        e.strategy_id,
        e.eval_date,
        e.is_active,
        e.strategy_portfolio_weight,
        e.rebalance_band_pct
    having greatest(0.0, 1.0 - coalesce(sum(c.capped_equity_weight), 0.0)) > 0.000001
),

combined_allocations as (
    select * from sector_capped_equities
    union all
    select * from residual_safe_harbor
),

normalized_final as (
    select
        strategy_id,
        eval_date,
        ticker,
        sector,
        assigned_bucket,
        is_active,
        strategy_portfolio_weight,
        rebalance_band_pct,
        correlation_cluster_id,
        uncapped_weight,
        capped_equity_weight / nullif(
            sum(capped_equity_weight) over (partition by strategy_id, eval_date),
            0.0
        ) as strategy_target_weight
    from combined_allocations
)

select
    strategy_id,
    eval_date,
    ticker,
    sector,
    assigned_bucket,
    is_active,
    correlation_cluster_id,
    round(cast(uncapped_weight as decimal(8, 6)), 6) as raw_risk_parity_weight,
    round(cast(strategy_target_weight as decimal(8, 6)), 6) as strategy_target_weight,
    round(cast(strategy_target_weight as decimal(8, 6)), 6) as final_target_weight,
    round(
        cast(strategy_target_weight * strategy_portfolio_weight as decimal(8, 6)),
        6
    ) as portfolio_target_weight,
    round(
        cast(strategy_target_weight * (1.0 - rebalance_band_pct) as decimal(8, 6)),
        6
    ) as lower_tolerance_bound,
    round(
        cast(strategy_target_weight * (1.0 + rebalance_band_pct) as decimal(8, 6)),
        6
    ) as upper_tolerance_bound
from normalized_final
order by
    strategy_id asc,
    eval_date asc,
    final_target_weight desc,
    ticker asc
