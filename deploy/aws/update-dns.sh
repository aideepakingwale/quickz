#!/bin/bash
# Point the DuckDNS name at this instance's CURRENT public IP. Runs at every boot, because a stopped-and-started
# instance gets a new address. Does nothing unless DUCKDNS_NAME and DUCKDNS_TOKEN are set.
set -euo pipefail
cd "$(dirname "$0")"
set -a; . ./.env; set +a
[ -n "${DUCKDNS_NAME:-}" ] && [ -n "${DUCKDNS_TOKEN:-}" ] || exit 0
TOKEN=$(curl -fsS -X PUT http://169.254.169.254/latest/api/token -H 'X-aws-ec2-metadata-token-ttl-seconds: 60')
IP=$(curl -fsS -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/public-ipv4)
for _ in 1 2 3 4 5 6; do
  if [ "$(curl -fsS "https://www.duckdns.org/update?domains=${DUCKDNS_NAME}&token=${DUCKDNS_TOKEN}&ip=${IP}")" = "OK" ]; then
    echo "DuckDNS ${DUCKDNS_NAME}.duckdns.org -> ${IP}"; exit 0
  fi
  sleep 5
done
echo "DuckDNS update failed" >&2; exit 1
