with active_bucket_stocks as (
    select
        b.eval_date,
        b.ticker,
        b.sector,
        b.current_bucket as assigned_bucket,
        cast(r.downside_semi_deviation as double) as downside_vol
    from {{ ref('dim_stock_bucket_classification') }} b
    inner join {{ ref('fct_stock_risk_metrics') }} r
        on b.eval_date = r.eval_date
       and b.ticker = r.ticker
       and r.lookback_window_years = 5
    where b.current_bucket in ('BUCKET_A_AGGRESSIVE', 'BUCKET_B_STEADY')
      and r.downside_semi_deviation > 0
),

-- Benchmark sector weights across all active S&P 500 constituents at each month-end
benchmark_sectors as (
    select
        cast(date_trunc('month', m.trade_date) as date) as eval_date,
        m.sector,
        count(distinct m.ticker) * 1.0
            / nullif(sum(count(distinct m.ticker)) over (partition by date_trunc('month', m.trade_date)), 0)
            as benchmark_sector_weight
    from {{ ref('stg_sp500_historical_membership') }} m
    inner join {{ ref('stg_trading_calendar') }} c
        on m.trade_date = c.calendar_date
       and c.is_month_end_trading_day = true
    where m.is_in_index = true
    group by date_trunc('month', m.trade_date), m.sector
),

-- Month-end macro regime status to check if BEAR_CONTRACTION halts BUCKET_A_AGGRESSIVE
month_end_macro as (
    select
        cast(date_trunc('month', r.trade_date) as date) as eval_date,
        r.regime_status
    from {{ ref('stg_macro_regimes') }} r
    inner join {{ ref('stg_trading_calendar') }} c
        on r.trade_date = c.calendar_date
       and c.is_month_end_trading_day = true
),

-- 1-year daily returns leading up to each eval_date for pairwise correlation clustering
candidate_daily_returns as (
    select
        a.eval_date,
        a.assigned_bucket,
        a.ticker,
        p.trade_date,
        cast(p.daily_return as double) as daily_ret
    from active_bucket_stocks a
    inner join {{ ref('stg_stock_prices_daily') }} p
        on a.ticker = p.ticker
       and p.trade_date <= (a.eval_date + interval '31 days')
       and p.trade_date > (a.eval_date - interval '335 days')
    where p.daily_return is not null
),

-- Compute pairwise Pearson correlation (rho) within the same eval_date and bucket
pairwise_corr as (
    select
        r1.eval_date,
        r1.assigned_bucket,
        r1.ticker as ticker_1,
        r2.ticker as ticker_2,
        count(*) as shared_days,
        (
            avg(r1.daily_ret * r2.daily_ret) - (avg(r1.daily_ret) * avg(r2.daily_ret))
        ) / nullif(
            stddev_pop(r1.daily_ret) * stddev_pop(r2.daily_ret),
            0.0
        ) as corr_1y
    from candidate_daily_returns r1
    inner join candidate_daily_returns r2
        on r1.eval_date = r2.eval_date
       and r1.assigned_bucket = r2.assigned_bucket
       and r1.trade_date = r2.trade_date
       and r1.ticker <= r2.ticker
    group by
        r1.eval_date,
        r1.assigned_bucket,
        r1.ticker,
        r2.ticker
),

-- Group correlated stocks (rho > 0.75) into shared risk units anchored by min(ticker)
high_corr_links as (
    select
        eval_date,
        assigned_bucket,
        ticker_1 as ticker,
        ticker_2 as partner_ticker
    from pairwise_corr
    where shared_days >= 120 and corr_1y > 0.75
    union all
    select
        eval_date,
        assigned_bucket,
        ticker_2 as ticker,
        ticker_1 as partner_ticker
    from pairwise_corr
    where shared_days >= 120 and corr_1y > 0.75 and ticker_1 != ticker_2
),

clusters as (
    select
        eval_date,
        assigned_bucket,
        ticker,
        'CLUSTER_' || min(partner_ticker) as correlation_cluster_id
    from high_corr_links
    group by eval_date, assigned_bucket, ticker
),

cluster_sizes as (
    select
        a.eval_date,
        a.ticker,
        a.sector,
        a.assigned_bucket,
        1.0 / a.downside_vol as inverse_downside_vol,
        coalesce(c.correlation_cluster_id, 'CLUSTER_' || a.ticker) as correlation_cluster_id,
        count(*) over (
            partition by a.eval_date, coalesce(c.correlation_cluster_id, 'CLUSTER_' || a.ticker)
        ) as cluster_member_count
    from active_bucket_stocks a
    left join clusters c
        on a.eval_date = c.eval_date
       and a.ticker = c.ticker
),

-- Split inverse volatility across correlated cluster members and normalize to 100%
risk_parity_raw as (
    select
        cs.eval_date,
        cs.ticker,
        cs.sector,
        cs.assigned_bucket,
        cs.inverse_downside_vol,
        cs.correlation_cluster_id,
        (cs.inverse_downside_vol / cs.cluster_member_count)
            / nullif(
                sum(cs.inverse_downside_vol / cs.cluster_member_count) over (partition by cs.eval_date),
                0.0
            ) as raw_risk_parity_weight,
        coalesce(bs.benchmark_sector_weight, 0.10) as benchmark_sector_weight,
        coalesce(mm.regime_status, 'BULL_STABLE') as regime_status
    from cluster_sizes cs
    left join benchmark_sectors bs
        on cs.eval_date = bs.eval_date
       and cs.sector = bs.sector
    left join month_end_macro mm
        on cs.eval_date = mm.eval_date
),

-- Enforce sector cap (+5% over benchmark sector weight) and macro regime override
sector_totals as (
    select
        *,
        sum(
            case
                when regime_status = 'BEAR_CONTRACTION' and assigned_bucket = 'BUCKET_A_AGGRESSIVE'
                then 0.0
                else raw_risk_parity_weight
            end
        ) over (partition by eval_date, sector) as raw_sector_weight
    from risk_parity_raw
),

capped_equities as (
    select
        eval_date,
        ticker,
        assigned_bucket,
        inverse_downside_vol,
        correlation_cluster_id,
        raw_risk_parity_weight,
        benchmark_sector_weight,
        case
            -- Halt Aggressive Bucket during BEAR_CONTRACTION (Breadth < 30%)
            when regime_status = 'BEAR_CONTRACTION' and assigned_bucket = 'BUCKET_A_AGGRESSIVE'
            then 0.0
            -- Scale down proportionally if sector exceeds (benchmark_sector_weight + 5%)
            when raw_sector_weight > (benchmark_sector_weight + 0.05) and raw_sector_weight > 0
            then raw_risk_parity_weight * ((benchmark_sector_weight + 0.05) / raw_sector_weight)
            else raw_risk_parity_weight
        end as final_target_weight
    from sector_totals
),

-- Route any residual unallocated weight (from sector caps or macro halts) into SPY Safe Harbor
spy_safe_harbor_fallback as (
    select
        eval_date,
        'SPY' as ticker,
        'SAFE_HARBOR_SPY' as assigned_bucket,
        0.0 as inverse_downside_vol,
        'CLUSTER_SPY_BENCHMARK' as correlation_cluster_id,
        0.0 as raw_risk_parity_weight,
        1.0 as benchmark_sector_weight,
        greatest(0.0, 1.0 - sum(final_target_weight)) as final_target_weight
    from capped_equities
    group by eval_date
    having (1.0 - sum(final_target_weight)) > 1e-6
),

combined_allocations as (
    select * from capped_equities where final_target_weight > 0
    union all
    select * from spy_safe_harbor_fallback
)

select
    eval_date,
    ticker,
    assigned_bucket,
    round(cast(inverse_downside_vol as decimal(12, 4)), 4) as inverse_downside_vol,
    correlation_cluster_id,
    round(cast(raw_risk_parity_weight as decimal(8, 6)), 6) as raw_risk_parity_weight,
    round(cast(benchmark_sector_weight as decimal(8, 6)), 6) as benchmark_sector_weight,
    round(cast(final_target_weight as decimal(8, 6)), 6) as final_target_weight,
    round(cast(final_target_weight * 0.80 as decimal(8, 6)), 6) as lower_tolerance_bound,
    round(cast(final_target_weight * 1.20 as decimal(8, 6)), 6) as upper_tolerance_bound
from combined_allocations
order by eval_date, final_target_weight desc, ticker