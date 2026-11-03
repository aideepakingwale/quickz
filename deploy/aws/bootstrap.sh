#!/bin/bash
# Installs and starts Quickz on a fresh Ubuntu 24.04 server. Run as root from the checked-out repo (/opt/quickz) with:
#   DOMAIN (or DUCKDNS_NAME)   SETUP_TOKEN   [DUCKDNS_TOKEN]   [BACKUP_BUCKET]   [MAX_MEDIA_MB]
#   [CDN_URL + S3_ENDPOINT + BACKUP_BUCKET: store pictures in S3 with the instance role and send phones to the CloudFront address]
# or, for the CloudFront-only setup (no domain, no Caddy; see quickz-cloudfront.yaml):
#   MODE=cloudfront   ORIGIN_SECRET   PUBLIC_URL (https://dxxxx.cloudfront.net)   SETUP_TOKEN   BACKUP_BUCKET   S3_ENDPOINT   [MAX_MEDIA_MB]
# Safe to run again: it rebuilds from the current code and restarts.
set -euxo pipefail
export DEBIAN_FRONTEND=noninteractive
APP=/opt/quickz
exec > >(tee -a /var/log/quickz-setup.log) 2>&1

# a small server needs swap, so building the image and the occasional spike never run out of memory
if ! swapon --show | grep -q /swapfile; then
  fallocate -l 1G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

apt-get update -y
apt-get install -y docker.io docker-compose-v2 git curl cron
systemctl enable --now docker
if [ -n "${BACKUP_BUCKET:-}" ] && ! command -v aws >/dev/null; then snap install aws-cli --classic; fi

MODE="${MODE:-direct}"
DOMAIN="${DOMAIN:-}"
if [ -z "$DOMAIN" ] && [ -n "${DUCKDNS_NAME:-}" ]; then DOMAIN="${DUCKDNS_NAME}.duckdns.org"; fi
if [ "$MODE" = "cloudfront" ]; then
  [ -n "${ORIGIN_SECRET:-}" ] && [ -n "${PUBLIC_URL:-}" ] || { echo "Set ORIGIN_SECRET and PUBLIC_URL"; exit 1; }
else
  [ -n "$DOMAIN" ] || { echo "Set DOMAIN or DUCKDNS_NAME"; exit 1; }
fi
SETUP_TOKEN="${SETUP_TOKEN:-$(openssl rand -hex 12)}"

mkdir -p /opt/quickz-data && chown 10001:10001 /opt/quickz-data     # the container runs as uid 10001
cd "$APP/deploy/aws"
umask 077
cat > .env <<ENV
DOMAIN=${DOMAIN}
QUICKZ_SETUP_TOKEN=${SETUP_TOKEN}
QUICKZ_MAX_MEDIA_MB=${MAX_MEDIA_MB:-50}
DUCKDNS_NAME=${DUCKDNS_NAME:-}
DUCKDNS_TOKEN=${DUCKDNS_TOKEN:-}
BACKUP_BUCKET=${BACKUP_BUCKET:-}
QUICKZ_S3_ENDPOINT=${CDN_URL:+${S3_ENDPOINT:-}}
QUICKZ_S3_BUCKET=${CDN_URL:+${BACKUP_BUCKET:-}}
QUICKZ_S3_USE_ROLE=${CDN_URL:+1}
QUICKZ_PUBLIC_BUCKET_URL=${CDN_URL:-}
ENV
if [ "$MODE" = "cloudfront" ]; then
  # every `docker compose` command run in this folder (backup.sh, update.sh, your own) now uses the CloudFront file
  sed -i '/^QUICKZ_S3_/d;/^QUICKZ_PUBLIC_BUCKET_URL/d' .env
  cat >> .env <<ENV
COMPOSE_FILE=docker-compose.cloudfront.yml
QUICKZ_ORIGIN_SECRET=${ORIGIN_SECRET}
QUICKZ_PUBLIC_URL=${PUBLIC_URL}
QUICKZ_S3_ENDPOINT=${S3_ENDPOINT:-}
QUICKZ_S3_BUCKET=${BACKUP_BUCKET:-}
QUICKZ_S3_USE_ROLE=1
ENV
fi
chmod +x update-dns.sh backup.sh

./update-dns.sh || echo "(DNS update skipped or failed: check DUCKDNS_NAME / DUCKDNS_TOKEN if you rely on it)"

docker compose up -d --build

# every boot: re-point the name at the new public IP.  every stop: back up first.  every 6 hours: back up.
cat > /etc/systemd/system/quickz-dns.service <<UNIT
[Unit]
Description=Point the DNS name at this server's current IP
After=network-online.target
Wants=network-online.target
Before=docker.service

[Service]
Type=oneshot
ExecStart=$APP/deploy/aws/update-dns.sh

[Install]
WantedBy=multi-user.target
UNIT
cat > /etc/systemd/system/quickz-backup-on-stop.service <<UNIT
[Unit]
Description=Back up Quickz when the server stops
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/true
ExecStop=$APP/deploy/aws/backup.sh
TimeoutStopSec=90

[Install]
WantedBy=multi-user.target
UNIT
echo "17 */6 * * * root $APP/deploy/aws/backup.sh >> /var/log/quickz-backup.log 2>&1" > /etc/cron.d/quickz-backup
systemctl daemon-reload
systemctl enable quickz-dns.service quickz-backup-on-stop.service
systemctl start quickz-backup-on-stop.service

echo
if [ "$MODE" = "cloudfront" ]; then
  echo "DiQuizBuzz is up behind CloudFront: ${PUBLIC_URL}/host   setup token: ${SETUP_TOKEN}"
else
  echo "DiQuizBuzz is up: https://${DOMAIN}/host   setup token: ${SETUP_TOKEN}"
fi
