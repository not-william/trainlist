# Trainlist — Design

**Date:** 2026-07-23
**Status:** Approved

## What it is

A levelsio-style (Nomad List / Airline List) single-page site ranking UK train
routes. Each listing is a **route + operator** combination — e.g. the East
Coast Main Line has separate listings for LNER and Lumo. Listings are shown as
cards in a grid with a rolling stock photo, a rank badge, and three scores:
punctuality, comfort, and price. The list is sorted by punctuality by default,
and punctuality can be filtered by time window: **Today / Last Week / Last
Month**, via clickable boxes at the top.

Punctuality data is collected by us, from the live Darwin Push Port feed,
stored raw and aggregated on demand. Comfort and price are curated seed data.

## Decisions made

| Decision | Choice |
|---|---|
| Punctuality source | Darwin Push Port (via Rail Data Marketplace registration); we run our own always-on collector |
| Comfort & price | Curated seed data, hand-scored per listing, stored in `listings.yaml` |
| Coverage (v1) | Major mainlines, ~15–25 listings: ECML (LNER, Lumo, Grand Central, Hull Trains), WCML (Avanti), GWML (GWR), MML (EMR), Brighton ML, Chiltern, Great Eastern, CrossCountry, TransPennine |
| Stack | Python Flask + SQLite on a single VPS |
| Aggregation | SQL over raw arrival rows at request time, ~60s in-memory cache |
| "On time" | Actual arrival at final destination within 5 minutes of scheduled (configurable); cancellations count as not on time |
| Timezone | All service dates and windows computed in `Europe/London` |

## Architecture

Two processes on one VPS, sharing one SQLite database (WAL mode):

```
Darwin Push Port ──> collector daemon ──> SQLite (arrivals) ──> Flask ──> browser
                                            ^
                            listings.yaml ──┘ (seed: routes, scores, photos)
```

### Collector (`collector/`)

A standalone Python daemon that:

1. Connects to the Darwin Push Port stream and subscribes to train status
   updates.
2. Parses each XML message defensively; malformed messages are logged and
   skipped, never crash the daemon.
3. Matches each train against tracked listings by **TOC code + origin→destination
   CRS pair**. Non-matching trains are dropped immediately.
4. On an actual arrival at the train's final destination, upserts one row into
   `arrivals` (unique on Darwin RID, so re-delivered messages don't
   double-count). Cancellation messages set the `cancelled` flag.
5. Writes a heartbeat timestamp on every processed message.

Resilience: exponential-backoff reconnect; systemd `Restart=always`; missed
data while down is an accepted gap in v1 (HSP backfill is a possible later
addition).

### Data model (SQLite)

- **`listings`** — id, route name, operator name, TOC code, photo filename,
  photo attribution, comfort score (0–10), price score (0–10, 10 = cheap),
  blurb.
- **`listing_routes`** — listing id, origin CRS, destination CRS. A listing may
  have several qualifying endpoint pairs.
- **`arrivals`** — Darwin RID (unique), listing id, service date
  (Europe/London), scheduled arrival, actual arrival, delay minutes, cancelled
  flag.

`listings` and `listing_routes` are loaded from `listings.yaml` by a seed
command; adding a route is editing YAML, not code.

### Web app (`app/`)

Flask + Jinja, server-rendered, one page. Gunicorn behind nginx/Caddy.
Punctuality per listing is one GROUP BY over `arrivals` for the selected
window, cached in memory ~60 seconds.

## UI

Single page, query-param driven so every view is a shareable URL
(`/?window=week&sort=comfort`):

- **Header** — site name, tagline, "last updated" from the newest arrival.
- **Filter boxes** — three big clickable boxes: Today · Last Week · Last
  Month. Default: Today. Active box highlighted.
- **Sort row** — Punctuality (default) · Comfort · Price.
- **Card grid** — responsive CSS grid. Each card: rolling stock photo, rank
  badge (#1, #2…), route name + operator, then:
  - 🕐 Punctuality — "**94%** on time" + thin bar (changes with window)
  - 💺 Comfort — n/10 + bar (static, curated)
  - 💷 Price — n/10 + bar (static, curated)
- **Not-enough-data rule** — fewer than 5 arrivals in the window (e.g. Today
  at 6am) renders "not enough data yet" instead of a percentage; such cards
  sort to the bottom when sorting by punctuality.
- **Footer** — Darwin/National Rail credit, Wikimedia Commons photo
  attributions, the on-time definition.

Style: dense, bold, slightly playful — big numbers, emoji, rounded cards with
hover lift, plain hand-written CSS, small amount of vanilla JS.

Photos: sourced once from Wikimedia Commons into `static/stock/`, attribution
stored per listing.

## Ops

- Two systemd units: `trainlist-web` (gunicorn), `trainlist-collector`.
- Secrets (Darwin credentials) in an env file loaded by systemd.
- `deploy.sh` rsyncs and restarts; no containers.
- `/health` endpoint reports collector heartbeat staleness; the visible "last
  updated" timestamp doubles as a human health check.

## Testing (pytest)

- **Parser** — captured real Darwin XML samples in the repo: arrival,
  cancellation, malformed.
- **Matcher** — TOC/endpoint fixtures, including trains that must NOT match.
- **Aggregation** — fixture DB with known arrivals; assert exact percentages,
  window boundaries in Europe/London (including BST/GMT transitions), the
  5-arrival minimum, and cancellation counting.
- **Routes** — Flask test client: sort orders, window params, not-enough-data
  rendering.

## Out of scope (v1)

- User voting on scores
- Automatic price derivation from fares data
- HSP backfill of collector downtime
- Cancellation percentage display (data is stored, display later)
- Per-station or intermediate-stop punctuality (destination only)
