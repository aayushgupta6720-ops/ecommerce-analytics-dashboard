-- RFM scores and segments for identified customers.
--   Recency:   days from the last purchase to $snapshot (the day after the window ends)
--   Frequency: distinct sales invoices
--   Monetary:  net revenue (sales minus the customer's cancellations in the window)
-- Scores are 1-5 by rank: row_number / count, times 5, rounded up. Ties break by customer_id,
-- which matches pandas rank(method="first") on a frame sorted by customer_id.
WITH sales AS (
    SELECT * FROM f
    WHERE is_product AND NOT is_cancellation AND customer_id IS NOT NULL
),
cancels AS (
    SELECT customer_id, sum(revenue) AS cancelled
    FROM f
    WHERE is_product AND is_cancellation AND customer_id IS NOT NULL
    GROUP BY customer_id
),
home_country AS (          -- most frequent country on the customer's sales lines (ties: alphabetical)
    SELECT customer_id, country
    FROM (
        SELECT customer_id, country,
               row_number() OVER (PARTITION BY customer_id ORDER BY count(*) DESC, country) AS rn
        FROM sales
        GROUP BY customer_id, country
    )
    WHERE rn = 1
),
base AS (
    SELECT
        s.customer_id,
        h.country,
        date_diff('day', max(s.invoice_date)::DATE, $snapshot::DATE)            AS recency,
        count(DISTINCT s.invoice)                                               AS frequency,
        sum(s.revenue) + coalesce(any_value(c.cancelled), 0)                    AS monetary
    FROM sales s
    JOIN home_country h USING (customer_id)
    LEFT JOIN cancels c USING (customer_id)
    GROUP BY s.customer_id, h.country
),
scored AS (
    SELECT *,
        least(5, greatest(1, ceil(row_number() OVER (ORDER BY recency DESC, customer_id)::DOUBLE
                                  / count(*) OVER () * 5)))::INTEGER            AS r_score,
        least(5, greatest(1, ceil(row_number() OVER (ORDER BY frequency, customer_id)::DOUBLE
                                  / count(*) OVER () * 5)))::INTEGER            AS f_score,
        least(5, greatest(1, ceil(row_number() OVER (ORDER BY monetary, customer_id)::DOUBLE
                                  / count(*) OVER () * 5)))::INTEGER            AS m_score
    FROM base
)
SELECT
    customer_id, country, recency, frequency, monetary, r_score, f_score, m_score,
    CASE
        WHEN r_score <= 2 AND f_score <= 2 THEN 'Hibernating'
        WHEN r_score <= 2 AND f_score <= 4 THEN 'At Risk'
        WHEN r_score <= 2                  THEN 'Can''t Lose'
        WHEN r_score = 3  AND f_score <= 2 THEN 'About to Sleep'
        WHEN r_score = 3  AND f_score = 3  THEN 'Need Attention'
        WHEN r_score = 3                   THEN 'Loyal'
        WHEN r_score = 4  AND f_score = 1  THEN 'Promising'
        WHEN r_score = 4  AND f_score <= 3 THEN 'Potential Loyalist'
        WHEN r_score = 4                   THEN 'Loyal'
        WHEN f_score = 1                   THEN 'New Customers'
        WHEN f_score <= 3                  THEN 'Potential Loyalist'
        ELSE                                    'Champions'
    END                                                                         AS segment
FROM scored
ORDER BY customer_id
