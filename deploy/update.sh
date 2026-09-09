#!/bin/sh
set -eu
docker compose config --quiet
docker compose up -d --build --remove-orphans
docker image prune -f
docker compose ps
