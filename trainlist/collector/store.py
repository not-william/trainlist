from datetime import datetime, timedelta, timezone

from .. import times
from . import matcher, parser


def handle_schedule(conn, index, schedule: parser.Schedule) -> None:
    listing_id = matcher.match(index, schedule)
    if listing_id is None:
        return
    sched_dt = times.combine(schedule.ssd, schedule.sched_arr)
    dep_dt = times.combine(schedule.ssd, schedule.origin_dep)
    if sched_dt < dep_dt:  # arrival is past midnight relative to ssd
        sched_dt += timedelta(days=1)
    conn.execute(
        """INSERT INTO schedules (rid, listing_id, ssd, dest_tpl, sched_arr)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(rid) DO UPDATE SET listing_id=excluded.listing_id,
               ssd=excluded.ssd, dest_tpl=excluded.dest_tpl,
               sched_arr=excluded.sched_arr""",
        (schedule.rid, listing_id, schedule.ssd, schedule.dest_tpl,
         sched_dt.isoformat()),
    )
    if schedule.cancelled:
        conn.execute(
            """INSERT INTO arrivals (rid, listing_id, service_date, sched_arr, cancelled)
               VALUES (?, ?, ?, ?, 1)
               ON CONFLICT(rid) DO UPDATE SET cancelled=1""",
            (schedule.rid, listing_id, schedule.ssd, sched_dt.isoformat()),
        )
    else:
        conn.execute(
            "DELETE FROM arrivals WHERE rid = ? AND cancelled = 1 AND actual_arr IS NULL",
            (schedule.rid,),
        )
    conn.commit()


def handle_arrival(conn, arrival: parser.Arrival) -> None:
    row = conn.execute(
        "SELECT * FROM schedules WHERE rid = ?", (arrival.rid,)
    ).fetchone()
    if row is None or row["dest_tpl"] != arrival.tpl:
        return
    sched_dt = datetime.fromisoformat(row["sched_arr"])
    actual_dt = times.combine(row["ssd"], arrival.at, near=sched_dt)
    delay = times.delay_minutes(sched_dt, actual_dt)
    conn.execute(
        """INSERT INTO arrivals (rid, listing_id, service_date, sched_arr,
                                 actual_arr, delay_min, cancelled)
           VALUES (?, ?, ?, ?, ?, ?, 0)
           ON CONFLICT(rid) DO UPDATE SET actual_arr=excluded.actual_arr,
               delay_min=excluded.delay_min, cancelled=0""",
        (arrival.rid, row["listing_id"], row["ssd"], row["sched_arr"],
         actual_dt.isoformat(), delay),
    )
    conn.commit()


def touch_heartbeat(conn) -> None:
    conn.execute(
        """INSERT INTO meta (key, value) VALUES ('heartbeat', ?)
           ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
        (datetime.now(timezone.utc).isoformat(),),
    )
    conn.commit()
