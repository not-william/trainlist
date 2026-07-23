import gzip
from pathlib import Path

from trainlist.collector import daemon, matcher

FIXTURES = Path(__file__).parent / "fixtures"


def read(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def test_handle_raw_end_to_end(seeded):
    index = matcher.load_route_index(seeded)
    daemon.handle_raw(seeded, index, read("schedule.xml"))
    daemon.handle_raw(seeded, index, gzip.compress(read("ts.xml")))
    row = seeded.execute("SELECT * FROM arrivals").fetchone()
    assert row["delay_min"] == 2.0
    assert seeded.execute(
        "SELECT value FROM meta WHERE key='heartbeat'"
    ).fetchone() is not None


def test_handle_raw_malformed_does_not_raise(seeded):
    index = matcher.load_route_index(seeded)
    daemon.handle_raw(seeded, index, read("malformed.xml"))  # must not raise
    daemon.handle_raw(seeded, index, b"")  # must not raise
    assert seeded.execute("SELECT COUNT(*) c FROM arrivals").fetchone()["c"] == 0
