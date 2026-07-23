from trainlist.collector import matcher, parser, store


def make_schedule(rid="r1", toc="GR", origin="KNGX", dest="EDINBUR",
                  origin_dep="09:00", sched_arr="13:20", ssd="2026-07-23",
                  cancelled=False):
    return parser.Schedule(
        rid=rid, ssd=ssd, toc=toc, origin_tpl=origin, origin_dep=origin_dep,
        dest_tpl=dest, sched_arr=sched_arr, cancelled=cancelled,
    )


def test_arrival_flow(seeded):
    index = matcher.load_route_index(seeded)
    store.handle_schedule(seeded, index, make_schedule())
    store.handle_arrival(seeded, parser.Arrival(rid="r1", tpl="EDINBUR", at="13:22"))
    row = seeded.execute("SELECT * FROM arrivals").fetchone()
    assert row["listing_id"] == 1
    assert row["service_date"] == "2026-07-23"
    assert row["delay_min"] == 2.0
    assert row["cancelled"] == 0


def test_unmatched_schedule_is_dropped(seeded):
    index = matcher.load_route_index(seeded)
    store.handle_schedule(seeded, index, make_schedule(toc="XX"))
    assert seeded.execute("SELECT COUNT(*) c FROM schedules").fetchone()["c"] == 0


def test_arrival_without_schedule_is_ignored(seeded):
    store.handle_arrival(seeded, parser.Arrival(rid="ghost", tpl="EDINBUR", at="13:22"))
    assert seeded.execute("SELECT COUNT(*) c FROM arrivals").fetchone()["c"] == 0


def test_arrival_at_intermediate_stop_is_ignored(seeded):
    index = matcher.load_route_index(seeded)
    store.handle_schedule(seeded, index, make_schedule())
    store.handle_arrival(seeded, parser.Arrival(rid="r1", tpl="YORK", at="10:53"))
    assert seeded.execute("SELECT COUNT(*) c FROM arrivals").fetchone()["c"] == 0


def test_redelivered_arrival_updates_in_place(seeded):
    index = matcher.load_route_index(seeded)
    store.handle_schedule(seeded, index, make_schedule())
    store.handle_arrival(seeded, parser.Arrival(rid="r1", tpl="EDINBUR", at="13:22"))
    store.handle_arrival(seeded, parser.Arrival(rid="r1", tpl="EDINBUR", at="13:25"))
    rows = seeded.execute("SELECT * FROM arrivals").fetchall()
    assert len(rows) == 1
    assert rows[0]["delay_min"] == 5.0


def test_cancellation_recorded(seeded):
    index = matcher.load_route_index(seeded)
    store.handle_schedule(seeded, index, make_schedule(cancelled=True))
    row = seeded.execute("SELECT * FROM arrivals").fetchone()
    assert row["cancelled"] == 1
    assert row["actual_arr"] is None


def test_reinstatement_clears_cancellation(seeded):
    index = matcher.load_route_index(seeded)
    store.handle_schedule(seeded, index, make_schedule(cancelled=True))
    store.handle_schedule(seeded, index, make_schedule(cancelled=False))
    assert seeded.execute("SELECT COUNT(*) c FROM arrivals").fetchone()["c"] == 0


def test_overnight_arrival_rolls_to_next_day(seeded):
    index = matcher.load_route_index(seeded)
    store.handle_schedule(
        seeded, index,
        make_schedule(origin_dep="23:40", sched_arr="06:10"),
    )
    row = seeded.execute("SELECT sched_arr FROM schedules").fetchone()
    assert row["sched_arr"] == "2026-07-24T06:10:00"
    store.handle_arrival(seeded, parser.Arrival(rid="r1", tpl="EDINBUR", at="06:15"))
    arr = seeded.execute("SELECT * FROM arrivals").fetchone()
    assert arr["delay_min"] == 5.0
    assert arr["service_date"] == "2026-07-23"  # counts toward its start date


def test_heartbeat(seeded):
    store.touch_heartbeat(seeded)
    row = seeded.execute("SELECT value FROM meta WHERE key='heartbeat'").fetchone()
    assert row is not None
