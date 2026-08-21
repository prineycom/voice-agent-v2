# syntax=docker/dockerfile:1.7
# The explicit stand preparation command builds this pinned context natively.
# Runtime consumes only its privately recorded immutable local image ID.
ARG BASE_IMAGE=python:3.13-alpine@sha256:2b5c902e7a9e3348f78e17816561e8785f6c3ca7bfedc55d6e47f2c922c5b176
FROM ${BASE_IMAGE}
ARG VOICE_AGENT_IMAGE_CONTEXT_REVISION
LABEL io.priney.voice-agent-v2.image-managed="1" \
      io.priney.voice-agent-v2.image-schema="1" \
      io.priney.voice-agent-v2.image-context="${VOICE_AGENT_IMAGE_CONTEXT_REVISION}"
RUN apk add --no-cache ca-certificates curl git openssh-client patch ripgrep sudo tar tini unzip xz zip \
    && addgroup -g 1000 agent && adduser -D -u 1000 -G agent agent \
    && printf 'agent ALL=(ALL) NOPASSWD: ALL\\nDefaults:agent !setenv\\n' > /etc/sudoers.d/agent \
    && chmod 0440 /etc/sudoers.d/agent \
    && install -d -m 0700 -o agent -g agent /workspace /cache \
       /var/lib/voice-agent/exec-ledger /var/lib/voice-agent/stream-ledger \
       /var/lib/voice-agent/process-ledger /var/lib/voice-agent/credentials
COPY --chmod=0555 helpers/agent-helper /usr/local/lib/voice-agent/agent-helper
USER 1000:1000
WORKDIR /workspace
ENTRYPOINT ["/sbin/tini","--"]
CMD ["/usr/local/lib/voice-agent/agent-helper","init-container"]
