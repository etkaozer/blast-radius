-- Executive summary. Aggregates ltv_usd, so it breaks one hop further out than
-- the dashboards do. Present so the demo has a downstream aggregation and not
-- only pass-through selects.
select
    acquisition_channel,
    count(*)            as customer_count,
    sum(ltv_usd)        as total_ltv,
    avg(ltv_usd)        as avg_ltv
from {{ ref('customer_ltv') }}
group by acquisition_channel
