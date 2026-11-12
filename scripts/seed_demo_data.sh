#!/usr/bin/env bash
# Seed the MongoDB demo collection once, using a setup user.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DB_HOST="${DB_HOST:-localhost}"
DB_PORT="${DB_PORT:-27017}"
DB_NAME="${DB_NAME:-demo}"
DB_USER="${DB_USER:-demo_owner}"
DB_AUTH_SOURCE="${DB_AUTH_SOURCE:-$DB_NAME}"
# mongosh prompts for the password; it is not stored in the script.
mongosh --host "$DB_HOST" --port "$DB_PORT" --username "$DB_USER" \
  --authenticationDatabase "$DB_AUTH_SOURCE" "$DB_NAME" "$ROOT/scripts/seed_demo_data.js"
