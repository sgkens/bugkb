# syntax=docker/dockerfile:1
# bugkb server image: PocketBase + the KB hooks and migrations. Multi-arch (amd64, arm64) via TARGETARCH.

FROM alpine:3.23 AS fetch
ARG PB_VERSION=0.40.4
ARG TARGETARCH
RUN apk add --no-cache ca-certificates unzip wget \
 && wget -q -O /tmp/pb.zip \
      "https://github.com/pocketbase/pocketbase/releases/download/v${PB_VERSION}/pocketbase_${PB_VERSION}_linux_${TARGETARCH}.zip" \
 && unzip -q /tmp/pb.zip pocketbase LICENSE.md -d /out \
 && chmod 0755 /out/pocketbase

FROM alpine:3.23
RUN apk add --no-cache ca-certificates tzdata \
 && addgroup -S -g 10001 bugkb && adduser -S -u 10001 -G bugkb bugkb \
 && mkdir -p /pb/pb_data && chown bugkb:bugkb /pb/pb_data

COPY --from=fetch /out/pocketbase /pb/pocketbase
COPY --from=fetch /out/LICENSE.md /pb/LICENSE.pocketbase
COPY LICENSE /pb/LICENSE
COPY pb_migrations /pb/pb_migrations
COPY pb_hooks /pb/pb_hooks
COPY docker/entrypoint.sh /pb/entrypoint.sh
RUN chmod 0755 /pb/entrypoint.sh

LABEL org.opencontainers.image.title="BugKB" \
      org.opencontainers.image.description="Local knowledge base for bugs, performance findings and environment gotchas" \
      org.opencontainers.image.source="https://gitlab.com/sgkens/bugkb" \
      org.opencontainers.image.licenses="MIT"

USER bugkb
WORKDIR /pb
VOLUME /pb/pb_data
EXPOSE 8090

# /api/kb/info proves the KB hooks loaded (not just that PocketBase is up) and triggers the search-index check.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD wget -q -O /dev/null http://127.0.0.1:8090/api/kb/info || exit 1

ENTRYPOINT ["/pb/entrypoint.sh"]
