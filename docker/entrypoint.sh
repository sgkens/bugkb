#!/bin/sh
# Optionally creates/updates the dashboard superuser, then runs PocketBase in the foreground.
#   BUGKB_SUPERUSER_EMAIL  BUGKB_SUPERUSER_PASSWORD   (both needed, password 8+ chars, email needs a dot in the domain)
set -eu

PB=/pb/pocketbase
DIR=/pb/pb_data

if { [ -n "${BUGKB_SUPERUSER_EMAIL:-}" ] && [ -z "${BUGKB_SUPERUSER_PASSWORD:-}" ]; } || \
   { [ -z "${BUGKB_SUPERUSER_EMAIL:-}" ] && [ -n "${BUGKB_SUPERUSER_PASSWORD:-}" ]; }; then
  echo "Set both BUGKB_SUPERUSER_EMAIL and BUGKB_SUPERUSER_PASSWORD, or neither." >&2
  exit 1
fi

if [ -n "${BUGKB_SUPERUSER_EMAIL:-}" ] && [ -n "${BUGKB_SUPERUSER_PASSWORD:-}" ]; then
  "$PB" superuser upsert "$BUGKB_SUPERUSER_EMAIL" "$BUGKB_SUPERUSER_PASSWORD" \
    --dir="$DIR" --migrationsDir=/pb/pb_migrations
fi

exec "$PB" serve \
  --http=0.0.0.0:8090 \
  --dir="$DIR" \
  --migrationsDir=/pb/pb_migrations \
  --hooksDir=/pb/pb_hooks
