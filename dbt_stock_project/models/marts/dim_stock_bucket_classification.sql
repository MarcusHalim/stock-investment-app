with strategies as (
    select
        strategy_id,
        lookback_window_years
    from {{ ref('dim_strategies') }}
),

buckets as (
    select
        strategy_id,
        bucket_id,
        bucket_name,
        priority_rank,
        cast(min_tenure_years as double) as min_tenure_years,
        cast(min_sortino_entry as double) as min_sortino_entry,
        cast(min_sortino_exit as double) as min_sortino_exit,
        cast(max_down_capture_entry as double) as max_down_capture_entry,
        cast(max_down_capture_exit as double) as max_down_capture_exit,
        cast(min_up_capture_entry as double) as min_up_capture_entry,
        cast(min_up_capture_exit as double) as min_up_capture_exit,
        cast(min_info_ratio_entry as double) as min_info_ratio_entry,
        cast(min_info_ratio_exit as double) as min_info_ratio_exit,
        cast(max_drawdown_entry as double) as max_drawdown_entry,
        cast(max_drawdown_exit as double) as max_drawdown_exit
    from {{ ref('dim_strategy_buckets') }}
),

risk_metrics as (
    select
        s.strategy_id,
        r.eval_date,
        r.ticker,
        cast(r.sortino_ratio as double) as sortino_ratio,
        cast(r.information_ratio as double) as information_ratio,
        cast(r.up_market_capture as double) as up_market_capture,
        cast(r.down_market_capture as double) as down_market_capture,
        cast(r.max_drawdown as double) as max_drawdown,
        cast(r.downside_semi_deviation as double) as downside_semi_deviation
    from strategies s
    inner join {{ ref('fct_stock_risk_metrics') }} r
        on s.lookback_window_years = r.lookback_window_years
),

membership_tenure as (
    select
        trade_date as eval_date,
        ticker,
        sector,
        cast(years_since_first_ingest as double) as years_tenure
    from {{ ref('stg_sp500_historical_membership') }}
    where is_in_index = true
),

joined_features as (
    select
        m.strategy_id,
        m.eval_date,
        m.ticker,
        coalesce(t.sector, 'Unclassified') as sector,
        coalesce(t.years_tenure, 0.0) as years_tenure,
        case
            when coalesce(t.years_tenure, 0.0) < 5.0 then 'EXCLUDED'
            when coalesce(t.years_tenure, 0.0) < 10.0 then 'EMERGING'
            else 'MATURE'
        end as ipo_tenure_tier,
        m.sortino_ratio,
        m.information_ratio,
        m.up_market_capture,
        m.down_market_capture,
        m.max_drawdown,
        m.downside_semi_deviation,
        avg(m.down_market_capture) over (
            partition by m.strategy_id, m.ticker
            order by m.eval_date
            rows between 11 preceding and current row
        ) as down_capture_sma_50w,
        avg(m.down_market_capture) over (
            partition by m.strategy_id, m.ticker
            order by m.eval_date
            rows between 47 preceding and current row
        ) as down_capture_sma_200w
    from risk_metrics m
    inner join membership_tenure t
        on m.eval_date = t.eval_date
        and m.ticker = t.ticker
),

bucket_candidates as (
    select
        f.strategy_id,
        f.eval_date,
        f.ticker,
        b.bucket_id,
        b.priority_rank,
        coalesce(
            f.years_tenure >= b.min_tenure_years
            and f.sortino_ratio >= b.min_sortino_entry
            and f.down_market_capture <= b.max_down_capture_entry
            and f.up_market_capture >= b.min_up_capture_entry
            and f.information_ratio >= b.min_info_ratio_entry
            and f.max_drawdown >= b.max_drawdown_entry,
            false
        ) as passes_entry,
        coalesce(
            f.years_tenure >= b.min_tenure_years
            and f.sortino_ratio >= b.min_sortino_exit
            and f.down_market_capture <= b.max_down_capture_exit
            and f.up_market_capture >= b.min_up_capture_exit
            and f.information_ratio >= b.min_info_ratio_exit
            and f.max_drawdown >= b.max_drawdown_exit,
            false
        ) as passes_exit_hold
    from joined_features f
    inner join buckets b
        on f.strategy_id = b.strategy_id
),

cand_with_lag as (
    select
        strategy_id,
        eval_date,
        ticker,
        bucket_id,
        priority_rank,
        passes_entry,
        passes_exit_hold,
        coalesce(
            lag(passes_entry) over (
                partition by strategy_id, ticker, bucket_id
                order by eval_date
            ),
            false
        ) as prev_passes_entry
    from bucket_candidates
),

qualified_buckets as (
    select
        strategy_id,
        eval_date,
        ticker,
        bucket_id,
        priority_rank
    from cand_with_lag
    where
        passes_entry = true
        or (prev_passes_entry = true and passes_exit_hold = true)
    qualify row_number() over (
        partition by strategy_id, eval_date, ticker
        order by priority_rank asc, bucket_id asc
    ) = 1
),

classified_equities as (
    select
        f.strategy_id,
        f.eval_date,
        f.ticker,
        f.sector,
        f.ipo_tenure_tier,
        f.sortino_ratio,
        f.information_ratio,
        f.up_market_capture,
        f.down_market_capture,
        f.max_drawdown,
        f.downside_semi_deviation,
        f.down_capture_sma_50w,
        f.down_capture_sma_200w,
        coalesce(q.bucket_id, 'UNASSIGNED') as current_bucket
    from joined_features f
    left join qualified_buckets q
        on f.strategy_id = q.strategy_id
        and f.eval_date = q.eval_date
        and f.ticker = q.ticker
),

with_prior_bucket as (
    select
        *,
        coalesce(
            lag(current_bucket) over (
                partition by strategy_id, ticker
                order by eval_date
            ),
            'UNASSIGNED'
        ) as prior_bucket
    from classified_equities
)

select
    strategy_id,
    eval_date,
    ticker,
    sector,
    ipo_tenure_tier,
    prior_bucket,
    current_bucket,
    round(cast(sortino_ratio as decimal(10, 4)), 4) as sortino_ratio,
    round(cast(information_ratio as decimal(10, 4)), 4) as information_ratio,
    round(cast(up_market_capture as decimal(10, 4)), 4) as up_market_capture,
    round(cast(down_market_capture as decimal(10, 4)), 4) as down_market_capture,
    round(cast(max_drawdown as decimal(10, 6)), 6) as max_drawdown,
    round(cast(downside_semi_deviation as decimal(10, 6)), 6) as downside_semi_deviation,
    coalesce(
        (down_capture_sma_50w - down_capture_sma_200w) > 0.10,
        false
    ) as is_structural_drift_flag
from with_prior_bucket
order by strategy_id, eval_date, ticker
