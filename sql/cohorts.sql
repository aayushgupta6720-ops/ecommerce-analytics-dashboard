-- Monthly acquisition cohorts x months since first order.
-- A customer's cohort is the month of their first purchase in the FULL dataset (not the filtered
-- window), so narrowing the dates never relabels a returning customer as new. Only cohorts acquired
-- inside the window are shown; cohort size = customers active in period 0. Revenue is net.
WITH acquired AS (
    SELECT customer_id, date_trunc('month', min(invoice_date)) AS cohort
    FROM transactions
    WHERE is_product AND NOT is_cancellation AND customer_id IS NOT NULL
    GROUP BY customer_id
),
lines AS (
    SELECT f.customer_id, f.revenue, f.is_cancellation,
           date_trunc('month', f.invoice_date) AS month, a.cohort
    FROM f
    JOIN acquired a USING (customer_id)
    WHERE f.is_product
),
window_start AS (
    SELECT min(month) AS first_month FROM lines WHERE NOT is_cancellation
),
cells AS (
    SELECT
        cohort,
        date_diff('month', cohort, month)                                       AS period,
        count(DISTINCT customer_id) FILTER (WHERE NOT is_cancellation)          AS customers,
        sum(revenue)                                                            AS revenue
    FROM lines, window_start
    WHERE cohort >= first_month
    GROUP BY cohort, period
    HAVING count(*) FILTER (WHERE NOT is_cancellation) > 0
),
sizes AS (
    SELECT cohort, customers AS cohort_size FROM cells WHERE period = 0
)
SELECT c.cohort, c.period, c.customers, c.revenue, s.cohort_size,
       c.customers / s.cohort_size AS retention
FROM cells c
JOIN sizes s USING (cohort)
ORDER BY c.cohort, c.period
