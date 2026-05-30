"""Verify what's in the database — your Phase 0 'definition of done' check.

Run with:  uv run tradeos-check
Prints, per symbol, how many rows you have and the date range they span.
"""

from .db import get_connection

SUMMARY_SQL = """
SELECT symbol,
       count(*)  AS rows,
       min(date) AS first_day,
       max(date) AS last_day
FROM prices
GROUP BY symbol
ORDER BY symbol;
"""


def main() -> None:
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(SUMMARY_SQL)
        rows = cur.fetchall()

    if not rows:
        print("No data yet. Run `uv run tradeos-ingest` first.")
        return

    print(f"{'symbol':<16}{'rows':>8}{'first_day':>14}{'last_day':>14}")
    print("-" * 52)
    for symbol, n, first_day, last_day in rows:
        print(f"{symbol:<16}{n:>8}{str(first_day):>14}{str(last_day):>14}")


if __name__ == "__main__":
    main()
