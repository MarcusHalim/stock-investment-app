with month_end_membership as (
    select
        cast(date_trunc('month', m.trade_date) as date) as eval_date,
        m.ticker,
        m.sector,
        m.is_in_index,
        m.years_since_first_ingest
    from {{ ref('stg_sp500_historical_membership') }} m
    inner join {{ ref('stg_trading_calendar') }} c
        on m.trade_date = c.calendar_date
       and c.is_month_end_trading_day = true
    where m.is_in_index = true
),

risk_5y as (
    select
        eval_date,
        ticker,
        information_ratio,
        sortino_ratio,
        max_drawdown,
        up_market_capture,
        down_market_capture,
        down_capture_sma_50w,
        down_capture_sma_200w
    from {{ ref('fct_stock_risk_metrics') }}
    where lookback_window_years = 5
),

raw_eligibility as (
    select
        m.eval_date,
        m.ticker,
        m.sector,
        case
            when m.years_since_first_ingest < 5.0 then 'EXCLUDED'
            when m.years_since_first_ingest < 10.0 then 'EMERGING'
            else 'MATURE'
        end as ipo_tenure_tier,
        r.information_ratio,
        r.sortino_ratio,
        r.max_drawdown,
        r.up_market_capture,
        r.down_market_capture,
        r.down_capture_sma_50w,
        r.down_capture_sma_200w,
        -- Strict Entry Candidate Classification
        case
            when m.years_since_first_ingest >= 10.0
                 and r.sortino_ratio >= 0.35
                 and r.down_market_capture <= 0.90
                 and r.max_drawdown >= -0.50
            then 'BUCKET_B_STEADY'
            when m.years_since_first_ingest >= 5.0
                 and r.up_market_capture >= 1.15
                 and r.information_ratio >= 0.25
            then 'BUCKET_A_AGGRESSIVE'
            else 'UNASSIGNED'
        end as entry_candidate_bucket
    from month_end_membership m
    inner join risk_5y r
        on m.eval_date = r.eval_date
       and m.ticker = r.ticker
),

prior_state as (
    select
        *,
        coalesce(
            lag(entry_candidate_bucket) over (
                partition by ticker
                order by eval_date
            ),
            'UNASSIGNED'
        ) as prior_bucket
    from raw_eligibility
),

hysteresis_evaluated as (
    select
        eval_date,
        ticker,
        sector,
        ipo_tenure_tier,
        prior_bucket,
        case
            -- 1. If it qualifies on strict entry criteria, assign directly
            when entry_candidate_bucket != 'UNASSIGNED'
            then entry_candidate_bucket
            -- 2. Hysteresis retention for BUCKET_B_STEADY (buffer zone before eviction)
            when prior_bucket = 'BUCKET_B_STEADY'
                 and ipo_tenure_tier = 'MATURE'
                 and sortino_ratio >= 0.15
                 and down_market_capture <= 1.00
                 and max_drawdown >= -0.55
            then 'BUCKET_B_STEADY'
            -- 3. Hysteresis retention for BUCKET_A_AGGRESSIVE (buffer zone before eviction)
            when prior_bucket = 'BUCKET_A_AGGRESSIVE'
                 and ipo_tenure_tier in ('EMERGING', 'MATURE')
                 and up_market_capture >= 1.05
                 and information_ratio >= 0.05
            then 'BUCKET_A_AGGRESSIVE'
            else 'UNASSIGNED'
        end as current_bucket,
        -- Flag structural drift if 50w Down-Capture degrades significantly above 200w baseline
        coalesce(
            down_capture_sma_50w > (down_capture_sma_200w + 0.10),
            false
        ) as is_structural_drift_flag
    from prior_state
)

select
    eval_date,
    ticker,
    sector,
    ipo_tenure_tier,
    prior_bucket,
    current_bucket,
    is_structural_drift_flag
from hysteresis_evaluated
order by eval_date, ticker