# syntax=docker/dockerfile:1.7
# The release pipeline resolves this native base per image-lock.v1.json and
# publishes the locked amd64/arm64 OCI index. Runtime pulling is forbidden.
ARG BASE_IMAGE=python:3.13-alpine@sha256:2b5c902e7a9e3348f78e17816561e8785f6c3ca7bfedc55d6e47f2c922c5b176
FROM ${BASE_IMAGE}
RUN apk add --no-cache git patch ripgrep sudo tar tini unzip xz zip \
    && addgroup -g 1000 agent && adduser -D -u 1000 -G agent agent \
    && printf 'agent ALL=(ALL) NOPASSWD: ALL\\nDefaults:agent !setenv\\n' > /etc/sudoers.d/agent \
    && chmod 0440 /etc/sudoers.d/agent \
    && install -d -m 0700 -o agent -g agent /workspace /cache /var/lib/voice-agent/exec-ledger
COPY --chmod=0555 helpers/agent-helper /usr/local/lib/voice-agent/agent-helper
USER 1000:1000
WORKDIR /workspace
ENTRYPOINT ["/sbin/tini","--"]
CMD ["sleep","infinity"]
