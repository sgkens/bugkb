#!/bin/sh
# Uses a disposable Docker volume; never mounts application or user data.
set -eu
image=${1:?Usage: scripts/test-image.sh IMAGE}
name="bugkb-test-${CI_JOB_ID:-$$}"
volume="$name-data"
cleanup() {
  docker rm -f "$name" >/dev/null 2>&1 || true
  docker volume rm "$volume" >/dev/null 2>&1 || true
}
trap cleanup EXIT
trap 'exit 1' INT TERM
docker volume create "$volume" >/dev/null
docker run -d --name "$name" -p 127.0.0.1::8090 -v "$volume:/pb/pb_data" "$image" >/dev/null
port=$(docker inspect --format '{{(index (index .NetworkSettings.Ports "8090/tcp") 0).HostPort}}' "$name")
export KB_TEST_URL="http://127.0.0.1:$port"
if ! python3 -m unittest discover -s tests -v; then
  docker logs "$name"
  exit 1
fi
# Confirm persistent storage survives restart and the built-in healthcheck passes.
docker restart "$name" >/dev/null
# Docker may assign a new ephemeral host port when a container restarts.
port=$(docker inspect --format '{{(index (index .NetworkSettings.Ports "8090/tcp") 0).HostPort}}' "$name")
export KB_TEST_URL="http://127.0.0.1:$port"
attempt=0
until [ "$(docker inspect --format '{{.State.Health.Status}}' "$name")" = healthy ]; do
  attempt=$((attempt + 1))
  if [ "$attempt" -ge 45 ]; then docker logs "$name"; exit 1; fi
  sleep 1
done
python3 - <<'PY'
import json, os, urllib.request
with urllib.request.urlopen(os.environ['KB_TEST_URL'] + '/api/kb/info', timeout=10) as response:
    info = json.load(response)
assert info['entries'] > 0 and info['entries'] == info['indexed'], info
print('Restart persistence and container health passed:', info)
PY
