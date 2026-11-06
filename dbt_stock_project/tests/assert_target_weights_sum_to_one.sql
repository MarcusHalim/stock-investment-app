with per_strategy_check as (
    select
        'STRATEGY_SUM' as check_type,
        strategy_id,
        eval_date,
        sum(strategy_target_weight) as total_weight
    from {{ ref('fct_portfolio_target_allocations') }}
    group by strategy_id, eval_date
    having abs(sum(strategy_target_weight) - 1.0) > 0.001
),

blended_portfolio_check as (
    select
        'BLENDED_PORTFOLIO_SUM' as check_type,
        'ALL_ACTIVE' as strategy_id,
        eval_date,
        sum(portfolio_target_weight) as total_weight
    from {{ ref('fct_portfolio_target_allocations') }}
    where is_active = true
    group by eval_date
    having abs(sum(portfolio_target_weight) - 1.0) > 0.001
)

select * from per_strategy_check
union all
select * from blended_portfolio_check
