# Deterministic evidence — native local AgentEnvironment image

**Tier:** deterministic PR evidence only
**Command:** `./verify`

## Contract

- `agent-environment/image-lock.v2.json` pins the base by digest and exact Dockerfile/helper bytes and modes. The derived context revision is independent of a mutable Docker tag.
- `stand agent-image prepare` requires rootless Docker, selects only the host-native `linux/amd64` or `linux/arm64` platform, invokes fixed `docker buildx build --load`, and freshly verifies exact image ID, platform, labels/context, user, entrypoint/helper command and rootfs presence.
- Private installation state atomically selects that exact ID. Unchanged preparation is idempotent; changed locked context retains the prior ID and never removes or prunes.
- Runtime accepts only the selected exact ID, freshly inspects it before resolution/reuse, and requires container inspection to report the same ID. It contains no build, login, pull, push, publish, fallback or repair route.
- Main/dev share the prepared immutable ID only. Their registries, containers, rootfs, workspace, cache, credentials and other mutable state remain instance-private.

## Deterministic coverage

`tests.test_agent_environment_image` covers exact context admission, rootless native platform, one fixed build, idempotency, context-change retention, missing/stale/mismatch states, and absence of remote/destructive commands. AgentEnvironment and stand tests cover runtime fresh inspect, missing-preparation isolation, no runtime image mutation, strict configuration, shared image root and instance-private mutable roots.

## Separate evidence

The deterministic fake-Docker tier does not claim a real rootless build, base acquisition, BuildKit/storage behavior, real image/container inspection, Linux/macOS parity, physical voice, lifecycle persistence, reboot or capacity. Issue #71 follow-up acceptance must prepare the real native image and exercise the same exact container across one documented dev stop/start.
