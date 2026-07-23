import sqlite3

import pytest

from trainlist import db


def test_init_creates_tables(conn):
    tables = {
        r["name"]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert {"listings", "listing_routes", "schedules", "arrivals", "meta"} <= tables


def test_init_is_idempotent(conn):
    db.init_db(conn)  # second call must not raise


def test_arrivals_rid_is_unique(conn):
    conn.execute(
        "INSERT INTO listings (id, slug, route_name, operator_name, toc, photo,"
        " photo_attribution, comfort, price) VALUES (1,'s','r','o','GR','p','a',5,5)"
    )
    conn.execute(
        "INSERT INTO arrivals (rid, listing_id, service_date) VALUES ('r1', 1, '2026-07-23')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO arrivals (rid, listing_id, service_date) VALUES ('r1', 1, '2026-07-23')"
        )


def test_row_factory(conn):
    row = conn.execute("SELECT 1 AS x").fetchone()
    assert row["x"] == 1
