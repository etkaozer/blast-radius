-- Customer dimension.
-- Reads stg_customers.email and stg_customers.signup_channel directly, which is
-- what makes it the 1-hop consumer in both demo scenarios.
-- customer_lifetime_value is the column removed in the 02_removal_contract demo.
with customers as (
    select * from {{ ref('stg_customers') }}
),

orders as (
    select
        customer_id,
        sum(amount_usd)               as lifetime_amount_usd,
        count(*)                      as order_count,
        max(order_date)               as most_recent_order_date
    from {{ ref('stg_orders') }}
    where status = 'completed'
    group by customer_id
)

select
    customers.customer_id,
    customers.first_name,
    customers.last_name,
    customers.email,
    customers.signup_channel,
    customers.signup_date,
    coalesce(orders.order_count, 0)          as order_count,
    orders.most_recent_order_date,
    coalesce(orders.lifetime_amount_usd, 0)  as customer_lifetime_value
from customers
left join orders on customers.customer_id = orders.customer_id
