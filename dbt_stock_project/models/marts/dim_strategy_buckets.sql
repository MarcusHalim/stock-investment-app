with raw_buckets as (
    select
        upper(trim(strategy_id)) as strategy_id,
        upper(trim(bucket_id)) as bucket_id,
        trim(bucket_name) as bucket_name,
        cast(priority_rank as integer) as priority_rank,
        cast(bucket_target_share as double) as raw_bucket_share,
        cast(min_tenure_years as decimal(6, 2)) as min_tenure_years,
        cast(min_sortino_entry as decimal(10, 4)) as min_sortino_entry,
        cast(min_sortino_exit as decimal(10, 4)) as min_sortino_exit,
        cast(max_down_capture_entry as decimal(10, 4)) as max_down_capture_entry,
        cast(max_down_capture_exit as decimal(10, 4)) as max_down_capture_exit,
        cast(min_up_capture_entry as decimal(10, 4)) as min_up_capture_entry,
        cast(min_up_capture_exit as decimal(10, 4)) as min_up_capture_exit,
        cast(min_info_ratio_entry as decimal(10, 4)) as min_info_ratio_entry,
        cast(min_info_ratio_exit as decimal(10, 4)) as min_info_ratio_exit,
        cast(max_drawdown_entry as decimal(10, 6)) as max_drawdown_entry,
        cast(max_drawdown_exit as decimal(10, 6)) as max_drawdown_exit,
        upper(trim(weighting_scheme)) as weighting_scheme,
        cast(route_to_spy_in_bear as boolean) as route_to_spy_in_bear,
        _updated_at
    from {{ source('bronze', 'bronze_strategy_buckets') }}
    where
        strategy_id is not null
        and bucket_id is not null
),

strategy_bucket_totals as (
    select
        strategy_id,
        sum(raw_bucket_share) as total_bucket_share
    from raw_buckets
    group by strategy_id
)

select
    b.strategy_id,
    b.bucket_id,
    b.bucket_name,
    b.priority_rank,
    round(
        cast(
            case
                when coalesce(t.total_bucket_share, 0.0) > 0.0
                then b.raw_bucket_share / t.total_bucket_share
                else 0.0
            end as decimal(8, 6)
        ),
        6
    ) as bucket_target_share,
    b.min_tenure_years,
    b.min_sortino_entry,
    b.min_sortino_exit,
    b.max_down_capture_entry,
    b.max_down_capture_exit,
    b.min_up_capture_entry,
    b.min_up_capture_exit,
    b.min_info_ratio_entry,
    b.min_info_ratio_exit,
    b.max_drawdown_entry,
    b.max_drawdown_exit,
    b.weighting_scheme,
    b.route_to_spy_in_bear,
    b._updated_at
from raw_buckets b
inner join strategy_bucket_totals t
    on b.strategy_id = t.strategy_id
order by b.strategy_id, b.priority_rank
