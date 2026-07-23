import os
import time
from datetime import datetime, timezone

from flask import Flask, g, jsonify, render_template, request

from . import db, stats

CACHE_TTL_SECONDS = 60
HEARTBEAT_STALE_SECONDS = 600
SORTS = ("punctuality", "comfort", "price")

_cache: dict[tuple, tuple[float, dict]] = {}


def clear_cache() -> None:
    _cache.clear()


def _cached_punctuality(conn, db_path: str, window: str) -> dict:
    now = time.monotonic()
    key = (db_path, window)
    hit = _cache.get(key)
    if hit and now - hit[0] < CACHE_TTL_SECONDS:
        return hit[1]
    data = stats.punctuality(conn, window)
    _cache[key] = (now, data)
    return data


def _build_cards(conn, db_path: str, window: str) -> list[dict]:
    pmap = _cached_punctuality(conn, db_path, window)
    cards = []
    for l in conn.execute("SELECT * FROM listings ORDER BY slug"):
        s = pmap.get(l["id"], {"pct": None, "total": 0})
        cards.append(
            {
                "slug": l["slug"],
                "route_name": l["route_name"],
                "operator_name": l["operator_name"],
                "photo": l["photo"],
                "photo_attribution": l["photo_attribution"],
                "comfort": l["comfort"],
                "price": l["price"],
                "blurb": l["blurb"],
                "punctuality": s["pct"],
                "total": s["total"],
            }
        )
    return cards


def _sort_cards(cards: list[dict], sort: str) -> list[dict]:
    if sort == "comfort":
        return sorted(cards, key=lambda c: -c["comfort"])
    if sort == "price":
        return sorted(cards, key=lambda c: -c["price"])
    return sorted(
        cards, key=lambda c: (c["punctuality"] is None, -(c["punctuality"] or 0))
    )


def _last_updated(conn) -> str | None:
    row = conn.execute("SELECT MAX(actual_arr) AS m FROM arrivals").fetchone()
    if not row["m"]:
        return None
    return datetime.fromisoformat(row["m"]).strftime("%-d %b %H:%M")


def create_app(db_path: str | None = None) -> Flask:
    app = Flask(__name__)
    app.config["DB_PATH"] = db_path or os.environ.get("TRAINLIST_DB", "trainlist.db")

    def get_conn():
        if "conn" not in g:
            g.conn = db.connect(app.config["DB_PATH"])
        return g.conn

    @app.teardown_appcontext
    def close_conn(exc):
        conn = g.pop("conn", None)
        if conn is not None:
            conn.close()

    @app.route("/")
    def index():
        window = request.args.get("window", "today")
        if window not in stats.WINDOWS:
            window = "today"
        sort = request.args.get("sort", "punctuality")
        if sort not in SORTS:
            sort = "punctuality"
        conn = get_conn()
        cards = _sort_cards(
            _build_cards(conn, app.config["DB_PATH"], window), sort
        )
        return render_template(
            "index.html",
            cards=cards,
            window=window,
            sort=sort,
            last_updated=_last_updated(conn),
            on_time_minutes=int(stats.ON_TIME_MINUTES),
        )

    @app.route("/health")
    def health():
        conn = get_conn()
        row = conn.execute(
            "SELECT value FROM meta WHERE key='heartbeat'"
        ).fetchone()
        if row is None:
            return jsonify({"status": "no heartbeat"}), 503
        age = (
            datetime.now(timezone.utc) - datetime.fromisoformat(row["value"])
        ).total_seconds()
        ok = age < HEARTBEAT_STALE_SECONDS
        return (
            jsonify(
                {"status": "ok" if ok else "stale", "heartbeat_age_seconds": int(age)}
            ),
            200 if ok else 503,
        )

    return app
