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

active_strategy_targets as (
    select
        t.ticker,
        t.strategy_id,
        t.assigned_bucket,
        cast(t.portfolio_target_weight as double) as portfolio_target_weight,
        cast(t.lower_tolerance_bound as double)
        * (
            cast(t.portfolio_target_weight as double)
            / nullif(cast(t.strategy_target_weight as double), 0.0)
        ) as weighted_lower_bound,
        cast(t.upper_tolerance_bound as double)
        * (
            cast(t.portfolio_target_weight as double)
            / nullif(cast(t.strategy_target_weight as double), 0.0)
        ) as weighted_upper_bound
    from {{ ref('fct_portfolio_target_allocations') }} t
    inner join latest_eval_month m
        on t.eval_date = m.latest_eval_date
    where
        t.is_active = true
        and t.portfolio_target_weight > 0.0
),

netted_targets as (
    select
        ticker,
        sum(portfolio_target_weight) as target_weight,
        sum(coalesce(weighted_lower_bound, portfolio_target_weight * 0.80)) as lower_bound,
        sum(coalesce(weighted_upper_bound, portfolio_target_weight * 1.20)) as upper_bound,
        string_agg(
            strategy_id || ':' || cast(round(portfolio_target_weight * 100.0, 2) as varchar) || '%',
            ' | ' order by strategy_id
        ) as strategy_breakdown
    from active_strategy_targets
    group by ticker
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

all_latest_prices as (
    select
        ticker,
        latest_close_price
    from latest_equity_prices
    union all
    select
        'SPY' as ticker,
        spy_close as latest_close_price
    from latest_market_state
    where not exists (
        select 1
        from latest_equity_prices
        where ticker = 'SPY'
    )
),

full_universe as (
    select ticker from current_holdings
    union
    select ticker from netted_targets
),

valued_positions as (
    select
        ms.as_of_date,
        u.ticker,
        coalesce(t.strategy_breakdown, 'LEGACY_HOLDING') as strategy_breakdown,
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
    left join netted_targets t
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
        strategy_breakdown,
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
        coalesce(
            (target_portfolio_weight = 0 and actual_portfolio_weight > 0)
            or (target_portfolio_weight > 0 and actual_portfolio_weight = 0)
            or actual_portfolio_weight < lower_bound
            or actual_portfolio_weight > upper_bound,
            false
        ) as is_tolerance_breached,
        active_macro_regime,
        target_portfolio_weight - actual_portfolio_weight as suggested_trade_weight_diff
    from portfolio_weights
)

select
    as_of_date,
    ticker,
    strategy_breakdown,
    round(cast(current_shares_held as decimal(12, 4)), 4) as current_shares_held,
    round(cast(latest_close_price as decimal(12, 4)), 4) as latest_close_price,
    round(cast(position_market_value as decimal(14, 4)), 4) as position_market_value,
    round(cast(actual_portfolio_weight as decimal(9, 6)), 6) as actual_portfolio_weight,
    round(cast(target_portfolio_weight as decimal(9, 6)), 6) as target_portfolio_weight,
    round(cast(relative_weight_drift as decimal(12, 6)), 6) as relative_weight_drift,
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
    round(cast(suggested_trade_weight_diff as decimal(9, 6)), 6) as suggested_trade_weight_diff
from drift_evaluation
order by abs(suggested_trade_weight_diff) desc, ticker asc
