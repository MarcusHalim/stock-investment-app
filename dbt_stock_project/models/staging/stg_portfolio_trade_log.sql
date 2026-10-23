with raw_executions as (
    select
        execution_id,
        trade_date,
        upper(trim(ticker)) as ticker,
        upper(trim(action)) as action,
        cast(shares_executed as decimal(12, 4)) as shares_executed,
        cast(fill_price as decimal(12, 4)) as fill_price
    from {{ source('bronze', 'bronze_manual_executions') }}
    where execution_id is not null
)

select
    execution_id,
    trade_date,
    ticker,
    action,
    cast(
        case
            when action = 'BUY' then shares_executed
            when action = 'SELL' then -1.0 * shares_executed
            else 0.0
        end as decimal(12, 4)
    ) as signed_shares,
    fill_price,
    round(
        cast(
            (
                case
                    when action = 'BUY' then shares_executed
                    when action = 'SELL' then -1.0 * shares_executed
                    else 0.0
                end
            ) * fill_price as decimal(14, 4)
        ),
        4
    ) as net_cash_impact
from raw_executions