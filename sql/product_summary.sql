-- Per product: net revenue and units (cancellations subtracted); orders and customers from sales.
-- Only products with at least one sale in the window are listed.
SELECT
    stock_code,
    any_value(description)                                                      AS description,
    sum(revenue)                                                                AS revenue,
    sum(quantity)                                                               AS units,
    count(DISTINCT invoice) FILTER (WHERE NOT is_cancellation)                  AS orders,
    count(DISTINCT customer_id) FILTER (WHERE NOT is_cancellation)              AS customers
FROM f
WHERE is_product
GROUP BY stock_code
HAVING count(*) FILTER (WHERE NOT is_cancellation) > 0
ORDER BY revenue DESC, stock_code
