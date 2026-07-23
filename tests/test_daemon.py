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


def test_subscription_config_topic_is_durable():
    # Topics support durable subscription: client-id + subscriptionName.
    connect_headers, sub_headers = daemon.subscription_config(
        "/topic/darwin.pushport-v16", user="u@example.com"
    )
    assert connect_headers == {"client-id": "u@example.com"}
    assert sub_headers == {"activemq.subscriptionName": "trainlist"}


def test_subscription_config_queue_is_plain():
    # Queues cannot be durably subscribed; must omit durable headers.
    connect_headers, sub_headers = daemon.subscription_config(
        "/queue/D0BExampleQueue", user="u@example.com"
    )
    assert connect_headers == {}
    assert sub_headers == {}
