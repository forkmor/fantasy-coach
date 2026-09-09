#!/bin/sh
set -eu

if [ "$(id -u)" -eq 0 ]; then
  echo "Run this script as the non-root deployment user with sudo access." >&2
  exit 1
fi
if [ ! -f .env ]; then
  echo "Missing .env. Run: python3 deploy/generate-config.py" >&2
  exit 1
fi
if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is not installed. Install Docker Engine from Docker's official Ubuntu repository, then rerun." >&2
  exit 1
fi
docker compose version >/dev/null
docker compose config --quiet
docker compose up -d --build
docker compose ps
echo "Fieldhouse is starting. DNS must point fantasy-coach.tech to this VPS for HTTPS issuance."
