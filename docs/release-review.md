# Production readiness review

Reviewed 2026-10-11 against PocketBase 0.40.4. Intended deployment: trusted local agents, with the API published only on loopback.

## Fixed before release

- Excluded `.env` and environment variants from both Git and the Docker build context. Removed example bootstrap credentials so an unchanged example does not create a predictable dashboard login.
- Upgraded the container base from Alpine 3.20 to supported Alpine 3.23.
- Applied credential checks to JSON metadata and confirmation state transitions. JSON keys such as `"token": "..."` now match the credential heuristic. A rejected confirmation rolls back its trust changes.
- Required numeric, finite before/after values for performance entries; rejected non-object confirmation environments.
- Set non-superuser record sources to `agent`, preventing creation with a forged `human` source.
- Required both optional dashboard bootstrap variables together so partial setup fails visibly.
- Changed native health detection to require the KB hooks, corrected IPv6 probe URLs, and restricted executable discovery to the actual PocketBase filename.
- Corrected the README's indexing and bind-mount deletion claims. Record commits and FTS updates are separate; Docker Compose volume deletion does not delete host bind directories.
- Added an MIT license file to match the existing declared license and included both BugKB and PocketBase license notices in the image.

## Verification

Ten integration tests passed against a native disposable PocketBase instance and the built Linux amd64 container. They cover CLI connectivity, create validation, trust locks, delete protection, duplicate refusal, searchable indexing, fix history, secret rollback, obsolete/revive behavior, and twelve concurrent confirmations without lost increments. The image test also checks persisted records and index counts after restart and waits for Docker's healthcheck to report healthy.

All three platform skills passed frontmatter validation. CI and Compose YAML were parsed locally. Shell scripts passed syntax checks. The GitLab pipeline itself still needs its first run on the configured runner; local YAML parsing is not GitLab server-side CI linting.

## Operational constraints

- The API intentionally permits unauthenticated reads and writes. This release is suitable for trusted local use; exposing it to an untrusted network requires authentication outside BugKB. Confirmed status is a recurrence heuristic, not an authenticated endorsement.
- Credential detection is a heuristic. Redact secrets before submission; the server cannot recognize every credential format.
- FTS updates run after record commits. Count mismatches recover on the next periodic check, but same-count drift requires an explicit rebuild. Concurrent near-identical creates can also pass duplicate detection before either commits; duplicate refusal is a curation aid, not a uniqueness guarantee.
- Use a Linux filesystem or Docker volume for SQLite, especially under WSL. Stop the service for file-copy backups and validate restores.
- CI publishes only amd64 initially. The Dockerfile's arm64 path needs equivalent runtime verification before adding a production arm64 manifest.
- Protect `main` and release tags; configure a `linux-shell` runner and masked/protected Docker Hub credentials. Publishing is not verified until the real GitLab pipeline runs.
