from datetime import date, datetime, timedelta


def combine(ssd: str, t: str, near: datetime | None = None) -> datetime:
    """Combine a service start date with a Darwin working time.

    Darwin times are local UK wall-clock times without a date. If `near` is
    given, shift the result by ±1 day so it lies within 12h of `near` —
    handles trains whose arrival falls the calendar day after `ssd`.
    """
    d = date.fromisoformat(ssd)
    parts = t.split(":")
    hour, minute = int(parts[0]), int(parts[1])
    second = int(parts[2]) if len(parts) > 2 else 0
    dt = datetime(d.year, d.month, d.day, hour, minute, second)
    if near is not None:
        if dt - near > timedelta(hours=12):
            dt -= timedelta(days=1)
        elif near - dt > timedelta(hours=12):
            dt += timedelta(days=1)
    return dt


def delay_minutes(sched: datetime, actual: datetime) -> float:
    return (actual - sched).total_seconds() / 60.0
