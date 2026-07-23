from trainlist import seed

YAML = """
listings:
  - slug: ecml-lner
    route_name: East Coast Main Line
    operator_name: LNER
    toc: GR
    photo: ecml-lner.jpg
    photo_attribution: "attr"
    comfort: 8
    price: 4
    routes:
      - [KNGX, EDINBUR]
      - [EDINBUR, KNGX]
"""


def write_yaml(tmp_path, text):
    p = tmp_path / "listings.yaml"
    p.write_text(text)
    return p


def test_load_listings(conn, tmp_path):
    seed.load_listings(conn, write_yaml(tmp_path, YAML))
    row = conn.execute("SELECT * FROM listings WHERE slug='ecml-lner'").fetchone()
    assert row["operator_name"] == "LNER"
    assert row["toc"] == "GR"
    assert row["comfort"] == 8
    assert row["blurb"] == ""  # optional key defaults to empty
    routes = conn.execute(
        "SELECT origin_tpl, dest_tpl FROM listing_routes WHERE listing_id=?",
        (row["id"],),
    ).fetchall()
    assert {(r["origin_tpl"], r["dest_tpl"]) for r in routes} == {
        ("KNGX", "EDINBUR"),
        ("EDINBUR", "KNGX"),
    }


def test_load_listings_is_idempotent(conn, tmp_path):
    p = write_yaml(tmp_path, YAML)
    seed.load_listings(conn, p)
    seed.load_listings(conn, p)
    assert conn.execute("SELECT COUNT(*) c FROM listings").fetchone()["c"] == 1
    assert conn.execute("SELECT COUNT(*) c FROM listing_routes").fetchone()["c"] == 2


def test_reseed_updates_scores(conn, tmp_path):
    seed.load_listings(conn, write_yaml(tmp_path, YAML))
    seed.load_listings(conn, write_yaml(tmp_path, YAML.replace("comfort: 8", "comfort: 9")))
    row = conn.execute("SELECT comfort FROM listings WHERE slug='ecml-lner'").fetchone()
    assert row["comfort"] == 9
