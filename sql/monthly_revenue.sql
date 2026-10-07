-- Net revenue, orders and customers per calendar month.
-- A month is "partial" when the filter window covers only some of its days.
WITH months AS (
    SELECT
        date_trunc('month', invoice_date)                                       AS month,
        sum(revenue)                                                            AS revenue,
        count(DISTINCT invoice) FILTER (WHERE NOT is_cancellation)              AS orders,
        count(DISTINCT customer_id) FILTER (WHERE NOT is_cancellation)          AS customers
    FROM f
    WHERE is_product
    GROUP BY 1
)
SELECT
    month,
    revenue,
    orders,
    customers,
    date_diff('day', greatest(month::DATE, $start::DATE), least(last_day(month), ($end_excl - INTERVAL 1 DAY)::DATE)) + 1
        < day(last_day(month))                                                  AS partial
FROM months
WHERE orders > 0 OR revenue <> 0
ORDER BY month
