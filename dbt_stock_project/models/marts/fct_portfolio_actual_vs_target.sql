with latest_market_state as (
    select
        trade_date as as_of_date,
        cast(spy_close as double) as spy_close,
        regime_status as active_macro_regime
    from {{ ref('stg_macro_regimes') }}
    qualify row_number() over (order by trade_date desc) = 1
),

latest_eval_month as (
    select
        max(eval_date) as latest_eval_date
    from {{ ref('fct_portfolio_target_allocations') }}
),

latest_targets as (
    select
        t.ticker,
        t.assigned_bucket,
        cast(t.final_target_weight as double) as target_weight,
        cast(t.lower_tolerance_bound as double) as lower_bound,
        cast(t.upper_tolerance_bound as double) as upper_bound
    from {{ ref('fct_portfolio_target_allocations') }} t
    inner join latest_eval_month m
        on t.eval_date = m.latest_eval_date
),

current_holdings as (
    select
        ticker,
        sum(cast(signed_shares as double)) as current_shares_held
    from {{ ref('stg_portfolio_trade_log') }}
    group by ticker
    having sum(cast(signed_shares as double)) > 0
),

latest_equity_prices as (
    select
        ticker,
        cast(raw_close_price as double) as latest_close_price
    from {{ ref('stg_stock_prices_daily') }}
    qualify row_number() over (partition by ticker order by trade_date desc) = 1
),

all_Latest_prices as (
    select ticker, latest_close_price from latest_equity_prices
    union all
    select
        'SPY' as ticker,
        spy_close as latest_close_price
    from latest_market_state
    where not exists (select 1 from latest_equity_prices where ticker = 'SPY')
),

full_universe as (
    select ticker from current_holdings
    union
    select ticker from latest_targets
),

valued_positions as (
    select
        ms.as_of_date,
        u.ticker,
        coalesce(h.current_shares_held, 0.0) as current_shares_held,
        coalesce(p.latest_close_price, 0.0) as latest_close_price,
        coalesce(h.current_shares_held, 0.0) * coalesce(p.latest_close_price, 0.0) as position_market_value,
        coalesce(t.target_weight, 0.0) as target_portfolio_weight,
        coalesce(t.lower_bound, 0.0) as lower_bound,
        coalesce(t.upper_bound, 0.0) as upper_bound,
        ms.active_macro_regime
    from full_universe u
    cross join latest_market_state ms
    left join current_holdings h
        on u.ticker = h.ticker
    left join all_latest_prices p
        on u.ticker = p.ticker
    left join latest_targets t
        on u.ticker = t.ticker
),

portfolio_weights as (
    select
        *,
        coalesce(
            position_market_value / nullif(sum(position_market_value) over (), 0.0),
            0.0
        ) as actual_portfolio_weight
    from valued_positions
),

drift_evaluation as (
    select
        as_of_date,
        ticker,
        current_shares_held,
        latest_close_price,
        position_market_value,
        actual_portfolio_weight,
        target_portfolio_weight,
        case
            when target_portfolio_weight > 0
            then (actual_portfolio_weight - target_portfolio_weight) / target_portfolio_weight
            when actual_portfolio_weight > 0
            then 1.0
            else 0.0
        end as relative_weight_drift,
        case
            when target_portfolio_weight = 0 and actual_portfolio_weight > 0 then true
            when target_portfolio_weight > 0 and actual_portfolio_weight = 0 then true
            when actual_portfolio_weight < lower_bound or actual_portfolio_weight > upper_bound then true
            else false
        end as is_tolerance_breached,
        active_macro_regime,
        target_portfolio_weight - actual_portfolio_weight as suggested_trade_weight_diff
    from portfolio_weights
)

select
    as_of_date,
    ticker,
    round(cast(current_shares_held as decimal(12, 4)), 4) as current_shares_held,
    round(cast(latest_close_price as decimal(12, 4)), 4) as latest_close_price,
    round(cast(position_market_value as decimal(14, 4)), 4) as position_market_value,
    round(cast(actual_portfolio_weight as decimal(8, 6)), 6) as actual_portfolio_weight,
    round(cast(target_portfolio_weight as decimal(8, 6)), 6) as target_portfolio_weight,
    round(cast(relative_weight_drift as decimal(8, 6)), 6) as relative_weight_drift,
    is_tolerance_breached,
    active_macro_regime,
    case
        when not is_tolerance_breached then 'HOLD'
        when active_macro_regime = 'HIGH_VOLATILITY' then 'PAUSED_BY_MACRO_FILTER'
        when active_macro_regime = 'BEAR_CONTRACTION'
             and suggested_trade_weight_diff > 0
             and ticker != 'SPY'
        then 'PAUSED_BY_MACRO_FILTER'
        when suggested_trade_weight_diff > 0 then 'BUY'
        when suggested_trade_weight_diff < 0 then 'SELL'
        else 'HOLD'
    end as recommended_action,
    round(cast(suggested_trade_weight_diff as decimal(8, 6)), 6) as suggested_trade_weight_diff
from drift_evaluation
order by abs(suggested_trade_weight_diff) desc, ticker