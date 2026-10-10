# BugKB

A local knowledge base for bugs, measured performance improvements, and environment gotchas. Agents search and record findings through a Python CLI; humans browse and curate entries in the PocketBase dashboard.

[![Docker pulls](https://img.shields.io/docker/pulls/sgkens/bugkb)](https://hub.docker.com/r/sgkens/bugkb)
[![MIT license](https://img.shields.io/badge/license-MIT-green)](https://gitlab.com/sgkens/bugkb/-/blob/main/LICENSE)

## Quick start

```sh
docker volume create bugkb-data
docker run -d --name bugkb --restart unless-stopped \
  --security-opt no-new-privileges:true --cap-drop ALL \
  -p 127.0.0.1:8090:8090 \
  -v bugkb-data:/pb/pb_data \
  sgkens/bugkb:latest
```

Open `http://127.0.0.1:8090/_/` for the dashboard. Create a dashboard superuser:

```sh
docker exec -it bugkb /pb/pocketbase superuser upsert you@local.dev '<strong-password>' --dir=/pb/pb_data
```

Replace both placeholders. For unattended setup, supply both `BUGKB_SUPERUSER_EMAIL` and `BUGKB_SUPERUSER_PASSWORD` as environment variables; these create or update the account on every start. Keep their values out of source control.

For production, replace `latest` with a published version tag or digest. SHA tags identify the tested Git commit; `X.Y.Z` tags come from protected GitLab releases. The CI-published image currently supports **linux/amd64**. The source Dockerfile also supports building linux/arm64.

## Local access only

The knowledge-base API allows unauthenticated reads and writes for trusted local agents. Keep the published port on `127.0.0.1`. Dashboard authentication does not protect the KB API. Remote or internet-facing use requires an authentication boundary.

## Features

- Full-text search with stemming, traceback term selection, and trust-aware ranking.
- Server-side validation, duplicate detection, protected trust fields, and fix history.
- Transactional confirmations that preserve concurrent updates and verified environment versions.
- Review queue for human curation; deletion requires dashboard superuser access.
- Codex, Claude Code, and Antigravity skill bundles with a Python 3.8+ standard-library CLI.
- Persistent SQLite data in `/pb/pb_data`, a non-root default user (UID/GID 10001), and a healthcheck that verifies the KB hooks loaded.

## Configuration and storage

| Setting | Default / purpose |
| --- | --- |
| Container port | `8090` |
| Data directory | `/pb/pb_data` |
| `TZ` | Optional timezone, for example `Australia/Sydney` |
| `BUGKB_SUPERUSER_EMAIL` | Optional dashboard account email; set with password |
| `BUGKB_SUPERUSER_PASSWORD` | Optional dashboard account password; minimum 8 characters |
| Client `KB_URL` | `http://127.0.0.1:8090` |
| Client `KB_PROJECT` | Override the automatically detected project |

Named volumes initialize with the image's data-directory ownership. For host bind mounts, create the directory yourself and pass `--user "$(id -u):$(id -g)"` to match its owner. Avoid SQLite data on Windows-mounted paths from WSL; use a Linux filesystem or Docker volume.

Use the dashboard's Settings → Backups, or stop the container before copying the entire data directory. Test backup restoration before relying on it. Removing the container preserves the named volume; deleting the volume destroys the knowledge base.

Source, skill installation, Compose setup and release instructions: [BugKB on GitLab](https://gitlab.com/sgkens/bugkb).

BugKB and PocketBase are MIT licensed.
