#!/bin/sh
set -eu
mkdir -p deploy/backups
chmod 700 deploy/backups
umask 077
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
remote_dir="/data/.fieldhouse-backup-$stamp"
local_dir="deploy/backups/fieldhouse-$stamp"
docker compose exec -T app python -c \
  "import sqlite3
from pathlib import Path
destination = Path('$remote_dir')
destination.mkdir(mode=0o700)
sources = [path for path in [Path('/data/harness.sqlite3')] if path.is_file()]
sources += sorted(Path('/data/teams').glob('team-*.sqlite3')) if Path('/data/teams').is_dir() else []
if not sources:
    raise SystemExit('No Fieldhouse databases were found')
for source_path in sources:
    target_path = destination / source_path.name
    source = sqlite3.connect(source_path)
    target = sqlite3.connect(target_path)
    source.backup(target)
    target.close()
    source.close()"
container_id="$(docker compose ps -q app)"
mkdir -m 700 "$local_dir"
docker cp "$container_id:$remote_dir/." "$local_dir"
docker compose exec -T app rm -rf "$remote_dir"
chmod 600 "$local_dir"/*.sqlite3
echo "Created $local_dir"
