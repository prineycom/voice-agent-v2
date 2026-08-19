'use strict';

// Installation-manager custody for the optional, already-existing Docker
// AgentEnvironment.  This module is deliberately inspection-only: it cannot
// create, start, stop, remove, commit, pull, prune, or rebuild a container.
module.exports = function createAgentEnvironmentPreserver(core) {
  const crypto = require('node:crypto');
  const fs = require('node:fs');
  const os = require('node:os');
  const path = require('node:path');
  const { spawnSync } = require('node:child_process');

  const ENDPOINT_SCHEMA = 'voice-agent.docker-endpoint.v1';
  const PRESERVATION_SCHEMA = 'voice-agent.agent-environment-preservation.v1';
  const MIGRATION_SCHEMA = 'voice-agent.agent-environment-migration.v1';
  const CONTAINER_ID = /^[a-f0-9]{64}$/;
  const SHA256 = /^[a-f0-9]{64}$/;
  const IMAGE = /^sha256:[a-f0-9]{64}$/;
  const ALLOWED_IMAGE_DIGEST = `sha256:${'8a5a972b25f7c203c71b8e28af17f756d9daf39bc74ebbc5d86eaf6c9f3da421'}`;
  const RUNTIME_SPEC = /^[a-z2-7]{52}$/;
  const MANAGED_LABEL = 'io.priney.voice-agent-v2.managed';
  const SCHEMA_LABEL = 'io.priney.voice-agent-v2.schema';
  const OWNER_LABEL = 'io.priney.voice-agent-v2.owner';
  const SPEC_LABEL = 'io.priney.voice-agent-v2.spec';
  const GENERATION_LABEL = 'io.priney.voice-agent-v2.generation';
  const STATES = new Set(['disabled', 'ready', 'degraded_endpoint_unavailable', 'degraded_identity_mismatch', 'stale_spec']);
  const ACTIONS = new Set(['none', 'configure_agent', 'restore_rootless_endpoint', 'restore_exact_environment', 'maintenance_rebuild_required']);

  function error(code, message) { throw new core.LauncherError(code, message); }
  function digest(value) { return crypto.createHash('sha256').update(value).digest('hex'); }
  function exists(filename) { try { fs.lstatSync(filename); return true; } catch (reason) { if (reason.code === 'ENOENT') return false; throw reason; } }
  function syncDirectory(directory) { const fd = fs.openSync(directory, fs.constants.O_RDONLY | fs.constants.O_DIRECTORY); try { fs.fsyncSync(fd); } finally { fs.closeSync(fd); } }
  function cleanPath(value) { return typeof value === 'string' && path.isAbsolute(value) && path.normalize(value) === value && !value.includes('\0'); }

  function endpointFor(uid) { return `unix:///run/user/${uid}/docker.sock`; }

  function endpointRecord(uid, facts = null) {
    const expected = endpointFor(uid);
    if (!facts) return {
      schema: ENDPOINT_SCHEMA, endpoint: null, kind: 'unavailable', ownership_verified: false,
      socket_identity: null, daemon_identity: null, rootless: false, user_namespace: null,
      cgroup_version: null, cgroup_driver: null, explicit_host: false,
    };
    const socketIdentity = facts.socket_identity || (Number.isSafeInteger(facts.socket_device) && Number.isSafeInteger(facts.socket_inode)
      ? `${facts.socket_device}:${facts.socket_inode}` : null);
    const valid = facts.endpoint === expected && facts.socket_path === `/run/user/${uid}/docker.sock`
      && facts.socket_type === 'socket' && facts.socket_uid === uid && facts.socket_mode === 0o600
      && typeof socketIdentity === 'string' && socketIdentity.length > 0
      && facts.explicit_host === true && facts.ambient_context_used === false
      && facts.rootless === true && facts.daemon_uid === uid
      && typeof facts.daemon_identity === 'string' && facts.daemon_identity.length > 0 && facts.daemon_identity.length <= 256
      && facts.user_namespace === 'rootless' && facts.cgroup_version === 2 && facts.cgroup_driver === 'systemd'
      && facts.service_endpoint === expected;
    if (!valid) error('docker_authority_invalid', 'rootful, foreign, group-authorized, ambient, mismatched, or unverifiable Docker authority was refused');
    return {
      schema: ENDPOINT_SCHEMA, endpoint: expected, kind: 'rootless', ownership_verified: true,
      socket_identity: socketIdentity, daemon_identity: digest(Buffer.from(facts.daemon_identity)), rootless: true,
      user_namespace: 'rootless', cgroup_version: 2, cgroup_driver: 'systemd', explicit_host: true,
    };
  }

  function validateEndpointRecord(document, uid) {
    if (!document || document.schema !== ENDPOINT_SCHEMA) error('docker_endpoint_record_invalid', 'Docker endpoint record is invalid');
    if (document.endpoint === null && document.kind === 'unavailable' && document.ownership_verified === false
      && document.socket_identity === null && document.daemon_identity === null && document.rootless === false
      && document.user_namespace === null && document.cgroup_version === null && document.cgroup_driver === null
      && document.explicit_host === false) return document;
    if (document.endpoint !== endpointFor(uid) || document.kind !== 'rootless' || document.ownership_verified !== true
      || document.rootless !== true || document.user_namespace !== 'rootless' || document.cgroup_version !== 2
      || document.cgroup_driver !== 'systemd' || document.explicit_host !== true
      || typeof document.socket_identity !== 'string' || !SHA256.test(document.daemon_identity || '')) {
      error('docker_endpoint_record_invalid', 'Docker endpoint record is not verified rootless authority');
    }
    return document;
  }

  function readServiceEndpoint(layout) {
    const filename = path.join(layout.private, 'service.env');
    if (!exists(filename)) return null;
    const text = core.readOwnedRegular(filename, layout.identity.uid, [0o600], 4 * 1024 * 1024).toString('utf8');
    const values = text.split('\n').filter((line) => line.startsWith('VOICE_AGENT_DOCKER_HOST='));
    if (values.length !== 1) return null;
    return values[0].slice('VOICE_AGENT_DOCKER_HOST='.length) || null;
  }

  function realAuthorityFacts(layout) {
    const uid = layout.identity.uid;
    const socketPath = `/run/user/${uid}/docker.sock`;
    const endpoint = endpointFor(uid);
    const serviceEndpoint = readServiceEndpoint(layout);
    try {
      const metadata = fs.lstatSync(socketPath);
      const result = spawnSync('docker', ['--host', endpoint, 'info', '--format', '{{json .}}'], {
        encoding: 'utf8', timeout: 5000, maxBuffer: 1024 * 1024,
        env: { PATH: '/usr/bin:/bin', HOME: '/nonexistent', DOCKER_CONFIG: '/nonexistent', LANG: 'C.UTF-8', LC_ALL: 'C.UTF-8' },
      });
      if (result.status !== 0) return null;
      const info = JSON.parse(result.stdout);
      const security = Array.isArray(info.SecurityOptions) ? info.SecurityOptions.map(String) : [];
      const rootless = security.some((item) => item.includes('rootless'));
      let rootOwner = null;
      if (cleanPath(info.DockerRootDir)) {
        core.noSymlinkComponents(info.DockerRootDir);
        const root = fs.lstatSync(info.DockerRootDir);
        if (root.isDirectory()) rootOwner = root.uid;
      }
      return {
        endpoint, socket_path: socketPath, socket_type: metadata.isSocket() ? 'socket' : 'other', socket_uid: metadata.uid,
        socket_mode: metadata.mode & 0o777, socket_device: metadata.dev, socket_inode: metadata.ino,
        explicit_host: true, ambient_context_used: false, rootless, daemon_uid: rootOwner,
        daemon_identity: String(info.ID || ''), user_namespace: rootless ? 'rootless' : 'host',
        cgroup_version: Number(info.CgroupVersion), cgroup_driver: String(info.CgroupDriver || ''), service_endpoint: serviceEndpoint,
      };
    } catch { return null; }
  }

  function containerFactsFromInspect(raw, layout, registry) {
    if (!raw || typeof raw !== 'object' || raw.Id !== registry.selected_container_id
      || !IMAGE.test(raw.Image || '') || typeof raw.Name !== 'string'
      || !raw.Config || typeof raw.Config !== 'object' || !raw.Config.Labels || typeof raw.Config.Labels !== 'object'
      || !raw.GraphDriver || typeof raw.GraphDriver !== 'object' || typeof raw.GraphDriver.Name !== 'string'
      || !raw.GraphDriver.Data || typeof raw.GraphDriver.Data !== 'object'
      || !raw.State || typeof raw.State !== 'object' || !Array.isArray(raw.Mounts)
      || !raw.HostConfig || typeof raw.HostConfig !== 'object') {
      error('agent_environment_inventory_invalid', 'Docker container inspection is incomplete');
    }
    const labels = raw.Config.Labels;
    const generation = Number(labels[GENERATION_LABEL]);
    const runtimeSpec = String(labels[SPEC_LABEL] || '');
    if (labels[MANAGED_LABEL] !== '1' || labels[SCHEMA_LABEL] !== '1'
      || labels[OWNER_LABEL] !== registry.owner_key || !RUNTIME_SPEC.test(runtimeSpec)
      || !Number.isSafeInteger(generation) || generation < 1) {
      error('agent_environment_inventory_invalid', 'Docker container custody labels are invalid');
    }
    const graphData = {};
    for (const key of Object.keys(raw.GraphDriver.Data).sort()) {
      const value = raw.GraphDriver.Data[key];
      if (typeof key !== 'string' || key.length < 1 || key.length > 128 || typeof value !== 'string' || value.length > 4096) {
        error('agent_environment_inventory_invalid', 'Docker rootfs storage facts are invalid');
      }
      graphData[key] = value;
    }
    const mounts = raw.Mounts.map((item) => {
      if (!item || typeof item !== 'object' || !cleanPath(item.Source) || !cleanPath(item.Destination)) {
        error('agent_environment_inventory_invalid', 'Docker mount facts are invalid');
      }
      core.noSymlinkComponents(item.Source);
      const metadata = fs.lstatSync(item.Source);
      if (!metadata.isDirectory() || metadata.isSymbolicLink()) error('agent_environment_inventory_invalid', 'Docker mount source custody is invalid');
      return {
        source: item.Source, destination: item.Destination, mode: item.RW === false ? 'read_only' : 'read_write',
        device: metadata.dev, inode: metadata.ino, owner: metadata.uid,
      };
    });
    const healthStatus = raw.State.Health && typeof raw.State.Health === 'object' ? raw.State.Health.Status : null;
    const running = raw.State.Running === true;
    const state = !running ? 'stopped' : healthStatus === 'unhealthy' ? 'unhealthy' : 'running';
    const health = running ? healthStatus === 'healthy' : false;
    const portBindings = raw.HostConfig.PortBindings && typeof raw.HostConfig.PortBindings === 'object'
      ? Object.keys(raw.HostConfig.PortBindings).sort() : [];
    const safeLabels = {
      managed: labels[MANAGED_LABEL], schema: labels[SCHEMA_LABEL], owner: labels[OWNER_LABEL],
      spec: runtimeSpec, generation: String(generation),
    };
    return {
      container_id: raw.Id, name: raw.Name.replace(/^\//, ''), image_digest: raw.Image,
      config_digest: digest(Buffer.from(core.canonicalJson(safeLabels))), runtime_spec: runtimeSpec,
      rootfs: { storage_identity: digest(Buffer.from(core.canonicalJson({ driver: raw.GraphDriver.Name, data: graphData }))) },
      state, health, mounts,
      network: { mode: String(raw.HostConfig.NetworkMode || ''), published_ports: portBindings },
      resources: {
        nano_cpus: Number(raw.HostConfig.NanoCpus || 0), memory_bytes: Number(raw.HostConfig.Memory || 0),
        pids: Number(raw.HostConfig.PidsLimit || 0), shm_bytes: Number(raw.HostConfig.ShmSize || 0),
      },
    };
  }

  function realContainerFacts(layout, endpoint, registry) {
    const result = spawnSync('docker', ['--host', endpoint, 'container', 'inspect', registry.selected_container_id], {
      encoding: 'utf8', timeout: 5000, maxBuffer: 4 * 1024 * 1024,
      env: { PATH: '/usr/bin:/bin', HOME: '/nonexistent', DOCKER_CONFIG: '/nonexistent', LANG: 'C.UTF-8', LC_ALL: 'C.UTF-8' },
    });
    if (result.status !== 0) return null;
    const parsed = JSON.parse(result.stdout);
    if (!Array.isArray(parsed) || parsed.length !== 1) error('agent_environment_inventory_invalid', 'Docker returned ambiguous container inspection');
    return containerFactsFromInspect(parsed[0], layout, registry);
  }

  function mountIdentity(mount) {
    if (!mount || typeof mount !== 'object' || !cleanPath(mount.source) || !cleanPath(mount.destination)
      || !['read_only', 'read_write'].includes(mount.mode) || !Number.isSafeInteger(mount.device)
      || !Number.isSafeInteger(mount.inode) || !Number.isSafeInteger(mount.owner)) {
      error('agent_environment_inventory_invalid', 'container mount inventory is invalid');
    }
    return { destination: mount.destination, source_identity: digest(Buffer.from(`${mount.device}:${mount.inode}:${mount.owner}`)), mode: mount.mode };
  }

  function inventoryDocument(facts, expected) {
    if (!facts || typeof facts !== 'object' || !CONTAINER_ID.test(facts.container_id || '')
      || typeof facts.name !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/.test(facts.name)
      || !IMAGE.test(facts.image_digest || '') || !SHA256.test(facts.config_digest || '')
      || !facts.rootfs || !SHA256.test(facts.rootfs.storage_identity || '')
      || !['running', 'stopped', 'unhealthy'].includes(facts.state)
      || !Array.isArray(facts.mounts) || typeof facts.network !== 'object' || typeof facts.resources !== 'object') {
      error('agent_environment_inventory_invalid', 'container inventory is invalid');
    }
    const mounts = facts.mounts.map(mountIdentity).sort((a, b) => a.destination.localeCompare(b.destination));
    const destinations = mounts.map((item) => item.destination);
    if (new Set(destinations).size !== destinations.length || !destinations.includes('/workspace') || !destinations.includes('/cache')) {
      error('agent_environment_inventory_invalid', 'workspace/cache mount inventory is incomplete');
    }
    const policy = {
      network: facts.network,
      resources: facts.resources,
      mounts,
    };
    const spec = digest(Buffer.from(core.canonicalJson({ image_digest: facts.image_digest, config_digest: facts.config_digest, policy })));
    const stale = Boolean(expected && (expected.force_stale === true
      || (expected.image_digest && expected.image_digest !== facts.image_digest)
      || (expected.config_digest && expected.config_digest !== facts.config_digest)
      || (expected.spec_digest && expected.spec_digest !== spec)
      || (expected.runtime_spec && expected.runtime_spec !== facts.runtime_spec)));
    const identity = digest(Buffer.from(core.canonicalJson({
      container_id: facts.container_id, name: facts.name, rootfs: facts.rootfs.storage_identity,
      workspace: mounts.find((item) => item.destination === '/workspace').source_identity,
      cache: mounts.find((item) => item.destination === '/cache').source_identity, mounts,
      network: facts.network, resources: facts.resources,
    })));
    return {
      container_id: facts.container_id, container_id_prefix: facts.container_id.slice(0, 12), name: facts.name,
      image_digest: facts.image_digest, config_digest: facts.config_digest, spec_digest: spec,
      rootfs_storage_identity: facts.rootfs.storage_identity,
      workspace_identity: mounts.find((item) => item.destination === '/workspace').source_identity,
      cache_identity: mounts.find((item) => item.destination === '/cache').source_identity,
      mount_identities: mounts, network_policy_digest: digest(Buffer.from(core.canonicalJson(facts.network))),
      resource_policy_digest: digest(Buffer.from(core.canonicalJson(facts.resources))),
      runtime_state: facts.state, health: facts.health === true ? 'healthy' : facts.health === false ? 'unhealthy' : 'unknown',
      identity_digest: identity, stale_spec: stale,
    };
  }

  function validatePreservation(document) {
    const keys = ['action', 'checked_at', 'container_id_prefix', 'endpoint_identity', 'identity_digest', 'reason_code', 'runtime_state', 'schema', 'state'];
    if (!document || typeof document !== 'object' || Array.isArray(document)
      || Object.keys(document).sort().join('\0') !== keys.join('\0')
      || document.schema !== PRESERVATION_SCHEMA || !STATES.has(document.state) || !ACTIONS.has(document.action)
      || (document.identity_digest !== null && !SHA256.test(document.identity_digest))
      || (document.container_id_prefix !== null && !/^[a-f0-9]{12}$/.test(document.container_id_prefix))
      || (document.runtime_state !== null && !['running', 'stopped', 'unhealthy'].includes(document.runtime_state))
      || (document.endpoint_identity !== null && (typeof document.endpoint_identity !== 'string' || document.endpoint_identity.length < 1 || document.endpoint_identity.length > 128))
      || (document.reason_code !== null && (typeof document.reason_code !== 'string' || !/^[a-z0-9_]{1,64}$/.test(document.reason_code)))
      || (document.checked_at !== null && typeof document.checked_at !== 'string')) {
      error('agent_environment_status_invalid', 'capability status is invalid');
    }
    return document;
  }

  function status(state, action, details = {}) {
    return validatePreservation({
      schema: PRESERVATION_SCHEMA, state, action, identity_digest: details.identity_digest || null,
      container_id_prefix: details.container_id_prefix || null, runtime_state: details.runtime_state || null,
      endpoint_identity: details.endpoint_identity || null, reason_code: details.reason_code || null,
      checked_at: details.checked_at || null,
    });
  }

  function configEnabled(layout) {
    const filename = path.join(layout.config, 'config.yaml');
    if (!exists(filename)) return false;
    const text = core.readOwnedRegular(filename, layout.identity.uid, [0o600], 4 * 1024 * 1024).toString('utf8');
    const enabled = new Map();
    let section = null;
    for (const line of text.split('\n')) {
      const top = /^([a-z_][a-z0-9_]*):(?:[ ]*#.*)?$/.exec(line);
      if (top) { section = top[1]; continue; }
      const child = /^[ ]{2}enabled:[ ]*(true|false)(?:[ ]*#.*)?$/.exec(line);
      if (child && (section === 'agent' || section === 'agent_environment')) enabled.set(section, child[1] === 'true');
      else if (/^[^ ]/.test(line) && !/^[ ]*(?:#.*)?$/.test(line)) section = null;
    }
    return enabled.get('agent') === true && enabled.get('agent_environment') === true;
  }

  function readRuntimeRegistry(layout) {
    const filename = path.join(layout.agent, 'private', 'registry.json');
    if (!exists(filename)) return null;
    const value = JSON.parse(core.readOwnedRegular(filename, layout.identity.uid, [0o600], 1024 * 1024).toString('utf8'));
    if (!value || value.schema_version !== 'voice-agent.agent-environment-registry.v1'
      || (value.selected_container_id !== null && !CONTAINER_ID.test(value.selected_container_id))) {
      error('agent_environment_registry_invalid', 'AgentEnvironment registry is incompatible');
    }
    return value;
  }

  function expectedSpec(layout, registry) {
    const expected = {
      image_digest: ALLOWED_IMAGE_DIGEST,
      ...(registry && typeof registry.spec === 'string' && RUNTIME_SPEC.test(registry.spec) ? { runtime_spec: registry.spec } : {}),
      ...(registry && registry.state === 'stale_spec' ? { force_stale: true } : {}),
    };
    const filename = path.join(layout.agent, 'private', 'desired-spec.json');
    if (!exists(filename)) return expected;
    const value = JSON.parse(core.readOwnedRegular(filename, layout.identity.uid, [0o600], 256 * 1024).toString('utf8'));
    if (!value || typeof value !== 'object' || Array.isArray(value)) error('agent_environment_inventory_invalid', 'desired AgentEnvironment spec is invalid');
    return { ...value, ...expected };
  }

  async function capture(layout, dependencies = {}, options = {}) {
    if (dependencies.agentEnvironment && typeof dependencies.agentEnvironment.capture === 'function') {
      const observed = await dependencies.agentEnvironment.capture({ layout, phase: options.phase || 'status' });
      if (!observed || typeof observed !== 'object') error('agent_environment_status_invalid', 'injected capability status is invalid');
      return status(observed.state, observed.action, observed);
    }
    const enabled = configEnabled(layout);
    let registry;
    try { registry = readRuntimeRegistry(layout); } catch { return status('degraded_identity_mismatch', 'restore_exact_environment', { reason_code: 'registry_invalid' }); }
    if (!registry || !registry.selected_container_id) return enabled
      ? status('degraded_identity_mismatch', 'restore_exact_environment', { reason_code: 'container_missing' })
      : status('disabled', 'configure_agent', { reason_code: 'not_configured' });
    let record = null;
    const endpointFile = path.join(layout.private, 'docker-endpoint.json');
    try { if (exists(endpointFile)) record = validateEndpointRecord(JSON.parse(core.readOwnedRegular(endpointFile, layout.identity.uid, [0o600], 256 * 1024).toString('utf8')), layout.identity.uid); } catch {}
    if (!record || !record.endpoint) return status('degraded_endpoint_unavailable', 'restore_rootless_endpoint', { reason_code: 'endpoint_unavailable' });
    const facts = realAuthorityFacts(layout);
    let verified;
    try { verified = endpointRecord(layout.identity.uid, facts); } catch { return status('degraded_endpoint_unavailable', 'restore_rootless_endpoint', { reason_code: 'endpoint_authority_mismatch' }); }
    if (verified.socket_identity !== record.socket_identity || verified.daemon_identity !== record.daemon_identity) {
      return status('degraded_endpoint_unavailable', 'restore_rootless_endpoint', { reason_code: 'endpoint_identity_changed' });
    }
    let container;
    try {
      container = dependencies.agentEnvironment && typeof dependencies.agentEnvironment.inspect === 'function'
        ? await dependencies.agentEnvironment.inspect({ endpoint: record.endpoint, container_id: registry.selected_container_id })
        : realContainerFacts(layout, record.endpoint, registry);
    } catch (reason) {
      if (reason instanceof core.LauncherError && reason.code === 'agent_environment_inventory_invalid') {
        return status('degraded_identity_mismatch', 'restore_exact_environment', { endpoint_identity: verified.socket_identity, reason_code: 'container_inventory_invalid' });
      }
      return status('degraded_endpoint_unavailable', 'restore_rootless_endpoint', { endpoint_identity: verified.socket_identity, reason_code: 'daemon_unavailable' });
    }
    if (!container) return status('degraded_identity_mismatch', 'restore_exact_environment', { endpoint_identity: verified.socket_identity, reason_code: 'container_missing' });
    if (container.container_id !== registry.selected_container_id) return status('degraded_identity_mismatch', 'restore_exact_environment', { reason_code: 'selected_container_mismatch' });
    const inventory = inventoryDocument(container, expectedSpec(layout, registry));
    if (inventory.stale_spec) return status('stale_spec', 'maintenance_rebuild_required', { ...inventory, endpoint_identity: verified.socket_identity, reason_code: 'configured_spec_changed' });
    if (container.state !== 'running' || container.health !== true) return status('degraded_identity_mismatch', 'restore_exact_environment', { ...inventory, endpoint_identity: verified.socket_identity, reason_code: container.state === 'stopped' ? 'container_stopped' : 'container_unhealthy' });
    return status(enabled ? 'ready' : 'disabled', enabled ? 'none' : 'configure_agent', { ...inventory, endpoint_identity: verified.socket_identity });
  }

  function assertPreserved(before, after) {
    if (!before || !after) error('agent_environment_receipt_invalid', 'AgentEnvironment preservation receipt is missing');
    if (before.identity_digest && after.identity_digest && before.identity_digest !== after.identity_digest) {
      error('agent_environment_identity_mismatch', 'AgentEnvironment container/rootfs/workspace/mount identity changed during application lifecycle');
    }
    if (before.identity_digest && !after.identity_digest && !['degraded_endpoint_unavailable'].includes(after.state)) {
      error('agent_environment_identity_mismatch', 'AgentEnvironment identity disappeared during application lifecycle');
    }
    return true;
  }

  function writePreservation(layout, observed, writer) {
    const filename = path.join(layout.agent, 'private', 'preservation.json');
    writer(filename, validatePreservation(observed), layout.identity.uid);
    return filename;
  }

  function readPreservation(installRoot, uid) {
    const filename = path.join(installRoot, 'agent-environment', 'private', 'preservation.json');
    if (!exists(filename)) return null;
    const value = JSON.parse(core.readOwnedRegular(filename, uid, [0o600], 256 * 1024).toString('utf8'));
    return validatePreservation(value);
  }

  function hashOwnedFile(filename, uid) {
    const descriptor = fs.openSync(filename, fs.constants.O_RDONLY | (fs.constants.O_NOFOLLOW || 0));
    try {
      const before = fs.fstatSync(descriptor);
      if (!before.isFile() || before.uid !== uid || before.nlink !== 1) error('legacy_agent_environment_unsafe', 'legacy AgentEnvironment file custody is invalid');
      const hash = crypto.createHash('sha256');
      const buffer = Buffer.allocUnsafe(1024 * 1024);
      let offset = 0;
      for (;;) {
        const count = fs.readSync(descriptor, buffer, 0, buffer.length, offset);
        if (count === 0) break;
        hash.update(buffer.subarray(0, count)); offset += count;
      }
      const after = fs.fstatSync(descriptor);
      if (after.dev !== before.dev || after.ino !== before.ino || after.size !== before.size || offset !== before.size) {
        error('legacy_agent_environment_cas_mismatch', 'legacy AgentEnvironment changed during inventory');
      }
      return { size: before.size, sha256: hash.digest('hex') };
    } finally { fs.closeSync(descriptor); }
  }

  function inventoryTree(root, uid) {
    const records = [];
    function walk(directory, prefix = '') {
      for (const name of fs.readdirSync(directory).sort()) {
        const relative = prefix ? `${prefix}/${name}` : name;
        const filename = path.join(directory, name); const meta = fs.lstatSync(filename);
        if (meta.uid !== uid || meta.isSymbolicLink() || meta.isSocket() || meta.isFIFO() || meta.isCharacterDevice() || meta.isBlockDevice()) error('legacy_agent_environment_unsafe', 'legacy AgentEnvironment has unsafe custody or type');
        if (meta.isDirectory()) { records.push(`D\0${relative}\0${meta.mode & 0o777}`); walk(filename, relative); }
        else if (meta.isFile() && meta.nlink === 1) {
          const identity = hashOwnedFile(filename, uid);
          records.push(`F\0${relative}\0${meta.mode & 0o777}\0${identity.size}\0${identity.sha256}`);
        } else error('legacy_agent_environment_unsafe', 'legacy AgentEnvironment has unsafe custody or type');
      }
    }
    walk(root); return { entry_count: records.length, inventory_sha256: digest(Buffer.from(records.join('\n'))) };
  }

  function removeOwnedStage(root, parent, uid) {
    if (path.dirname(root) !== parent) error('legacy_agent_environment_unsafe', 'migration stage escaped its canonical parent');
    function remove(filename) {
      const metadata = fs.lstatSync(filename);
      if (metadata.uid !== uid || metadata.isSymbolicLink()) error('legacy_agent_environment_unsafe', 'migration stage custody is invalid');
      if (metadata.isDirectory()) { for (const name of fs.readdirSync(filename)) remove(path.join(filename, name)); fs.rmdirSync(filename); }
      else if (metadata.isFile() && metadata.nlink === 1) fs.unlinkSync(filename);
      else error('legacy_agent_environment_unsafe', 'migration stage type is invalid');
    }
    remove(root); syncDirectory(parent);
  }

  function copyOwnedTree(source, target, uid) {
    const sourceMeta = fs.lstatSync(source);
    fs.mkdirSync(target, { mode: sourceMeta.mode & 0o777 }); fs.chmodSync(target, sourceMeta.mode & 0o777);
    for (const name of fs.readdirSync(source).sort()) {
      const from = path.join(source, name); const to = path.join(target, name); const metadata = fs.lstatSync(from);
      if (metadata.uid !== uid || metadata.isSymbolicLink()) error('legacy_agent_environment_unsafe', 'legacy AgentEnvironment copy source is unsafe');
      if (metadata.isDirectory()) copyOwnedTree(from, to, uid);
      else if (metadata.isFile() && metadata.nlink === 1) {
        const input = fs.openSync(from, fs.constants.O_RDONLY | (fs.constants.O_NOFOLLOW || 0));
        const output = fs.openSync(to, fs.constants.O_WRONLY | fs.constants.O_CREAT | fs.constants.O_EXCL | (fs.constants.O_NOFOLLOW || 0), metadata.mode & 0o777);
        try {
          const inputMeta = fs.fstatSync(input);
          if (!inputMeta.isFile() || inputMeta.uid !== uid || inputMeta.nlink !== 1) error('legacy_agent_environment_unsafe', 'legacy AgentEnvironment copy source changed');
          const buffer = Buffer.allocUnsafe(1024 * 1024); let offset = 0;
          for (;;) {
            const count = fs.readSync(input, buffer, 0, buffer.length, offset);
            if (count === 0) break;
            let written = 0;
            while (written < count) written += fs.writeSync(output, buffer, written, count - written, offset + written);
            offset += count;
          }
          if (offset !== inputMeta.size) error('legacy_agent_environment_cas_mismatch', 'legacy AgentEnvironment changed during copy');
          fs.fchmodSync(output, metadata.mode & 0o777); fs.fsyncSync(output);
        } finally { fs.closeSync(output); fs.closeSync(input); }
      } else error('legacy_agent_environment_unsafe', 'legacy AgentEnvironment copy source type is invalid');
    }
    syncDirectory(target);
  }

  function migrateLegacy(layout, transactionId, writer, options = {}) {
    if (!/^[a-f0-9]{32}$/.test(transactionId)) error('legacy_agent_environment_unsafe', 'migration transaction identity is invalid');
    const source = path.join(layout.identity.home, '.cache', 'voice-agent-v2', 'agent-environment');
    const target = layout.agent;
    if (!exists(source)) return { state: 'absent', receipt: null };
    core.noSymlinkComponents(source); const sourceMeta = fs.lstatSync(source);
    if (!sourceMeta.isDirectory() || sourceMeta.uid !== layout.identity.uid || (sourceMeta.mode & 0o777) !== 0o700) error('legacy_agent_environment_unsafe', 'legacy AgentEnvironment root custody is invalid');
    const before = inventoryTree(source, layout.identity.uid);
    if (exists(target)) {
      const priorReceipt = path.join(target, 'private', 'migration-receipt.json');
      if (exists(priorReceipt)) {
        const receipt = JSON.parse(core.readOwnedRegular(priorReceipt, layout.identity.uid, [0o600], 256 * 1024).toString('utf8'));
        if (receipt.schema === MIGRATION_SCHEMA && receipt.entry_count === before.entry_count
          && receipt.inventory_sha256 === before.inventory_sha256 && receipt.target_class === 'durable_user_state'
          && receipt.release_owned === false && receipt.gc_eligible === false) return { state: 'already_migrated', receipt };
        error('legacy_agent_environment_conflict', 'canonical AgentEnvironment migration receipt differs');
      }
      const names = fs.readdirSync(target);
      const scaffold = new Set(['private', 'workspace', 'cache', 'rootfs-storage']);
      const scaffoldOnly = names.every((name) => scaffold.has(name) && fs.lstatSync(path.join(target, name)).isDirectory()
        && fs.readdirSync(path.join(target, name)).length === 0);
      if (!scaffoldOnly && names.length !== 0) {
        const after = inventoryTree(target, layout.identity.uid);
        if (after.inventory_sha256 !== before.inventory_sha256 || after.entry_count !== before.entry_count) error('legacy_agent_environment_conflict', 'canonical AgentEnvironment already differs');
        return { state: 'already_migrated', receipt: after };
      }
      for (const name of names) fs.rmdirSync(path.join(target, name));
      fs.rmdirSync(target); syncDirectory(path.dirname(target));
    }
    const targetParent = path.dirname(target);
    const sameFilesystem = options.forceCrossFilesystem !== true && sourceMeta.dev === fs.lstatSync(targetParent).dev;
    let method;
    if (sameFilesystem) {
      fs.renameSync(source, target); syncDirectory(path.dirname(source)); syncDirectory(targetParent);
      method = 'same_filesystem_rename';
    } else {
      const stage = path.join(targetParent, `.agent-environment-migration-${transactionId}`);
      if (exists(stage)) removeOwnedStage(stage, targetParent, layout.identity.uid);
      copyOwnedTree(source, stage, layout.identity.uid);
      const staged = inventoryTree(stage, layout.identity.uid);
      if (staged.inventory_sha256 !== before.inventory_sha256 || staged.entry_count !== before.entry_count) error('legacy_agent_environment_cas_mismatch', 'staged AgentEnvironment inventory differs');
      fs.renameSync(stage, target); syncDirectory(targetParent);
      method = 'cross_filesystem_staged_copy';
    }
    const after = inventoryTree(target, layout.identity.uid);
    if (after.inventory_sha256 !== before.inventory_sha256 || after.entry_count !== before.entry_count) error('legacy_agent_environment_cas_mismatch', 'migrated AgentEnvironment inventory differs');
    const receipt = {
      schema: MIGRATION_SCHEMA, transaction_id: transactionId, method,
      entry_count: after.entry_count, inventory_sha256: after.inventory_sha256,
      source_removed_by_rename: sameFilesystem, source_retained: !sameFilesystem,
      target_class: 'durable_user_state', release_owned: false, gc_eligible: false,
    };
    const privateRoot = path.join(target, 'private');
    if (!exists(privateRoot)) { fs.mkdirSync(privateRoot, { mode: 0o700 }); fs.chmodSync(privateRoot, 0o700); syncDirectory(target); }
    writer(path.join(privateRoot, 'migration-receipt.json'), receipt, layout.identity.uid);
    return { state: 'migrated', receipt };
  }

  return {
    ACTIONS, ENDPOINT_SCHEMA, MIGRATION_SCHEMA, PRESERVATION_SCHEMA, STATES, assertPreserved, capture,
    configEnabled, containerFactsFromInspect, endpointFor, endpointRecord, inventoryDocument, migrateLegacy,
    readPreservation, validateEndpointRecord, validatePreservation, writePreservation,
  };
};
