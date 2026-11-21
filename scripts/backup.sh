#!/usr/bin/env bash
# Writes a consistent copy of the database and alert key to ./backups. Run from the project folder.
set -euo pipefail
mkdir -p backups
stamp=$(date +%Y%m%d-%H%M%S)
docker compose exec -T app python - <<'PY'
import sqlite3
src = sqlite3.connect("/data/innerface.db"); dst = sqlite3.connect("/tmp/backup.db")
with dst:
    src.backup(dst)
PY
# /tmp in the container is a memory disk, which "docker compose cp" can't read, so the files are streamed out instead.
umask 077
docker compose exec -T app cat /tmp/backup.db > "backups/innerface-$stamp.db"
docker compose exec -T app cat /data/vapid_private.pem > "backups/vapid-$stamp.pem"
docker compose exec -T app rm -f /tmp/backup.db
test -s "backups/innerface-$stamp.db" && test -s "backups/vapid-$stamp.pem"
echo "Saved backups/innerface-$stamp.db"
