import logging
import os
import time

import stomp

from .. import db
from . import matcher, parser, store

log = logging.getLogger("trainlist.collector")

RECONNECT_MAX_SECONDS = 300


def handle_raw(conn, index, raw: bytes) -> None:
    try:
        messages = parser.parse_message(raw)
    except Exception:
        log.warning("skipping unparseable message", exc_info=True)
        return
    for msg in messages:
        if isinstance(msg, parser.Schedule):
            store.handle_schedule(conn, index, msg)
        elif isinstance(msg, parser.Arrival):
            store.handle_arrival(conn, msg)
    store.touch_heartbeat(conn)


class DarwinListener(stomp.ConnectionListener):
    def __init__(self, conn, index):
        self.conn = conn
        self.index = index

    def on_message(self, frame):
        body = frame.body
        if isinstance(body, str):
            body = body.encode("utf-8", "replace")
        try:
            handle_raw(self.conn, self.index, body)
        except Exception:
            log.exception("error handling message")

    def on_error(self, frame):
        log.error("broker error: %s", frame.body)


def run(conn, index) -> None:
    """Connect, subscribe, block until disconnected, then raise."""
    host = os.environ["DARWIN_HOST"]
    port = int(os.environ.get("DARWIN_PORT", "61613"))
    user = os.environ["DARWIN_USER"]
    password = os.environ["DARWIN_PASS"]
    topic = os.environ.get("DARWIN_TOPIC", "/topic/darwin.pushport-v16")

    c = stomp.Connection12([(host, port)], heartbeats=(15000, 15000), auto_decode=False)
    c.set_listener("darwin", DarwinListener(conn, index))
    # client-id + subscriptionName make the subscription durable, so the broker
    # queues messages across short disconnects.
    c.connect(user, password, wait=True, headers={"client-id": user})
    c.subscribe(
        topic,
        id="trainlist",
        ack="auto",
        headers={"activemq.subscriptionName": "trainlist"},
    )
    log.info("connected to %s:%s, subscribed to %s", host, port, topic)
    while c.is_connected():
        time.sleep(5)
    raise ConnectionError("disconnected from Darwin")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    conn = db.connect(os.environ.get("TRAINLIST_DB", "trainlist.db"))
    db.init_db(conn)
    index = matcher.load_route_index(conn)
    log.info("tracking %d route pairs", len(index))
    backoff = 1
    while True:
        try:
            run(conn, index)
            backoff = 1
        except Exception:
            log.exception("connection lost")
        log.info("reconnecting in %ds", backoff)
        time.sleep(backoff)
        backoff = min(backoff * 2, RECONNECT_MAX_SECONDS)


if __name__ == "__main__":
    main()
