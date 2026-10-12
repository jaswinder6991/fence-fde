#!/usr/bin/env bash
set -euo pipefail

_DIR=$(dirname "$0")
_HERE=$(cd "$_DIR" && pwd)
ROOT="$(cd "$_HERE/../../../.." && pwd)"
cd "$ROOT"

docker compose exec -T db pg_isready -U fence -d fence >/dev/null

exists="$(docker compose exec -T db psql -U fence -d fence -tAc \
  "SELECT 1 FROM pg_database WHERE datname='fence_verify'")"

if [[ "${exists//[[:space:]]/}" != "1" ]]; then
  docker compose exec -T db psql -U fence -d fence -c "CREATE DATABASE fence_verify;"
  echo "created database fence_verify"
else
  echo "database fence_verify already exists"
fi
