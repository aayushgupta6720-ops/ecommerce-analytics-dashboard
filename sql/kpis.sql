-- Headline KPIs for the filtered window.
-- Revenue is NET of cancellations: cancelled lines carry negative quantity and revenue,
-- so summing every product line nets an order that was cancelled in full to zero.
-- Orders and customers count actual sales only.
SELECT
    coalesce(sum(revenue) FILTER (WHERE NOT is_cancellation), 0)              AS gross_sales,
    coalesce(-sum(revenue) FILTER (WHERE is_cancellation), 0)                 AS cancelled_value,
    coalesce(sum(revenue), 0)                                                 AS revenue,
    count(DISTINCT invoice) FILTER (WHERE NOT is_cancellation)                AS orders,
    count(DISTINCT customer_id) FILTER (WHERE NOT is_cancellation)            AS customers,
    coalesce(sum(quantity), 0)                                                AS units,
    coalesce(sum(revenue) / nullif(count(DISTINCT invoice) FILTER (WHERE NOT is_cancellation), 0), 0) AS aov,
    coalesce(-sum(revenue) FILTER (WHERE is_cancellation)
             / nullif(sum(revenue) FILTER (WHERE NOT is_cancellation), 0), 0) AS cancel_rate
FROM f
WHERE is_product
