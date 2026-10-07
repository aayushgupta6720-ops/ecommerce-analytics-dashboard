-- Shared filter, prepended to every query by retail/sql.py.
-- $start / $end_excl: date window (end exclusive); $countries: list of country names, empty = all.
-- NOT MATERIALIZED: queries reference f several times, and DuckDB would otherwise hold the whole
-- filtered table in memory; this way each reference streams from the Parquet file instead.
f AS NOT MATERIALIZED (
    SELECT *
    FROM transactions
    WHERE invoice_date >= $start
      AND invoice_date < $end_excl
      AND (len($countries) = 0 OR list_contains($countries, country))
)
