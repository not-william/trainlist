import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    id INTEGER PRIMARY KEY,
    slug TEXT NOT NULL UNIQUE,
    route_name TEXT NOT NULL,
    operator_name TEXT NOT NULL,
    toc TEXT NOT NULL,
    photo TEXT NOT NULL,
    photo_attribution TEXT NOT NULL,
    comfort REAL NOT NULL,
    price REAL NOT NULL,
    blurb TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS listing_routes (
    listing_id INTEGER NOT NULL REFERENCES listings(id),
    origin_tpl TEXT NOT NULL,
    dest_tpl TEXT NOT NULL,
    PRIMARY KEY (listing_id, origin_tpl, dest_tpl)
);

CREATE TABLE IF NOT EXISTS schedules (
    rid TEXT PRIMARY KEY,
    listing_id INTEGER NOT NULL REFERENCES listings(id),
    ssd TEXT NOT NULL,
    dest_tpl TEXT NOT NULL,
    sched_arr TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS arrivals (
    rid TEXT PRIMARY KEY,
    listing_id INTEGER NOT NULL REFERENCES listings(id),
    service_date TEXT NOT NULL,
    sched_arr TEXT,
    actual_arr TEXT,
    delay_min REAL,
    cancelled INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_arrivals_listing_date
    ON arrivals(listing_id, service_date);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def connect(path: str) -> sqlite3.Connection:
    # check_same_thread=False: the STOMP listener thread is the sole user of
    # the collector's connection; the web app opens its own per request.
    conn = sqlite3.connect(path, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()
