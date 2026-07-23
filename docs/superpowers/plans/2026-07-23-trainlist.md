# Trainlist Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A levelsio-style single-page site ranking UK train routes (route + operator) by punctuality, comfort, and price, fed by our own Darwin Push Port collector.

**Architecture:** Two Python processes on one VPS share one SQLite database (WAL mode). A collector daemon subscribes to Darwin Push Port over STOMP, stores one row per tracked train arrival, and a Flask app aggregates "x% on time" per listing at request time with a 60-second in-memory cache. Comfort and price are curated seed data in `listings.yaml`.

**Tech Stack:** Python ≥3.11, Flask, SQLite (stdlib `sqlite3`), stomp.py, PyYAML, gunicorn, pytest.

**Spec:** `docs/superpowers/specs/2026-07-23-trainlist-design.md`

## Global Constraints

- Python ≥ 3.11 (needs `zoneinfo`, modern dataclasses).
- Runtime dependencies ONLY: `flask`, `gunicorn`, `PyYAML`, `stomp.py`. Dev dependency: `pytest`. No ORMs, no frontend frameworks.
- Locations are Darwin **TIPLOC** codes (e.g. `KNGX`, `EDINBUR`), never CRS.
- "On time" = arrival at final destination with `delay_min <= 5` (env `ON_TIME_MINUTES`, default 5). Cancelled trains are NOT on time.
- A listing shows a percentage only with ≥ 5 arrivals in the window (`MIN_ARRIVALS = 5`); otherwise "not enough data yet".
- Service dates and windows use `Europe/London` dates. Windows: today = today; week = last 7 days incl. today; month = last 30 days incl. today.
- SQLite in WAL mode; the collector is the only writer, the web app only reads.
- XML parsing is namespace-agnostic (match on local tag names) and defensive: a bad message is logged and skipped, never crashes the daemon.
- TDD for every task: write the failing test, watch it fail, implement, watch it pass, commit.

## File Structure

```
pyproject.toml               # packaging, deps, pytest config
listings.yaml                # curated seed: listings, routes, scores, photo credits
trainlist/
├── __init__.py
├── db.py                    # connect(), init_db(), schema
├── times.py                 # working-time parsing, midnight rollover, delay calc
├── seed.py                  # listings.yaml → DB (idempotent), CLI
├── stats.py                 # windows, punctuality aggregation
├── webapp.py                # create_app(), cards, cache, /health
├── templates/index.html
├── static/style.css
├── static/stock/            # rolling stock photos (Task 10)
└── collector/
    ├── __init__.py
    ├── parser.py            # Darwin XML → Schedule / Arrival dataclasses
    ├── matcher.py           # (toc, origin, dest) → listing id
    ├── store.py             # write schedules/arrivals/heartbeat
    └── daemon.py            # STOMP loop, reconnect, handle_raw()
tests/
├── conftest.py              # conn / seeded fixtures
├── fixtures/                # captured-shape Darwin XML samples
└── test_*.py
deploy/
├── trainlist-web.service
├── trainlist-collector.service
└── deploy.sh
.env.example
README.md
```

---

### Task 1: Project scaffold + database schema

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `trainlist/__init__.py`, `trainlist/db.py`, `tests/conftest.py`, `tests/test_db.py`

**Interfaces:**
- Produces: `db.connect(path: str) -> sqlite3.Connection` (Row factory, WAL, FKs on, `check_same_thread=False`); `db.init_db(conn) -> None`. Tables: `listings(id, slug UNIQUE, route_name, operator_name, toc, photo, photo_attribution, comfort, price, blurb)`, `listing_routes(listing_id, origin_tpl, dest_tpl)`, `schedules(rid PK, listing_id, ssd, dest_tpl, sched_arr)`, `arrivals(rid PK, listing_id, service_date, sched_arr, actual_arr, delay_min, cancelled)`, `meta(key PK, value)`.

- [ ] **Step 1: Create scaffold files**

`pyproject.toml`:

```toml
[project]
name = "trainlist"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "flask>=3.0",
    "gunicorn>=21",
    "PyYAML>=6",
    "stomp.py>=8",
]

[project.optional-dependencies]
dev = ["pytest>=8"]

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
include = ["trainlist*"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

`.gitignore`:

```
venv/
__pycache__/
*.db
*.db-wal
*.db-shm
.env
```

`trainlist/__init__.py`: empty file.

Then: `python -m venv venv && venv/bin/pip install -e '.[dev]'`

- [ ] **Step 2: Write the failing tests**

`tests/conftest.py`:

```python
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
```

`tests/test_db.py`:

```python
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
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `venv/bin/pytest tests/test_db.py -v`
Expected: FAIL / ERROR with `ModuleNotFoundError` or `AttributeError` (no `trainlist.db`).

- [ ] **Step 4: Implement `trainlist/db.py`**

```python
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `venv/bin/pytest tests/test_db.py -v`
Expected: 4 passed.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml .gitignore trainlist/ tests/
git commit -m "feat: project scaffold and sqlite schema"
```

---

### Task 2: Time helpers

**Files:**
- Create: `trainlist/times.py`, `tests/test_times.py`

**Interfaces:**
- Produces: `times.combine(ssd: str, t: str, near: datetime | None = None) -> datetime` — combines a `YYYY-MM-DD` service start date with a Darwin working time (`HH:MM` or `HH:MM:SS`); if `near` is given, shifts ±1 day so the result lies within 12h of it (midnight rollover). `times.delay_minutes(sched: datetime, actual: datetime) -> float`.

- [ ] **Step 1: Write the failing tests**

`tests/test_times.py`:

```python
from datetime import datetime

from trainlist import times


def test_combine_basic():
    assert times.combine("2026-07-23", "13:20") == datetime(2026, 7, 23, 13, 20)


def test_combine_with_seconds():
    assert times.combine("2026-07-23", "13:20:30") == datetime(2026, 7, 23, 13, 20, 30)


def test_combine_near_rolls_forward_past_midnight():
    sched = datetime(2026, 7, 24, 0, 10)
    assert times.combine("2026-07-23", "00:15", near=sched) == datetime(2026, 7, 24, 0, 15)


def test_combine_near_rolls_back_before_midnight():
    sched = datetime(2026, 7, 23, 23, 55)
    assert times.combine("2026-07-24", "23:50", near=sched) == datetime(2026, 7, 23, 23, 50)


def test_combine_near_no_shift_when_close():
    sched = datetime(2026, 7, 23, 13, 20)
    assert times.combine("2026-07-23", "13:22", near=sched) == datetime(2026, 7, 23, 13, 22)


def test_delay_minutes():
    sched = datetime(2026, 7, 23, 13, 20)
    actual = datetime(2026, 7, 23, 13, 22)
    assert times.delay_minutes(sched, actual) == 2.0


def test_delay_minutes_early_is_negative():
    sched = datetime(2026, 7, 23, 13, 20)
    actual = datetime(2026, 7, 23, 13, 19)
    assert times.delay_minutes(sched, actual) == -1.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/pytest tests/test_times.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'trainlist.times'`.

- [ ] **Step 3: Implement `trainlist/times.py`**

```python
from datetime import date, datetime, timedelta


def combine(ssd: str, t: str, near: datetime | None = None) -> datetime:
    """Combine a service start date with a Darwin working time.

    Darwin times are local UK wall-clock times without a date. If `near` is
    given, shift the result by ±1 day so it lies within 12h of `near` —
    handles trains whose arrival falls the calendar day after `ssd`.
    """
    d = date.fromisoformat(ssd)
    parts = t.split(":")
    hour, minute = int(parts[0]), int(parts[1])
    second = int(parts[2]) if len(parts) > 2 else 0
    dt = datetime(d.year, d.month, d.day, hour, minute, second)
    if near is not None:
        if dt - near > timedelta(hours=12):
            dt -= timedelta(days=1)
        elif near - dt > timedelta(hours=12):
            dt += timedelta(days=1)
    return dt


def delay_minutes(sched: datetime, actual: datetime) -> float:
    return (actual - sched).total_seconds() / 60.0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/pytest tests/test_times.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add trainlist/times.py tests/test_times.py
git commit -m "feat: working-time helpers with midnight rollover"
```

---

### Task 3: Seed loader + starter listings.yaml

**Files:**
- Create: `trainlist/seed.py`, `listings.yaml` (starter — full data in Task 10), `tests/test_seed.py`

**Interfaces:**
- Consumes: `db.connect`, `db.init_db` (Task 1).
- Produces: `seed.load_listings(conn, yaml_path: str | Path) -> None` — idempotent upsert by slug; replaces each listing's routes. CLI: `python -m trainlist.seed [listings.yaml]` (DB path from env `TRAINLIST_DB`, default `trainlist.db`). YAML shape: top-level `listings:` list, each with `slug, route_name, operator_name, toc, photo, photo_attribution, comfort, price, blurb (optional), routes: [[ORIGIN_TPL, DEST_TPL], ...]`.

- [ ] **Step 1: Create starter `listings.yaml`**

```yaml
# Curated seed data. Comfort/price are hand-scored 0-10 (price: 10 = cheap).
# Routes are directional TIPLOC pairs; list both directions.
listings:
  - slug: ecml-lner
    route_name: East Coast Main Line
    operator_name: LNER
    toc: GR
    photo: ecml-lner.jpg
    photo_attribution: "TODO-replace-in-task-10"
    comfort: 8
    price: 4
    blurb: "Azuma (Class 800/801) London–Edinburgh"
    routes:
      - [KNGX, EDINBUR]
      - [EDINBUR, KNGX]
  - slug: ecml-lumo
    route_name: East Coast Main Line
    operator_name: Lumo
    toc: LD
    photo: ecml-lumo.jpg
    photo_attribution: "TODO-replace-in-task-10"
    comfort: 6
    price: 9
    blurb: "Class 803, single-class London–Edinburgh"
    routes:
      - [KNGX, EDINBUR]
      - [EDINBUR, KNGX]
```

(The two `TODO-replace-in-task-10` strings are data placeholders deliberately resolved by Task 10, which replaces this whole file — not plan placeholders.)

- [ ] **Step 2: Write the failing tests**

`tests/test_seed.py`:

```python
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
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `venv/bin/pytest tests/test_seed.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'trainlist.seed'`.

- [ ] **Step 4: Implement `trainlist/seed.py`**

```python
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `venv/bin/pytest tests/test_seed.py -v`
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add trainlist/seed.py listings.yaml tests/test_seed.py
git commit -m "feat: idempotent listings.yaml seed loader"
```

---

### Task 4: Darwin XML parser

**Files:**
- Create: `trainlist/collector/__init__.py` (empty), `trainlist/collector/parser.py`, `tests/fixtures/schedule.xml`, `tests/fixtures/ts.xml`, `tests/fixtures/cancel.xml`, `tests/fixtures/malformed.xml`, `tests/test_parser.py`

**Interfaces:**
- Produces:
  - `@dataclass parser.Schedule(rid: str, ssd: str, toc: str, origin_tpl: str, origin_dep: str, dest_tpl: str, sched_arr: str, cancelled: bool)`
  - `@dataclass parser.Arrival(rid: str, tpl: str, at: str)`
  - `parser.parse_message(raw: bytes) -> list[Schedule | Arrival]` — accepts plain or gzipped XML; namespace-agnostic; raises `xml.etree.ElementTree.ParseError` on unparseable XML (caller catches); returns `[]` for messages with nothing we track.

- [ ] **Step 1: Create XML fixtures**

These mirror the shape of real Darwin Push Port messages (v16 Pport envelope; child elements carry their own namespaces — which is exactly why the parser matches local names only). After go-live, replace/extend with genuinely captured messages if they differ.

`tests/fixtures/schedule.xml`:

```xml
<Pport xmlns="http://www.thalesgroup.com/rtti/PushPort/v16" ts="2026-07-23T05:00:00.0000000+01:00" version="16.0">
  <uR updateOrigin="Darwin">
    <schedule xmlns:ns2="http://www.thalesgroup.com/rtti/PushPort/Schedules/v2" rid="202607238012345" uid="P12345" trainId="1S05" ssd="2026-07-23" toc="GR">
      <ns2:OR tpl="KNGX" act="TB" ptd="09:00" wtd="09:00"/>
      <ns2:IP tpl="YORK" act="T" pta="10:52" ptd="10:55" wta="10:52" wtd="10:55"/>
      <ns2:DT tpl="EDINBUR" act="TF" pta="13:20" wta="13:20"/>
    </schedule>
  </uR>
</Pport>
```

`tests/fixtures/ts.xml`:

```xml
<Pport xmlns="http://www.thalesgroup.com/rtti/PushPort/v16" ts="2026-07-23T13:22:10.0000000+01:00" version="16.0">
  <uR updateOrigin="TD">
    <TS xmlns:ns3="http://www.thalesgroup.com/rtti/PushPort/Forecasts/v3" rid="202607238012345" uid="P12345" ssd="2026-07-23">
      <ns3:Location tpl="YORK" wta="10:52" wtd="10:55">
        <ns3:arr et="10:53" src="Darwin"/>
      </ns3:Location>
      <ns3:Location tpl="EDINBUR" pta="13:20" wta="13:20">
        <ns3:arr at="13:22" src="TD"/>
        <ns3:plat>2</ns3:plat>
      </ns3:Location>
    </TS>
  </uR>
</Pport>
```

(The YORK location has only an estimate `et` — the parser must emit an `Arrival` only for actual `at` times, so this fixture yields exactly one `Arrival`.)

`tests/fixtures/cancel.xml` — same as `schedule.xml` but the destination is cancelled:

```xml
<Pport xmlns="http://www.thalesgroup.com/rtti/PushPort/v16" ts="2026-07-23T06:00:00.0000000+01:00" version="16.0">
  <uR updateOrigin="Darwin">
    <schedule xmlns:ns2="http://www.thalesgroup.com/rtti/PushPort/Schedules/v2" rid="202607238012345" uid="P12345" trainId="1S05" ssd="2026-07-23" toc="GR">
      <ns2:OR tpl="KNGX" act="TB" ptd="09:00" wtd="09:00" can="true"/>
      <ns2:DT tpl="EDINBUR" act="TF" pta="13:20" wta="13:20" can="true"/>
      <ns2:cancelReason>887</ns2:cancelReason>
    </schedule>
  </uR>
</Pport>
```

`tests/fixtures/malformed.xml`:

```xml
<Pport><uR><schedule rid="broken"
```

- [ ] **Step 2: Write the failing tests**

`tests/test_parser.py`:

```python
import gzip
from pathlib import Path
from xml.etree import ElementTree

import pytest

from trainlist.collector import parser

FIXTURES = Path(__file__).parent / "fixtures"


def read(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def test_parse_schedule():
    msgs = parser.parse_message(read("schedule.xml"))
    assert len(msgs) == 1
    s = msgs[0]
    assert isinstance(s, parser.Schedule)
    assert s.rid == "202607238012345"
    assert s.ssd == "2026-07-23"
    assert s.toc == "GR"
    assert s.origin_tpl == "KNGX"
    assert s.origin_dep == "09:00"
    assert s.dest_tpl == "EDINBUR"
    assert s.sched_arr == "13:20"
    assert s.cancelled is False


def test_parse_ts_actual_arrivals_only():
    msgs = parser.parse_message(read("ts.xml"))
    assert msgs == [parser.Arrival(rid="202607238012345", tpl="EDINBUR", at="13:22")]


def test_parse_cancellation():
    (s,) = parser.parse_message(read("cancel.xml"))
    assert s.cancelled is True


def test_parse_gzipped():
    msgs = parser.parse_message(gzip.compress(read("ts.xml")))
    assert len(msgs) == 1


def test_malformed_raises_parse_error():
    with pytest.raises(ElementTree.ParseError):
        parser.parse_message(read("malformed.xml"))


def test_untracked_message_returns_empty():
    xml = b'<Pport xmlns="http://www.thalesgroup.com/rtti/PushPort/v16"><uR><trainAlert/></uR></Pport>'
    assert parser.parse_message(xml) == []
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `venv/bin/pytest tests/test_parser.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'trainlist.collector'`.

- [ ] **Step 4: Implement `trainlist/collector/parser.py`** (and empty `trainlist/collector/__init__.py`)

```python
import gzip
import xml.etree.ElementTree as ET
from dataclasses import dataclass

GZIP_MAGIC = b"\x1f\x8b"


@dataclass
class Schedule:
    rid: str
    ssd: str
    toc: str
    origin_tpl: str
    origin_dep: str
    dest_tpl: str
    sched_arr: str
    cancelled: bool


@dataclass
class Arrival:
    rid: str
    tpl: str
    at: str


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_message(raw: bytes) -> list[Schedule | Arrival]:
    """Parse one Darwin Pport message (plain or gzipped XML).

    Namespace-agnostic: Darwin nests several schema namespaces inside the
    Pport envelope, so we match on local tag names only.
    """
    if raw[:2] == GZIP_MAGIC:
        raw = gzip.decompress(raw)
    root = ET.fromstring(raw)
    out: list[Schedule | Arrival] = []
    for el in root.iter():
        name = _local(el.tag)
        if name == "schedule":
            s = _parse_schedule(el)
            if s is not None:
                out.append(s)
        elif name == "TS":
            out.extend(_parse_ts(el))
    return out


def _parse_schedule(el) -> Schedule | None:
    rid, ssd, toc = el.get("rid"), el.get("ssd"), el.get("toc")
    if not (rid and ssd and toc):
        return None
    origin = dest = None
    for child in el:
        name = _local(child.tag)
        if name in ("OR", "OPOR") and origin is None:
            origin = child
        elif name in ("DT", "OPDT"):
            dest = child
    if origin is None or dest is None:
        return None
    origin_tpl, dest_tpl = origin.get("tpl"), dest.get("tpl")
    origin_dep = origin.get("wtd") or origin.get("ptd")
    sched_arr = dest.get("wta") or dest.get("pta")
    if not (origin_tpl and dest_tpl and origin_dep and sched_arr):
        return None
    return Schedule(
        rid=rid,
        ssd=ssd,
        toc=toc,
        origin_tpl=origin_tpl,
        origin_dep=origin_dep,
        dest_tpl=dest_tpl,
        sched_arr=sched_arr,
        cancelled=dest.get("can") == "true",
    )


def _parse_ts(el) -> list[Arrival]:
    rid = el.get("rid")
    if not rid:
        return []
    out = []
    for loc in el:
        if _local(loc.tag) != "Location":
            continue
        tpl = loc.get("tpl")
        if not tpl:
            continue
        for sub in loc:
            if _local(sub.tag) == "arr":
                at = sub.get("at")
                if at:
                    out.append(Arrival(rid=rid, tpl=tpl, at=at))
    return out
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `venv/bin/pytest tests/test_parser.py -v`
Expected: 6 passed.

- [ ] **Step 6: Commit**

```bash
git add trainlist/collector/ tests/fixtures/ tests/test_parser.py
git commit -m "feat: namespace-agnostic darwin pushport parser"
```

---

### Task 5: Route matcher

**Files:**
- Create: `trainlist/collector/matcher.py`, `tests/test_matcher.py`

**Interfaces:**
- Consumes: `parser.Schedule` (Task 4), DB tables (Task 1).
- Produces: `matcher.load_route_index(conn) -> dict[tuple[str, str, str], int]` mapping `(toc, origin_tpl, dest_tpl)` to listing id; `matcher.match(index, schedule: parser.Schedule) -> int | None`.

- [ ] **Step 1: Write the failing tests**

`tests/test_matcher.py`:

```python
from trainlist.collector import matcher, parser


def make_schedule(toc="GR", origin="KNGX", dest="EDINBUR"):
    return parser.Schedule(
        rid="r1",
        ssd="2026-07-23",
        toc=toc,
        origin_tpl=origin,
        origin_dep="09:00",
        dest_tpl=dest,
        sched_arr="13:20",
        cancelled=False,
    )


def test_match(seeded):
    index = matcher.load_route_index(seeded)
    assert matcher.match(index, make_schedule()) == 1


def test_match_reverse_direction(seeded):
    index = matcher.load_route_index(seeded)
    assert matcher.match(index, make_schedule(origin="EDINBUR", dest="KNGX")) == 1


def test_no_match_wrong_toc(seeded):
    index = matcher.load_route_index(seeded)
    assert matcher.match(index, make_schedule(toc="LD")) is None


def test_no_match_wrong_endpoints(seeded):
    index = matcher.load_route_index(seeded)
    assert matcher.match(index, make_schedule(dest="YORK")) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/pytest tests/test_matcher.py -v`
Expected: FAIL with `ModuleNotFoundError` / `AttributeError` for `matcher`.

- [ ] **Step 3: Implement `trainlist/collector/matcher.py`**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/pytest tests/test_matcher.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add trainlist/collector/matcher.py tests/test_matcher.py
git commit -m "feat: schedule-to-listing route matcher"
```

---

### Task 6: Store — schedules, arrivals, cancellations, heartbeat

**Files:**
- Create: `trainlist/collector/store.py`, `tests/test_store.py`

**Interfaces:**
- Consumes: `parser.Schedule` / `parser.Arrival` (Task 4), `matcher` (Task 5), `times.combine` / `times.delay_minutes` (Task 2).
- Produces: `store.handle_schedule(conn, index, schedule) -> None`; `store.handle_arrival(conn, arrival) -> None`; `store.touch_heartbeat(conn) -> None` (writes UTC ISO timestamp to `meta` key `heartbeat`). Rules: unmatched schedules are dropped; `sched_arr` is stored as a full ISO datetime (rolled to the next day if the arrival working time precedes the origin departure); a cancelled schedule upserts an `arrivals` row with `cancelled=1`; a later un-cancelled schedule deletes a cancelled row that has no actual arrival; an actual arrival is recorded only if its TIPLOC equals the stored `dest_tpl`, and upserts (re-delivery safe).

- [ ] **Step 1: Write the failing tests**

`tests/test_store.py`:

```python
from trainlist.collector import matcher, parser, store


def make_schedule(rid="r1", toc="GR", origin="KNGX", dest="EDINBUR",
                  origin_dep="09:00", sched_arr="13:20", ssd="2026-07-23",
                  cancelled=False):
    return parser.Schedule(
        rid=rid, ssd=ssd, toc=toc, origin_tpl=origin, origin_dep=origin_dep,
        dest_tpl=dest, sched_arr=sched_arr, cancelled=cancelled,
    )


def test_arrival_flow(seeded):
    index = matcher.load_route_index(seeded)
    store.handle_schedule(seeded, index, make_schedule())
    store.handle_arrival(seeded, parser.Arrival(rid="r1", tpl="EDINBUR", at="13:22"))
    row = seeded.execute("SELECT * FROM arrivals").fetchone()
    assert row["listing_id"] == 1
    assert row["service_date"] == "2026-07-23"
    assert row["delay_min"] == 2.0
    assert row["cancelled"] == 0


def test_unmatched_schedule_is_dropped(seeded):
    index = matcher.load_route_index(seeded)
    store.handle_schedule(seeded, index, make_schedule(toc="XX"))
    assert seeded.execute("SELECT COUNT(*) c FROM schedules").fetchone()["c"] == 0


def test_arrival_without_schedule_is_ignored(seeded):
    store.handle_arrival(seeded, parser.Arrival(rid="ghost", tpl="EDINBUR", at="13:22"))
    assert seeded.execute("SELECT COUNT(*) c FROM arrivals").fetchone()["c"] == 0


def test_arrival_at_intermediate_stop_is_ignored(seeded):
    index = matcher.load_route_index(seeded)
    store.handle_schedule(seeded, index, make_schedule())
    store.handle_arrival(seeded, parser.Arrival(rid="r1", tpl="YORK", at="10:53"))
    assert seeded.execute("SELECT COUNT(*) c FROM arrivals").fetchone()["c"] == 0


def test_redelivered_arrival_updates_in_place(seeded):
    index = matcher.load_route_index(seeded)
    store.handle_schedule(seeded, index, make_schedule())
    store.handle_arrival(seeded, parser.Arrival(rid="r1", tpl="EDINBUR", at="13:22"))
    store.handle_arrival(seeded, parser.Arrival(rid="r1", tpl="EDINBUR", at="13:25"))
    rows = seeded.execute("SELECT * FROM arrivals").fetchall()
    assert len(rows) == 1
    assert rows[0]["delay_min"] == 5.0


def test_cancellation_recorded(seeded):
    index = matcher.load_route_index(seeded)
    store.handle_schedule(seeded, index, make_schedule(cancelled=True))
    row = seeded.execute("SELECT * FROM arrivals").fetchone()
    assert row["cancelled"] == 1
    assert row["actual_arr"] is None


def test_reinstatement_clears_cancellation(seeded):
    index = matcher.load_route_index(seeded)
    store.handle_schedule(seeded, index, make_schedule(cancelled=True))
    store.handle_schedule(seeded, index, make_schedule(cancelled=False))
    assert seeded.execute("SELECT COUNT(*) c FROM arrivals").fetchone()["c"] == 0


def test_overnight_arrival_rolls_to_next_day(seeded):
    index = matcher.load_route_index(seeded)
    store.handle_schedule(
        seeded, index,
        make_schedule(origin_dep="23:40", sched_arr="06:10"),
    )
    row = seeded.execute("SELECT sched_arr FROM schedules").fetchone()
    assert row["sched_arr"] == "2026-07-24T06:10:00"
    store.handle_arrival(seeded, parser.Arrival(rid="r1", tpl="EDINBUR", at="06:15"))
    arr = seeded.execute("SELECT * FROM arrivals").fetchone()
    assert arr["delay_min"] == 5.0
    assert arr["service_date"] == "2026-07-23"  # counts toward its start date


def test_heartbeat(seeded):
    store.touch_heartbeat(seeded)
    row = seeded.execute("SELECT value FROM meta WHERE key='heartbeat'").fetchone()
    assert row is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/pytest tests/test_store.py -v`
Expected: FAIL with `ModuleNotFoundError` / `AttributeError` for `store`.

- [ ] **Step 3: Implement `trainlist/collector/store.py`**

```python
from datetime import datetime, timedelta, timezone

from .. import times
from . import matcher, parser


def handle_schedule(conn, index, schedule: parser.Schedule) -> None:
    listing_id = matcher.match(index, schedule)
    if listing_id is None:
        return
    sched_dt = times.combine(schedule.ssd, schedule.sched_arr)
    dep_dt = times.combine(schedule.ssd, schedule.origin_dep)
    if sched_dt < dep_dt:  # arrival is past midnight relative to ssd
        sched_dt += timedelta(days=1)
    conn.execute(
        """INSERT INTO schedules (rid, listing_id, ssd, dest_tpl, sched_arr)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(rid) DO UPDATE SET listing_id=excluded.listing_id,
               ssd=excluded.ssd, dest_tpl=excluded.dest_tpl,
               sched_arr=excluded.sched_arr""",
        (schedule.rid, listing_id, schedule.ssd, schedule.dest_tpl,
         sched_dt.isoformat()),
    )
    if schedule.cancelled:
        conn.execute(
            """INSERT INTO arrivals (rid, listing_id, service_date, sched_arr, cancelled)
               VALUES (?, ?, ?, ?, 1)
               ON CONFLICT(rid) DO UPDATE SET cancelled=1""",
            (schedule.rid, listing_id, schedule.ssd, sched_dt.isoformat()),
        )
    else:
        conn.execute(
            "DELETE FROM arrivals WHERE rid = ? AND cancelled = 1 AND actual_arr IS NULL",
            (schedule.rid,),
        )
    conn.commit()


def handle_arrival(conn, arrival: parser.Arrival) -> None:
    row = conn.execute(
        "SELECT * FROM schedules WHERE rid = ?", (arrival.rid,)
    ).fetchone()
    if row is None or row["dest_tpl"] != arrival.tpl:
        return
    sched_dt = datetime.fromisoformat(row["sched_arr"])
    actual_dt = times.combine(row["ssd"], arrival.at, near=sched_dt)
    delay = times.delay_minutes(sched_dt, actual_dt)
    conn.execute(
        """INSERT INTO arrivals (rid, listing_id, service_date, sched_arr,
                                 actual_arr, delay_min, cancelled)
           VALUES (?, ?, ?, ?, ?, ?, 0)
           ON CONFLICT(rid) DO UPDATE SET actual_arr=excluded.actual_arr,
               delay_min=excluded.delay_min, cancelled=0""",
        (arrival.rid, row["listing_id"], row["ssd"], row["sched_arr"],
         actual_dt.isoformat(), delay),
    )
    conn.commit()


def touch_heartbeat(conn) -> None:
    conn.execute(
        """INSERT INTO meta (key, value) VALUES ('heartbeat', ?)
           ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
        (datetime.now(timezone.utc).isoformat(),),
    )
    conn.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/pytest tests/test_store.py -v`
Expected: 9 passed.

- [ ] **Step 5: Commit**

```bash
git add trainlist/collector/store.py tests/test_store.py
git commit -m "feat: arrival/cancellation storage with midnight handling"
```

---

### Task 7: Punctuality stats

**Files:**
- Create: `trainlist/stats.py`, `tests/test_stats.py`

**Interfaces:**
- Consumes: `arrivals` table (Task 1).
- Produces: `stats.WINDOWS = {"today": 0, "week": 6, "month": 29}`; `stats.ON_TIME_MINUTES` (float, env `ON_TIME_MINUTES`, default 5); `stats.MIN_ARRIVALS = 5`; `stats.london_today() -> date`; `stats.window_start(window: str, today: date | None = None) -> date`; `stats.punctuality(conn, window: str, today: date | None = None) -> dict[int, dict]` mapping listing id to `{"pct": int | None, "total": int}` — `pct` rounded, `None` when `total < MIN_ARRIVALS`.

- [ ] **Step 1: Write the failing tests**

`tests/test_stats.py`:

```python
from datetime import date, datetime
from zoneinfo import ZoneInfo

from trainlist import stats

TODAY = date(2026, 7, 23)


def add_arrival(conn, rid, listing_id=1, service_date="2026-07-23",
                delay=0.0, cancelled=0):
    conn.execute(
        """INSERT INTO arrivals (rid, listing_id, service_date, sched_arr,
                                 actual_arr, delay_min, cancelled)
           VALUES (?, ?, ?, '2026-07-23T13:20:00', '2026-07-23T13:20:00', ?, ?)""",
        (rid, listing_id, service_date, delay, cancelled),
    )
    conn.commit()


def test_window_start():
    assert stats.window_start("today", TODAY) == date(2026, 7, 23)
    assert stats.window_start("week", TODAY) == date(2026, 7, 17)
    assert stats.window_start("month", TODAY) == date(2026, 6, 24)


def test_london_today_uses_uk_calendar():
    assert stats.london_today() == datetime.now(ZoneInfo("Europe/London")).date()


def test_percentage_counts_late_and_cancelled(seeded):
    for i in range(8):
        add_arrival(seeded, f"ok{i}")
    add_arrival(seeded, "late", delay=7.0)
    add_arrival(seeded, "canc", cancelled=1)
    out = stats.punctuality(seeded, "today", today=TODAY)
    assert out[1] == {"pct": 80, "total": 10}


def test_exactly_threshold_is_on_time(seeded):
    for i in range(4):
        add_arrival(seeded, f"ok{i}")
    add_arrival(seeded, "edge", delay=5.0)
    out = stats.punctuality(seeded, "today", today=TODAY)
    assert out[1]["pct"] == 100


def test_below_min_arrivals_gives_none(seeded):
    for i in range(4):
        add_arrival(seeded, f"r{i}")
    out = stats.punctuality(seeded, "today", today=TODAY)
    assert out[1] == {"pct": None, "total": 4}


def test_window_boundaries(seeded):
    add_arrival(seeded, "today", service_date="2026-07-23")
    add_arrival(seeded, "week-edge", service_date="2026-07-17")
    add_arrival(seeded, "week-out", service_date="2026-07-16")
    add_arrival(seeded, "month-edge", service_date="2026-06-24")
    add_arrival(seeded, "month-out", service_date="2026-06-23")
    assert stats.punctuality(seeded, "today", today=TODAY)[1]["total"] == 1
    assert stats.punctuality(seeded, "week", today=TODAY)[1]["total"] == 2
    assert stats.punctuality(seeded, "month", today=TODAY)[1]["total"] == 4
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/pytest tests/test_stats.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'trainlist.stats'`.

- [ ] **Step 3: Implement `trainlist/stats.py`**

```python
import os
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

ON_TIME_MINUTES = float(os.environ.get("ON_TIME_MINUTES", "5"))
MIN_ARRIVALS = 5
WINDOWS = {"today": 0, "week": 6, "month": 29}
LONDON = ZoneInfo("Europe/London")


def london_today() -> date:
    # Windows roll over at UK midnight, not UTC midnight.
    return datetime.now(LONDON).date()


def window_start(window: str, today: date | None = None) -> date:
    if today is None:
        today = london_today()
    return today - timedelta(days=WINDOWS[window])


def punctuality(conn, window: str, today: date | None = None) -> dict[int, dict]:
    start = window_start(window, today)
    rows = conn.execute(
        """SELECT listing_id,
                  COUNT(*) AS total,
                  SUM(CASE WHEN cancelled = 0 AND delay_min <= ? THEN 1 ELSE 0 END)
                      AS on_time
           FROM arrivals
           WHERE service_date >= ?
           GROUP BY listing_id""",
        (ON_TIME_MINUTES, start.isoformat()),
    ).fetchall()
    out = {}
    for r in rows:
        pct = (
            round(100 * r["on_time"] / r["total"])
            if r["total"] >= MIN_ARRIVALS
            else None
        )
        out[r["listing_id"]] = {"pct": pct, "total": r["total"]}
    return out
```

(Window filtering is pure date arithmetic on stored `Europe/London` service dates, so BST/GMT transitions only affect `london_today()` — which is why no datetime-level DST tests are needed here.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/pytest tests/test_stats.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add trainlist/stats.py tests/test_stats.py
git commit -m "feat: windowed punctuality aggregation"
```

---

### Task 8: Flask web app — page, sorting, cache, /health

**Files:**
- Create: `trainlist/webapp.py`, `trainlist/templates/index.html`, `trainlist/static/style.css`, `tests/test_web.py`

**Interfaces:**
- Consumes: `db` (Task 1), `stats` (Task 7).
- Produces: `webapp.create_app(db_path: str | None = None) -> Flask` (db path falls back to env `TRAINLIST_DB`, then `trainlist.db`); `webapp.clear_cache() -> None` (for tests); `GET /?window=today|week|month&sort=punctuality|comfort|price` (invalid values fall back to defaults); `GET /health` → 200 JSON if collector heartbeat < 10 min old, else 503. Gunicorn entrypoint: `trainlist.webapp:create_app()`.

- [ ] **Step 1: Write the failing tests**

`tests/test_web.py`:

```python
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
        conn.execute(
            """INSERT INTO arrivals (rid, listing_id, service_date, sched_arr,
                   actual_arr, delay_min, cancelled)
               VALUES (?, ?, ?, 'x', 'x', ?, 0)""",
            (rid, listing_id, today, delay),
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/pytest tests/test_web.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'trainlist.webapp'`.

- [ ] **Step 3: Implement `trainlist/webapp.py`**

```python
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
```

- [ ] **Step 4: Create `trainlist/templates/index.html`**

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Trainlist — UK train routes ranked</title>
  <link rel="stylesheet" href="{{ url_for('static', filename='style.css') }}">
</head>
<body>
  <header>
    <h1>🚆 Trainlist</h1>
    <p class="tagline">UK train routes ranked by punctuality</p>
    {% if last_updated %}<p class="updated">last updated {{ last_updated }}</p>{% endif %}
  </header>

  <nav class="windows">
    {% for w, label in [("today", "Today"), ("week", "Last Week"), ("month", "Last Month")] %}
      <a class="window-box{{ ' active' if w == window }}"
         href="{{ url_for('index', window=w, sort=sort) }}">{{ label }}</a>
    {% endfor %}
  </nav>

  <nav class="sorts">
    <span>sort by</span>
    {% for s, label in [("punctuality", "🕐 Punctuality"), ("comfort", "💺 Comfort"), ("price", "💷 Price")] %}
      <a class="sort-link{{ ' active' if s == sort }}"
         href="{{ url_for('index', window=window, sort=s) }}">{{ label }}</a>
    {% endfor %}
  </nav>

  <main class="grid">
    {% for card in cards %}
    <article class="card">
      <div class="photo-wrap">
        <img src="{{ url_for('static', filename='stock/' + card.photo) }}"
             alt="{{ card.operator_name }} rolling stock" loading="lazy">
        <span class="rank">#{{ loop.index }}</span>
      </div>
      <div class="card-body">
        <h2>{{ card.route_name }}</h2>
        <p class="operator">{{ card.operator_name }}</p>
        {% if card.blurb %}<p class="blurb">{{ card.blurb }}</p>{% endif %}
        <div class="score">
          <span class="label">🕐</span>
          {% if card.punctuality is not none %}
            <span class="value"><strong>{{ card.punctuality }}%</strong> on time</span>
            <div class="bar"><div class="fill" style="width: {{ card.punctuality }}%"></div></div>
          {% else %}
            <span class="nodata">not enough data yet</span>
          {% endif %}
        </div>
        <div class="score">
          <span class="label">💺</span>
          <span class="value"><strong>{{ card.comfort }}</strong>/10 comfort</span>
          <div class="bar"><div class="fill" style="width: {{ card.comfort * 10 }}%"></div></div>
        </div>
        <div class="score">
          <span class="label">💷</span>
          <span class="value"><strong>{{ card.price }}</strong>/10 price</span>
          <div class="bar"><div class="fill" style="width: {{ card.price * 10 }}%"></div></div>
        </div>
      </div>
    </article>
    {% endfor %}
  </main>

  <footer>
    <p>Punctuality = arrival at final destination within {{ on_time_minutes }} minutes
       of schedule; cancellations count as not on time.
       Data from the Darwin Push Port feed © National Rail Enquiries.</p>
    <details>
      <summary>Photo credits</summary>
      <ul>
        {% for card in cards %}
        <li>{{ card.route_name }} / {{ card.operator_name }}: {{ card.photo_attribution }}</li>
        {% endfor %}
      </ul>
    </details>
  </footer>
</body>
</html>
```

- [ ] **Step 5: Create `trainlist/static/style.css`**

```css
* { box-sizing: border-box; margin: 0; padding: 0; }

body {
  font-family: -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  background: #f4f4f2;
  color: #1a1a1a;
  padding: 1.5rem;
  max-width: 1200px;
  margin: 0 auto;
}

header { text-align: center; margin-bottom: 1.5rem; }
header h1 { font-size: 2.2rem; }
.tagline { color: #555; }
.updated { color: #999; font-size: 0.8rem; margin-top: 0.3rem; }

.windows { display: flex; gap: 0.8rem; justify-content: center; margin-bottom: 1rem; }
.window-box {
  display: block;
  padding: 1rem 2rem;
  background: #fff;
  border: 2px solid #ddd;
  border-radius: 12px;
  font-weight: 700;
  text-decoration: none;
  color: #1a1a1a;
  transition: transform 0.1s, border-color 0.1s;
}
.window-box:hover { transform: translateY(-2px); }
.window-box.active { border-color: #e4002b; background: #fff5f6; }

.sorts {
  text-align: center;
  margin-bottom: 1.5rem;
  color: #777;
  font-size: 0.9rem;
}
.sort-link {
  margin-left: 0.6rem;
  text-decoration: none;
  color: #555;
  padding: 0.25rem 0.6rem;
  border-radius: 8px;
}
.sort-link.active { background: #1a1a1a; color: #fff; }

.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 1.2rem;
}

.card {
  background: #fff;
  border-radius: 14px;
  overflow: hidden;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.08);
  transition: transform 0.12s, box-shadow 0.12s;
}
.card:hover { transform: translateY(-4px); box-shadow: 0 6px 18px rgba(0, 0, 0, 0.12); }

.photo-wrap { position: relative; aspect-ratio: 16 / 9; background: #ddd; }
.photo-wrap img { width: 100%; height: 100%; object-fit: cover; display: block; }
.rank {
  position: absolute;
  top: 0.6rem;
  left: 0.6rem;
  background: rgba(0, 0, 0, 0.75);
  color: #fff;
  font-weight: 800;
  padding: 0.2rem 0.55rem;
  border-radius: 8px;
  font-size: 0.95rem;
}

.card-body { padding: 0.9rem 1rem 1.1rem; }
.card-body h2 { font-size: 1.05rem; }
.operator { color: #e4002b; font-weight: 700; font-size: 0.9rem; margin-bottom: 0.2rem; }
.blurb { color: #888; font-size: 0.8rem; margin-bottom: 0.4rem; }

.score { margin-top: 0.55rem; font-size: 0.9rem; }
.score .label { margin-right: 0.3rem; }
.score strong { font-size: 1.05rem; }
.nodata { color: #999; font-style: italic; }

.bar {
  height: 6px;
  background: #eee;
  border-radius: 3px;
  margin-top: 0.25rem;
  overflow: hidden;
}
.fill { height: 100%; background: #2fb344; border-radius: 3px; }

footer {
  margin-top: 2.5rem;
  color: #999;
  font-size: 0.8rem;
  text-align: center;
}
footer details { margin-top: 0.6rem; }
footer ul { list-style: none; margin-top: 0.4rem; }
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `venv/bin/pytest tests/test_web.py -v`
Expected: 7 passed.

- [ ] **Step 7: Smoke-check in a browser**

```bash
TRAINLIST_DB=/tmp/dev.db venv/bin/python -m trainlist.seed listings.yaml
TRAINLIST_DB=/tmp/dev.db venv/bin/flask --app "trainlist.webapp:create_app()" run
```

Open http://127.0.0.1:5000 — expect the two starter cards (both "not enough data yet"), working filter boxes and sort links.

- [ ] **Step 8: Commit**

```bash
git add trainlist/webapp.py trainlist/templates/ trainlist/static/ tests/test_web.py
git commit -m "feat: flask app with card grid, window filters, sorting, /health"
```

---

### Task 9: Collector daemon

**Files:**
- Create: `trainlist/collector/daemon.py`, `tests/test_daemon.py`

**Interfaces:**
- Consumes: `parser`, `matcher`, `store` (Tasks 4–6), `db` (Task 1).
- Produces: `daemon.handle_raw(conn, index, raw: bytes) -> None` (never raises: parse errors are logged and skipped; touches heartbeat after each processed message); CLI `python -m trainlist.collector.daemon`. Env: `TRAINLIST_DB`, `DARWIN_HOST`, `DARWIN_PORT` (default 61613), `DARWIN_USER`, `DARWIN_PASS`, `DARWIN_TOPIC` (default `/topic/darwin.pushport-v16`).

- [ ] **Step 1: Write the failing tests**

`tests/test_daemon.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/pytest tests/test_daemon.py -v`
Expected: FAIL with `ImportError`/`AttributeError` for `daemon`.

- [ ] **Step 3: Implement `trainlist/collector/daemon.py`**

```python
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
```

Note: `parser.parse_message(b"")` raises `ParseError`, which `handle_raw` catches — that's what the second malformed test covers.

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/pytest tests/test_daemon.py -v`
Expected: 2 passed.

- [ ] **Step 5: Run the whole suite**

Run: `venv/bin/pytest -v`
Expected: all tests from Tasks 1–9 pass.

- [ ] **Step 6: Commit**

```bash
git add trainlist/collector/daemon.py tests/test_daemon.py
git commit -m "feat: stomp collector daemon with reconnect and heartbeat"
```

---

### Task 10: Full curated seed data + photos

**Files:**
- Modify: `listings.yaml` (replace entirely)
- Create: `trainlist/static/stock/*.jpg` (14 photos)

**Interfaces:**
- Consumes: `seed.load_listings` (Task 3). No code changes — data only.

- [ ] **Step 1: Replace `listings.yaml` with the full curated set**

Comfort/price are editorial starting points (price: 10 = cheap) — tune freely.

```yaml
# Curated seed data. Comfort/price are hand-scored 0-10 (price: 10 = cheap).
# Routes are directional TIPLOC pairs; list both directions.
# photo_attribution is filled in Step 3 when photos are downloaded.
listings:
  - slug: ecml-lner
    route_name: East Coast Main Line
    operator_name: LNER
    toc: GR
    photo: ecml-lner.jpg
    photo_attribution: ""
    comfort: 8
    price: 4
    blurb: "Azuma (Class 800/801), London–Leeds/Newcastle/Edinburgh"
    routes:
      - [KNGX, EDINBUR]
      - [EDINBUR, KNGX]
      - [KNGX, NWCSTLE]
      - [NWCSTLE, KNGX]
      - [KNGX, LEEDS]
      - [LEEDS, KNGX]
  - slug: ecml-lumo
    route_name: East Coast Main Line
    operator_name: Lumo
    toc: LD
    photo: ecml-lumo.jpg
    photo_attribution: ""
    comfort: 6
    price: 9
    blurb: "Class 803, single-class London–Edinburgh"
    routes:
      - [KNGX, EDINBUR]
      - [EDINBUR, KNGX]
  - slug: ecml-hull-trains
    route_name: East Coast Main Line
    operator_name: Hull Trains
    toc: HT
    photo: ecml-hull-trains.jpg
    photo_attribution: ""
    comfort: 7
    price: 7
    blurb: "Paragon (Class 802), London–Hull"
    routes:
      - [KNGX, HULL]
      - [HULL, KNGX]
  - slug: ecml-grand-central
    route_name: East Coast Main Line
    operator_name: Grand Central
    toc: GC
    photo: ecml-grand-central.jpg
    photo_attribution: ""
    comfort: 6
    price: 8
    blurb: "Class 180 Adelante, London–Sunderland"
    routes:
      - [KNGX, SNDRLND]
      - [SNDRLND, KNGX]
  - slug: wcml-avanti
    route_name: West Coast Main Line
    operator_name: Avanti West Coast
    toc: VT
    photo: wcml-avanti.jpg
    photo_attribution: ""
    comfort: 7
    price: 3
    blurb: "Pendolino (Class 390), London–Manchester/Liverpool/Glasgow"
    routes:
      - [EUSTON, MNCRPIC]
      - [MNCRPIC, EUSTON]
      - [EUSTON, LVRPLSH]
      - [LVRPLSH, EUSTON]
      - [EUSTON, GLGC]
      - [GLGC, EUSTON]
  - slug: gwml-gwr
    route_name: Great Western Main Line
    operator_name: GWR
    toc: GW
    photo: gwml-gwr.jpg
    photo_attribution: ""
    comfort: 7
    price: 4
    blurb: "IET (Class 800/802), London–Bristol/Cardiff/Plymouth"
    routes:
      - [PADTON, BRSTLTM]
      - [BRSTLTM, PADTON]
      - [PADTON, CRDFCEN]
      - [CRDFCEN, PADTON]
      - [PADTON, PLYMTH]
      - [PLYMTH, PADTON]
  - slug: mml-emr
    route_name: Midland Main Line
    operator_name: East Midlands Railway
    toc: EM
    photo: mml-emr.jpg
    photo_attribution: ""
    comfort: 7
    price: 5
    blurb: "Class 222 Meridian, London–Sheffield/Nottingham"
    routes:
      - [STPX, SHEFFLD]
      - [SHEFFLD, STPX]
      - [STPX, NOTNGHM]
      - [NOTNGHM, STPX]
  - slug: bml-southern
    route_name: Brighton Main Line
    operator_name: Southern
    toc: SN
    photo: bml-southern.jpg
    photo_attribution: ""
    comfort: 5
    price: 6
    blurb: "Class 377 Electrostar, London Victoria–Brighton"
    routes:
      - [VICTRIC, BRGHTN]
      - [BRGHTN, VICTRIC]
  - slug: bml-gatwick-express
    route_name: Brighton Main Line
    operator_name: Gatwick Express
    toc: GX
    photo: bml-gatwick-express.jpg
    photo_attribution: ""
    comfort: 6
    price: 4
    blurb: "Class 387, non-stop London Victoria–Gatwick Airport"
    routes:
      - [VICTRIC, GTWK]
      - [GTWK, VICTRIC]
  - slug: chiltern
    route_name: Chiltern Main Line
    operator_name: Chiltern Railways
    toc: CH
    photo: chiltern.jpg
    photo_attribution: ""
    comfort: 7
    price: 7
    blurb: "Class 168, London Marylebone–Birmingham Moor Street"
    routes:
      - [MARYLBN, BMHMMST]
      - [BMHMMST, MARYLBN]
  - slug: geml-greater-anglia
    route_name: Great Eastern Main Line
    operator_name: Greater Anglia
    toc: LE
    photo: geml-greater-anglia.jpg
    photo_attribution: ""
    comfort: 8
    price: 5
    blurb: "Stadler FLIRT (Class 745), London Liverpool Street–Norwich"
    routes:
      - [LIVST, NRCH]
      - [NRCH, LIVST]
  - slug: xc-voyager
    route_name: Cross Country Route
    operator_name: CrossCountry
    toc: XC
    photo: xc-voyager.jpg
    photo_attribution: ""
    comfort: 4
    price: 4
    blurb: "Voyager (Class 220/221), Birmingham–Bristol/Manchester"
    routes:
      - [BHAMNWS, BRSTLTM]
      - [BRSTLTM, BHAMNWS]
      - [BHAMNWS, MNCRPIC]
      - [MNCRPIC, BHAMNWS]
  - slug: tpe-nova
    route_name: North TransPennine
    operator_name: TransPennine Express
    toc: TP
    photo: tpe-nova.jpg
    photo_attribution: ""
    comfort: 6
    price: 6
    blurb: "Nova fleet, Manchester–York/Newcastle"
    routes:
      - [MNCRPIC, YORK]
      - [YORK, MNCRPIC]
      - [MNCRPIC, NWCSTLE]
      - [NWCSTLE, MNCRPIC]
  - slug: se-highspeed
    route_name: High Speed 1
    operator_name: Southeastern Highspeed
    toc: SE
    photo: se-highspeed.jpg
    photo_attribution: ""
    comfort: 7
    price: 5
    blurb: "Javelin (Class 395), London St Pancras–Ashford"
    routes:
      - [STPANCI, ASHFKY]
      - [ASHFKY, STPANCI]
```

- [ ] **Step 2: Verify every TIPLOC and TOC code against reference data**

The TIPLOCs above are from memory and MUST be verified — a wrong TIPLOC silently collects nothing. Two options:

Option A (authoritative): download CORPUS from Network Rail (free account at https://publicdatafeeds.networkrail.co.uk/):

```bash
curl -u "$NROD_USER:$NROD_PASS" -L -o corpus.json.gz \
  "https://publicdatafeeds.networkrail.co.uk/ntrod/SupportingFileAuthenticate?type=CORPUS"
gunzip corpus.json.gz
python3 - <<'EOF'
import json, yaml
corpus = {e["TIPLOC"] for e in json.load(open("corpus.json"))["TIPLOCDATA"] if e["TIPLOC"]}
data = yaml.safe_load(open("listings.yaml"))
tiplocs = {t for l in data["listings"] for pair in l["routes"] for t in pair}
missing = sorted(tiplocs - corpus)
print("MISSING:", missing if missing else "none - all TIPLOCs valid")
EOF
```

Option B (no account): look up each station on the Open Rail Data wiki (https://wiki.openraildata.com) or realtimetrains.co.uk detailed view, which shows TIPLOCs.

Fix any TIPLOC the check flags (likely suspects: `NWCSTLE`, `SNDRLND`, `BMHMMST`, `STPX` vs `STPANCI`, `GTWK`). Verify TOC codes against https://wiki.openraildata.com/index.php/TOC_Codes (expected: GR, LD, HT, GC, VT, GW, EM, SN, GX, CH, LE, XC, TP, SE). Also confirm Southeastern Highspeed services genuinely run from `STPANCI`; if they use the domestic St Pancras TIPLOC, use that instead. Do not skip this step.

- [ ] **Step 3: Download rolling stock photos**

For each listing, find a photo on https://commons.wikimedia.org (search the class + operator, e.g. "Class 803 Lumo", "Class 390 Avanti"). Requirements: CC BY / CC BY-SA / public domain license, landscape, ≥1200px wide. Save to `trainlist/static/stock/<slug>.jpg` and set `photo_attribution` in `listings.yaml` to `"Photo: <author>, Wikimedia Commons, <license>"` exactly as the file page states. Example:

```bash
curl -L -o trainlist/static/stock/ecml-lumo.jpg \
  "https://upload.wikimedia.org/wikipedia/commons/<path-from-file-page>"
```

All 14 listings must end up with a photo file and a non-empty `photo_attribution`.

- [ ] **Step 4: Seed and visually verify**

```bash
TRAINLIST_DB=/tmp/dev.db venv/bin/python -m trainlist.seed listings.yaml
TRAINLIST_DB=/tmp/dev.db venv/bin/flask --app "trainlist.webapp:create_app()" run
```

Expected: `Seeded 14 listings into /tmp/dev.db`; the page shows 14 cards, every card has a photo, footer lists 14 photo credits.

- [ ] **Step 5: Run the full test suite**

Run: `venv/bin/pytest -v`
Expected: all pass (seed tests use their own YAML, so data changes don't break them).

- [ ] **Step 6: Commit**

```bash
git add listings.yaml trainlist/static/stock/
git commit -m "feat: full curated seed data with verified tiplocs and photos"
```

---

### Task 11: Deployment artifacts + README

**Files:**
- Create: `deploy/trainlist-web.service`, `deploy/trainlist-collector.service`, `deploy/deploy.sh`, `.env.example`, `README.md`

**Interfaces:**
- Consumes: gunicorn entrypoint `trainlist.webapp:create_app()` (Task 8), collector CLI `python -m trainlist.collector.daemon` (Task 9), seed CLI (Task 3).

- [ ] **Step 1: Create `deploy/trainlist-web.service`**

```ini
[Unit]
Description=Trainlist web (gunicorn)
After=network.target

[Service]
User=trainlist
WorkingDirectory=/opt/trainlist
EnvironmentFile=/opt/trainlist/.env
ExecStart=/opt/trainlist/venv/bin/gunicorn -w 2 -b 127.0.0.1:8000 "trainlist.webapp:create_app()"
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 2: Create `deploy/trainlist-collector.service`**

```ini
[Unit]
Description=Trainlist Darwin collector
After=network.target

[Service]
User=trainlist
WorkingDirectory=/opt/trainlist
EnvironmentFile=/opt/trainlist/.env
ExecStart=/opt/trainlist/venv/bin/python -m trainlist.collector.daemon
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 3: Create `deploy/deploy.sh`**

```bash
#!/usr/bin/env bash
set -euo pipefail

HOST=${1:?usage: deploy/deploy.sh user@host}

rsync -az --delete \
  --exclude .git --exclude venv --exclude '__pycache__' \
  --exclude '*.db' --exclude '*.db-wal' --exclude '*.db-shm' --exclude .env \
  ./ "$HOST":/opt/trainlist/

ssh "$HOST" '
  cd /opt/trainlist &&
  venv/bin/pip install -q -e . &&
  venv/bin/python -m trainlist.seed listings.yaml &&
  sudo systemctl restart trainlist-web trainlist-collector
'
echo "deployed to $HOST"
```

Then: `chmod +x deploy/deploy.sh`

- [ ] **Step 4: Create `.env.example`**

```
TRAINLIST_DB=/opt/trainlist/trainlist.db
# Darwin Push Port credentials — from your National Rail open data account
DARWIN_HOST=darwin-dist-44ae45.nationalrail.co.uk
DARWIN_PORT=61613
DARWIN_USER=
DARWIN_PASS=
DARWIN_TOPIC=/topic/darwin.pushport-v16
ON_TIME_MINUTES=5
```

- [ ] **Step 5: Create `README.md`**

```markdown
# Trainlist

UK train routes ranked by punctuality, comfort, and price — levelsio style.
One Flask app + one Darwin Push Port collector + one SQLite file, on one VPS.

## How it works

- `trainlist/collector/` subscribes to the Darwin Push Port feed (STOMP),
  matches trains to curated route+operator listings by TOC and endpoint
  TIPLOCs, and stores one row per arrival at the final destination.
- `trainlist/webapp.py` serves the ranked card grid; punctuality is aggregated
  live per time window (today / last week / last month) with a 60s cache.
- Comfort and price are hand-curated in `listings.yaml` (0–10; price 10 = cheap).
- "On time" = within 5 minutes at final destination; cancellations count as
  not on time. Percentages need ≥5 arrivals in the window.

## Darwin access

Register for the Darwin Push Port feed via National Rail's open data portal
(https://opendata.nationalrail.co.uk — "Darwin Real Time Information"). Your
account page shows the STOMP host, port, topic, and credentials; put them in
`.env` (copy `.env.example`). If your registration route only offers the Rail
Data Marketplace Kafka variant, the transport lives entirely in
`trainlist/collector/daemon.py:run()` — swap the STOMP connection for a Kafka
consumer there; parsing and storage are transport-agnostic.

## Develop

    python -m venv venv && venv/bin/pip install -e '.[dev]'
    venv/bin/pytest
    TRAINLIST_DB=/tmp/dev.db venv/bin/python -m trainlist.seed listings.yaml
    TRAINLIST_DB=/tmp/dev.db venv/bin/flask --app "trainlist.webapp:create_app()" run

## Deploy (one VPS)

    # once, on the server:
    sudo useradd -r -m -d /opt/trainlist trainlist
    sudo -u trainlist python3 -m venv /opt/trainlist/venv
    sudo cp deploy/*.service /etc/systemd/system/ && sudo systemctl daemon-reload
    sudo systemctl enable trainlist-web trainlist-collector
    # copy .env.example to /opt/trainlist/.env and fill in credentials
    # point nginx/Caddy at 127.0.0.1:8000

    # every deploy:
    deploy/deploy.sh trainlist@your-vps

Health: `GET /health` returns 503 if the collector heartbeat is >10 min stale.
```

- [ ] **Step 6: Verify shell syntax and run the suite**

```bash
bash -n deploy/deploy.sh
venv/bin/pytest -v
```

Expected: no syntax errors; all tests pass.

- [ ] **Step 7: Commit**

```bash
git add deploy/ .env.example README.md
git commit -m "feat: systemd units, deploy script, and README"
```

---

## Post-plan verification (manual, after Darwin credentials arrive)

Not a task — a go-live checklist:

1. Fill `.env` with real Darwin credentials; run the collector locally for an hour: `set -a; . ./.env; set +a; venv/bin/python -m trainlist.collector.daemon`
2. Check rows appear: `sqlite3 trainlist.db 'SELECT COUNT(*) FROM schedules; SELECT * FROM arrivals LIMIT 5;'`
3. If schedules stay at 0, log a few raw messages to disk and compare their element/attribute shapes against `tests/fixtures/*.xml`; adjust fixtures and parser together (TDD).
4. Confirm "Today" shows percentages once listings pass 5 arrivals.
