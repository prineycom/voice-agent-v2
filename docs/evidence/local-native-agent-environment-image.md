# Deterministic evidence — native local AgentEnvironment image

**Tier:** deterministic PR evidence only
**Command:** `./verify`

## Contract

- `agent-environment/image-lock.v2.json` pins the base by digest and exact Dockerfile/helper bytes and original modes. The derived context revision is independent of a mutable Docker tag.
- `stand agent-image prepare` requires those original modes, rootless Docker, selects only the host-native `linux/amd64` or `linux/arm64` platform, invokes fixed `docker buildx build --load`, and freshly verifies exact image ID, platform, labels/context, user, entrypoint/helper command and rootfs presence.
- Runtime consumes the same locked bytes/spec/revision from canonical source or from only the complete `stand_dev` immutable-release `0444`/`0555` transform. Partial/other modes, changed bytes/lock/revision, or fresh image-inspect mismatch fail before a container mutation.
- Private installation state atomically selects that exact ID. Unchanged preparation is idempotent; changed locked context retains the prior ID and never removes or prunes.
- Runtime accepts only the selected exact ID and freshly inspects it before resolution/reuse through the exact current-user rootless Unix socket derived from effective UID. Its Docker client retains an empty private home/config, explicitly sets only that endpoint, and rejects ambient context/host/config/credentials plus missing, foreign, replaced, remote, system/rootful, incompatible, or changed endpoint/daemon custody without fallback. Endpoint failure stays content-free and distinct from actual prepared-image absence. Container inspection must report the same image ID; runtime contains no build, login, pull, push, publish, fallback or repair route.
- Main/dev share the prepared immutable ID only. Their registries, containers, rootfs, workspace, cache, credentials and other mutable state remain instance-private.

## Deterministic coverage

`tests.test_agent_environment_image` covers exact prepare-time context admission, rootless native platform, one fixed build, idempotency, context-change retention, missing/stale/mismatch states, and absence of remote/destructive commands. `DockerCLICustodyTests` proves the private empty client carries only the exact derived same-user Unix endpoint despite ambient system/remote/context/credential values; validates rootless daemon custody; progresses a prepared-image status to `absent`; and rejects missing, foreign, replaced, rootful or changed daemon/socket identity before requested mutation with content/path-safe reasons and no retry/fallback. The end-to-end AgentEnvironment boundary prepares through fake Docker, applies the actual immutable-release transform, loads an instance V2 profile, creates through the exact prepared selection, and keeps unexpected/mixed modes, bytes, revision and inspect mismatches pre-mutation. Existing AgentEnvironment and stand tests retain runtime fresh inspect, missing-preparation isolation, no runtime image mutation, strict configuration, shared image root and instance-private mutable roots.

## Separate evidence

The deterministic fake-Docker tier does not claim a real rootless build, base acquisition, BuildKit/storage behavior, real image/container inspection, Linux/macOS parity, physical voice, lifecycle persistence, reboot or capacity. Issue #71 follow-up acceptance must prepare the real native image and exercise the same exact container across one documented dev stop/start.
