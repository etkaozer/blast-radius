-- Staging layer for orders.
select
    id                                as order_id,
    customer_id,
    cast(order_date as date)          as order_date,
    amount_usd,
    status
from {{ ref('raw_orders') }}
