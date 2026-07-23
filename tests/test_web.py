from datetime import datetime, timezone

import pytest

from trainlist import db, stats, webapp


@pytest.fixture
def app_db(tmp_path):
    path = str(tmp_path / "test.db")
    conn = db.connect(path)
    db.init_db(conn)
    for i, (slug, op, comfort, price) in enumerate(
        [
            ("ecml-lner", "LNER", 8, 4),
            ("ecml-lumo", "Lumo", 9, 9),
            ("wcml-avanti", "Avanti West Coast", 7, 10),
        ],
        start=1,
    ):
        conn.execute(
            """INSERT INTO listings (id, slug, route_name, operator_name, toc,
                   photo, photo_attribution, comfort, price, blurb)
               VALUES (?, ?, 'Route', ?, 'XX', 'p.jpg', 'attr', ?, ?, '')""",
            (i, slug, op, comfort, price),
        )
    today = stats.london_today().isoformat()

    def add(rid, listing_id, delay):
        # actual_arr must be a valid ISO datetime, as store.py always writes one.
        iso = f"{today}T13:20:00"
        conn.execute(
            """INSERT INTO arrivals (rid, listing_id, service_date, sched_arr,
                   actual_arr, delay_min, cancelled)
               VALUES (?, ?, ?, ?, ?, ?, 0)""",
            (rid, listing_id, today, iso, iso, delay),
        )

    for i in range(6):  # LNER: 6/6 on time -> 100%
        add(f"a{i}", 1, 0.0)
    for i in range(5):  # Lumo: 2/5 on time -> 40%
        add(f"b{i}", 2, 0.0 if i < 2 else 9.0)
    for i in range(2):  # Avanti: only 2 arrivals -> not enough data
        add(f"c{i}", 3, 0.0)
    conn.commit()
    yield path, conn
    conn.close()


@pytest.fixture
def client(app_db):
    path, _ = app_db
    webapp.clear_cache()
    app = webapp.create_app(path)
    app.config["TESTING"] = True
    return app.test_client()


def order_of(html: bytes, *names: str) -> list[int]:
    return sorted(range(len(names)), key=lambda i: html.index(names[i].encode()))


def test_index_renders(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"Trainlist" in resp.data
    assert b"100%" in resp.data


def test_default_sort_is_punctuality_with_nodata_last(client):
    html = client.get("/").data
    assert order_of(html, "LNER", "Lumo", "Avanti West Coast") == [0, 1, 2]
    assert b"not enough data yet" in html


def test_sort_by_comfort(client):
    html = client.get("/?sort=comfort").data
    assert order_of(html, "Lumo", "LNER", "Avanti West Coast") == [0, 1, 2]


def test_sort_by_price(client):
    html = client.get("/?sort=price").data
    assert order_of(html, "Avanti West Coast", "Lumo", "LNER") == [0, 1, 2]


def test_invalid_params_fall_back(client):
    assert client.get("/?window=bogus&sort=bogus").status_code == 200


def test_health_without_heartbeat_is_503(client):
    assert client.get("/health").status_code == 503


def test_health_with_fresh_heartbeat_is_200(app_db):
    path, conn = app_db
    conn.execute(
        "INSERT INTO meta (key, value) VALUES ('heartbeat', ?)",
        (datetime.now(timezone.utc).isoformat(),),
    )
    conn.commit()
    webapp.clear_cache()
    app = webapp.create_app(path)
    assert app.test_client().get("/health").status_code == 200
