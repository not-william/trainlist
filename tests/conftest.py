import pytest

from trainlist import db


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    db.init_db(c)
    yield c
    c.close()


@pytest.fixture
def seeded(conn):
    conn.execute(
        """INSERT INTO listings (id, slug, route_name, operator_name, toc, photo,
               photo_attribution, comfort, price, blurb)
           VALUES (1, 'ecml-lner', 'East Coast Main Line', 'LNER', 'GR',
               'ecml-lner.jpg', 'test attribution', 8, 4, '')"""
    )
    conn.execute("INSERT INTO listing_routes VALUES (1, 'KNGX', 'EDINBUR')")
    conn.execute("INSERT INTO listing_routes VALUES (1, 'EDINBUR', 'KNGX')")
    conn.commit()
    return conn
