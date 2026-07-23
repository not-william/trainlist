from datetime import datetime

from trainlist import times


def test_combine_basic():
    assert times.combine("2026-07-23", "13:20") == datetime(2026, 7, 23, 13, 20)


def test_combine_with_seconds():
    assert times.combine("2026-07-23", "13:20:30") == datetime(2026, 7, 23, 13, 20, 30)


def test_combine_near_rolls_forward_past_midnight():
    sched = datetime(2026, 7, 24, 0, 10)
    assert times.combine("2026-07-23", "00:15", near=sched) == datetime(2026, 7, 24, 0, 15)


def test_combine_near_rolls_back_before_midnight():
    sched = datetime(2026, 7, 23, 23, 55)
    assert times.combine("2026-07-24", "23:50", near=sched) == datetime(2026, 7, 23, 23, 50)


def test_combine_near_no_shift_when_close():
    sched = datetime(2026, 7, 23, 13, 20)
    assert times.combine("2026-07-23", "13:22", near=sched) == datetime(2026, 7, 23, 13, 22)


def test_delay_minutes():
    sched = datetime(2026, 7, 23, 13, 20)
    actual = datetime(2026, 7, 23, 13, 22)
    assert times.delay_minutes(sched, actual) == 2.0


def test_delay_minutes_early_is_negative():
    sched = datetime(2026, 7, 23, 13, 20)
    actual = datetime(2026, 7, 23, 13, 19)
    assert times.delay_minutes(sched, actual) == -1.0
