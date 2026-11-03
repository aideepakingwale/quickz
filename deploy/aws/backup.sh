#!/bin/bash
# A consistent copy of the database (safe during a game); the newest 14 are kept on the disk. If BACKUP_BUCKET is set the
# newest one and the uploaded pictures/clips are also copied to S3 with the instance's IAM role (no access keys stored).
set -euo pipefail
cd "$(dirname "$0")"
set -a; . ./.env; set +a
docker compose exec -T quickz python -m quickz backup "/data/backups/quickz-$(date +%Y%m%d-%H%M%S).db.gz" >/dev/null
ls -1t /opt/quickz-data/backups/*.db.gz | tail -n +15 | xargs -r rm -f
if [ -n "${BACKUP_BUCKET:-}" ]; then
  latest=$(ls -1t /opt/quickz-data/backups/*.db.gz | head -1)
  aws s3 cp "$latest" "s3://${BACKUP_BUCKET}/db/latest.db.gz" --only-show-errors
  aws s3 cp "$latest" "s3://${BACKUP_BUCKET}/db/daily/$(date +%F).db.gz" --only-show-errors
  if [ -d /opt/quickz-data/uploads ]; then aws s3 sync /opt/quickz-data/uploads "s3://${BACKUP_BUCKET}/uploads" --only-show-errors; fi
fi
echo "backup ok $(date -Is)"
