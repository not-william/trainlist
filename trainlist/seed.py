import os
import sys
from pathlib import Path

import yaml

from . import db


def load_listings(conn, yaml_path) -> None:
    data = yaml.safe_load(Path(yaml_path).read_text())
    for item in data["listings"]:
        conn.execute(
            """
            INSERT INTO listings (slug, route_name, operator_name, toc, photo,
                                  photo_attribution, comfort, price, blurb)
            VALUES (:slug, :route_name, :operator_name, :toc, :photo,
                    :photo_attribution, :comfort, :price, :blurb)
            ON CONFLICT(slug) DO UPDATE SET
                route_name=excluded.route_name,
                operator_name=excluded.operator_name,
                toc=excluded.toc,
                photo=excluded.photo,
                photo_attribution=excluded.photo_attribution,
                comfort=excluded.comfort,
                price=excluded.price,
                blurb=excluded.blurb
            """,
            {
                "slug": item["slug"],
                "route_name": item["route_name"],
                "operator_name": item["operator_name"],
                "toc": item["toc"],
                "photo": item["photo"],
                "photo_attribution": item["photo_attribution"],
                "comfort": item["comfort"],
                "price": item["price"],
                "blurb": item.get("blurb", ""),
            },
        )
        listing_id = conn.execute(
            "SELECT id FROM listings WHERE slug=?", (item["slug"],)
        ).fetchone()["id"]
        conn.execute("DELETE FROM listing_routes WHERE listing_id=?", (listing_id,))
        for origin_tpl, dest_tpl in item["routes"]:
            conn.execute(
                "INSERT INTO listing_routes (listing_id, origin_tpl, dest_tpl) VALUES (?, ?, ?)",
                (listing_id, origin_tpl, dest_tpl),
            )
    conn.commit()


def main() -> None:
    db_path = os.environ.get("TRAINLIST_DB", "trainlist.db")
    yaml_path = sys.argv[1] if len(sys.argv) > 1 else "listings.yaml"
    conn = db.connect(db_path)
    db.init_db(conn)
    load_listings(conn, yaml_path)
    n = conn.execute("SELECT COUNT(*) c FROM listings").fetchone()["c"]
    print(f"Seeded {n} listings into {db_path}")


if __name__ == "__main__":
    main()
