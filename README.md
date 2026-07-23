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
