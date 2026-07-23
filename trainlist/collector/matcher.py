from . import parser


def load_route_index(conn) -> dict[tuple[str, str, str], int]:
    index = {}
    rows = conn.execute(
        """SELECT lr.listing_id, l.toc, lr.origin_tpl, lr.dest_tpl
           FROM listing_routes lr JOIN listings l ON l.id = lr.listing_id"""
    )
    for r in rows:
        index[(r["toc"], r["origin_tpl"], r["dest_tpl"])] = r["listing_id"]
    return index


def match(index: dict, schedule: parser.Schedule) -> int | None:
    return index.get((schedule.toc, schedule.origin_tpl, schedule.dest_tpl))
