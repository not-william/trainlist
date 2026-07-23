import os
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

ON_TIME_MINUTES = float(os.environ.get("ON_TIME_MINUTES", "5"))
MIN_ARRIVALS = 5
WINDOWS = {"today": 0, "week": 6, "month": 29}
LONDON = ZoneInfo("Europe/London")


def london_today() -> date:
    # Windows roll over at UK midnight, not UTC midnight.
    return datetime.now(LONDON).date()


def window_start(window: str, today: date | None = None) -> date:
    if today is None:
        today = london_today()
    return today - timedelta(days=WINDOWS[window])


def punctuality(conn, window: str, today: date | None = None) -> dict[int, dict]:
    start = window_start(window, today)
    rows = conn.execute(
        """SELECT listing_id,
                  COUNT(*) AS total,
                  SUM(CASE WHEN cancelled = 0 AND delay_min <= ? THEN 1 ELSE 0 END)
                      AS on_time
           FROM arrivals
           WHERE service_date >= ?
           GROUP BY listing_id""",
        (ON_TIME_MINUTES, start.isoformat()),
    ).fetchall()
    out = {}
    for r in rows:
        pct = (
            round(100 * r["on_time"] / r["total"])
            if r["total"] >= MIN_ARRIVALS
            else None
        )
        out[r["listing_id"]] = {"pct": pct, "total": r["total"]}
    return out
