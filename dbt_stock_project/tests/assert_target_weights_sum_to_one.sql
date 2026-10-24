-- Fails if any evaluation month's target portfolio allocations do not sum to 1.0 (+/- 0.001 rounding tolerance)
select
    eval_date,
    sum(final_target_weight) as total_weight
from {{ ref('fct_portfolio_target_allocations') }}
group by eval_date
having abs(sum(final_target_weight) - 1.0) > 0.001