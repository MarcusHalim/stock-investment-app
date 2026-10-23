with spy_trading_days as (
    select
        trade_date,
        true as is_spy_traded
    from {{ source('bronze', 'bronze_macro_indicators') }}
    where spy_close is not null
),

date_bounds as (
    select
        min(trade_date) as min_date,
        max(trade_date) as max_date
    from spy_trading_days
),

-- Generate a contiguous daily spine from min_date to max_date (up to 15,000 calendar days / ~41 years)
number_spine as (
    select
        row_number() over (order by 1) - 1 as day_offset
    from {{ source('bronze', 'bronze_macro_indicators') }} a
    cross join (select 1 union all select 2 union all select 3) b
),

calendar_grid as (
    select
        cast(
            date_bounds.min_date + number_spine.day_offset * interval '1 day'
            as date
        ) as calendar_date
    from date_bounds
    inner join number_spine
        on date_bounds.min_date + number_spine.day_offset * interval '1 day' <= date_bounds.max_date
),

joined_calendar as (
    select
        c.calendar_date,
        coalesce(s.is_spy_traded, false) as is_trading_day,
        max(case when s.is_spy_traded then c.calendar_date end) over (
            partition by date_trunc('month', c.calendar_date)
        ) as max_trading_date_in_month
    from calendar_grid c
    left join spy_trading_days s
        on c.calendar_date = s.trade_date
)

select
    calendar_date,
    is_trading_day,
    coalesce(
        is_trading_day and calendar_date = max_trading_date_in_month,
        false
    ) as is_month_end_trading_day,
    case
        when is_trading_day then sum(case when is_trading_day then 1 else 0 end) over (
            order by calendar_date
            rows between unbounded preceding and current row
        )
    end as trading_day_seq
from joined_calendar
order by calendar_date