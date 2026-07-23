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
