-- Staging layer for customers.
-- signup_channel is the column the adversarial demo removes; email is the one
-- the rename demo touches. Both are selected explicitly (never `select *`) so
-- that column-level lineage is unambiguous for the ingestion connector.
select
    id                                as customer_id,
    first_name,
    last_name,
    email,
    signup_channel,
    cast(signup_date as date)         as signup_date
from {{ ref('raw_customers') }}
