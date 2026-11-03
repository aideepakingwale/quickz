#!/bin/bash
# Update a running server to the latest code on its branch. Keeps the data, the certificate and the settings in .env.
#   sudo bash /opt/quickz/deploy/aws/update.sh
set -euo pipefail
cd /opt/quickz
git pull --ff-only
cd deploy/aws
./backup.sh || echo "(backup skipped)"
docker compose up -d --build
docker image prune -f >/dev/null
docker compose ps
