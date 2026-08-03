-- Lifetime value mart.
-- Two hops from stg_customers, and the model that renames columns on the way
-- through: email -> customer_email, signup_channel -> acquisition_channel,
-- customer_lifetime_value -> ltv_usd. The renames are the reason the demo
-- lineage is interesting rather than a straight line.
select
    customer_id,
    email                       as customer_email,
    signup_channel              as acquisition_channel,
    customer_lifetime_value     as ltv_usd,
    order_count,
    most_recent_order_date
from {{ ref('dim_customers') }}
