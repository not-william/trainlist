from datetime import date, datetime
from zoneinfo import ZoneInfo

from trainlist import stats

TODAY = date(2026, 7, 23)


def add_arrival(conn, rid, listing_id=1, service_date="2026-07-23",
                delay=0.0, cancelled=0):
    conn.execute(
        """INSERT INTO arrivals (rid, listing_id, service_date, sched_arr,
                                 actual_arr, delay_min, cancelled)
           VALUES (?, ?, ?, '2026-07-23T13:20:00', '2026-07-23T13:20:00', ?, ?)""",
        (rid, listing_id, service_date, delay, cancelled),
    )
    conn.commit()


def test_window_start():
    assert stats.window_start("today", TODAY) == date(2026, 7, 23)
    assert stats.window_start("week", TODAY) == date(2026, 7, 17)
    assert stats.window_start("month", TODAY) == date(2026, 6, 24)


def test_london_today_uses_uk_calendar():
    assert stats.london_today() == datetime.now(ZoneInfo("Europe/London")).date()


def test_percentage_counts_late_and_cancelled(seeded):
    for i in range(8):
        add_arrival(seeded, f"ok{i}")
    add_arrival(seeded, "late", delay=7.0)
    add_arrival(seeded, "canc", cancelled=1)
    out = stats.punctuality(seeded, "today", today=TODAY)
    assert out[1] == {"pct": 80, "total": 10}


def test_exactly_threshold_is_on_time(seeded):
    for i in range(4):
        add_arrival(seeded, f"ok{i}")
    add_arrival(seeded, "edge", delay=5.0)
    out = stats.punctuality(seeded, "today", today=TODAY)
    assert out[1]["pct"] == 100


def test_below_min_arrivals_gives_none(seeded):
    for i in range(4):
        add_arrival(seeded, f"r{i}")
    out = stats.punctuality(seeded, "today", today=TODAY)
    assert out[1] == {"pct": None, "total": 4}


def test_window_boundaries(seeded):
    add_arrival(seeded, "today", service_date="2026-07-23")
    add_arrival(seeded, "week-edge", service_date="2026-07-17")
    add_arrival(seeded, "week-out", service_date="2026-07-16")
    add_arrival(seeded, "month-edge", service_date="2026-06-24")
    add_arrival(seeded, "month-out", service_date="2026-06-23")
    assert stats.punctuality(seeded, "today", today=TODAY)[1]["total"] == 1
    assert stats.punctuality(seeded, "week", today=TODAY)[1]["total"] == 2
    assert stats.punctuality(seeded, "month", today=TODAY)[1]["total"] == 4
