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
    if (document.endpoint === null && document.kind === 'unavailable' && document.ownership_verified === false) return document;
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
    const stale = Boolean(expected && ((expected.image_digest && expected.image_digest !== facts.image_digest)
      || (expected.config_digest && expected.config_digest !== facts.config_digest)
      || (expected.spec_digest && expected.spec_digest !== spec)));
    const identity = digest(Buffer.from(core.canonicalJson({
      container_id: facts.container_id, name: facts.name, rootfs: facts.rootfs.storage_identity,
      workspace: mounts.find((item) => item.destination === '/workspace').source_identity,
      cache: mounts.find((item) => item.destination === '/cache').source_identity, mounts,
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

  function status(state, action, details = {}) {
    if (!STATES.has(state) || !ACTIONS.has(action)) error('agent_environment_status_invalid', 'capability status is invalid');
    return {
      schema: PRESERVATION_SCHEMA, state, action, identity_digest: details.identity_digest || null,
      container_id_prefix: details.container_id_prefix || null, runtime_state: details.runtime_state || null,
      endpoint_identity: details.endpoint_identity || null, reason_code: details.reason_code || null,
      checked_at: details.checked_at || null,
    };
  }

  function configEnabled(layout) {
    const filename = path.join(layout.config, 'config.yaml');
    if (!exists(filename)) return false;
    const text = core.readOwnedRegular(filename, layout.identity.uid, [0o600], 4 * 1024 * 1024).toString('utf8');
    return /^agent:\n(?:[ ]{2}.*\n)*?[ ]{2}enabled:[ ]+true[ ]*$/m.test(text)
      && /^agent_environment:\n(?:[ ]{2}.*\n)*?[ ]{2}enabled:[ ]+true[ ]*$/m.test(text);
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
    const filename = path.join(layout.agent, 'private', 'desired-spec.json');
    if (exists(filename)) {
      const value = JSON.parse(core.readOwnedRegular(filename, layout.identity.uid, [0o600], 256 * 1024).toString('utf8'));
      return value && typeof value === 'object' ? value : {};
    }
    return registry && typeof registry.spec === 'string' && SHA256.test(registry.spec) ? { spec_digest: registry.spec } : {};
  }

  async function capture(layout, dependencies = {}, options = {}) {
    if (dependencies.agentEnvironment && typeof dependencies.agentEnvironment.capture === 'function') {
      const observed = await dependencies.agentEnvironment.capture({ layout, phase: options.phase || 'status' });
      if (!observed || !STATES.has(observed.state) || !ACTIONS.has(observed.action)
        || (observed.identity_digest !== null && !SHA256.test(observed.identity_digest))) error('agent_environment_status_invalid', 'injected capability status is invalid');
      return { schema: PRESERVATION_SCHEMA, identity_digest: null, container_id_prefix: null, runtime_state: null, endpoint_identity: null, reason_code: null, checked_at: null, ...observed };
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
    if (!dependencies.agentEnvironment || typeof dependencies.agentEnvironment.inspect !== 'function') {
      return status('degraded_endpoint_unavailable', 'restore_rootless_endpoint', { endpoint_identity: verified.socket_identity, reason_code: 'container_probe_unavailable' });
    }
    let container;
    try { container = await dependencies.agentEnvironment.inspect({ endpoint: record.endpoint, container_id: registry.selected_container_id }); }
    catch { return status('degraded_endpoint_unavailable', 'restore_rootless_endpoint', { endpoint_identity: verified.socket_identity, reason_code: 'daemon_unavailable' }); }
    if (!container || container.container_id !== registry.selected_container_id) return status('degraded_identity_mismatch', 'restore_exact_environment', { reason_code: 'selected_container_mismatch' });
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
    writer(filename, observed, layout.identity.uid);
    return filename;
  }

  function readPreservation(installRoot, uid) {
    const filename = path.join(installRoot, 'agent-environment', 'private', 'preservation.json');
    if (!exists(filename)) return null;
    const value = JSON.parse(core.readOwnedRegular(filename, uid, [0o600], 256 * 1024).toString('utf8'));
    if (!value || value.schema !== PRESERVATION_SCHEMA || !STATES.has(value.state) || !ACTIONS.has(value.action)
      || (value.identity_digest !== null && !SHA256.test(value.identity_digest))) error('agent_environment_status_invalid', 'preservation record is invalid');
    return value;
  }

  function inventoryTree(root, uid) {
    const records = [];
    function walk(directory, prefix = '') {
      for (const name of fs.readdirSync(directory).sort()) {
        const relative = prefix ? `${prefix}/${name}` : name;
        const filename = path.join(directory, name); const meta = fs.lstatSync(filename);
        if (meta.uid !== uid || meta.isSymbolicLink() || meta.isSocket() || meta.isFIFO() || meta.isCharacterDevice() || meta.isBlockDevice()) error('legacy_agent_environment_unsafe', 'legacy AgentEnvironment has unsafe custody or type');
        if (meta.isDirectory()) { records.push(`D\0${relative}\0${meta.mode & 0o777}`); walk(filename, relative); }
        else if (meta.isFile() && meta.nlink === 1) records.push(`F\0${relative}\0${meta.mode & 0o777}\0${meta.size}\0${digest(core.readOwnedRegular(filename, uid, null, 1024 * 1024 * 1024))}`);
        else error('legacy_agent_environment_unsafe', 'legacy AgentEnvironment has unsafe custody or type');
      }
    }
    walk(root); return { entry_count: records.length, inventory_sha256: digest(Buffer.from(records.join('\n'))) };
  }

  function migrateLegacy(layout, transactionId, writer) {
    const source = path.join(layout.identity.home, '.cache', 'voice-agent-v2', 'agent-environment');
    const target = layout.agent;
    if (!exists(source)) return { state: 'absent', receipt: null };
    core.noSymlinkComponents(source); const sourceMeta = fs.lstatSync(source);
    if (!sourceMeta.isDirectory() || sourceMeta.uid !== layout.identity.uid || (sourceMeta.mode & 0o777) !== 0o700) error('legacy_agent_environment_unsafe', 'legacy AgentEnvironment root custody is invalid');
    const before = inventoryTree(source, layout.identity.uid);
    if (exists(target)) {
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
    fs.renameSync(source, target); syncDirectory(path.dirname(source)); syncDirectory(path.dirname(target));
    const after = inventoryTree(target, layout.identity.uid);
    if (after.inventory_sha256 !== before.inventory_sha256 || after.entry_count !== before.entry_count) error('legacy_agent_environment_cas_mismatch', 'migrated AgentEnvironment inventory differs');
    const receipt = {
      schema: MIGRATION_SCHEMA, transaction_id: transactionId, method: 'same_filesystem_rename',
      entry_count: after.entry_count, inventory_sha256: after.inventory_sha256,
      source_removed_by_rename: true, target_class: 'durable_user_state', release_owned: false, gc_eligible: false,
    };
    const privateRoot = path.join(target, 'private');
    if (!exists(privateRoot)) { fs.mkdirSync(privateRoot, { mode: 0o700 }); fs.chmodSync(privateRoot, 0o700); syncDirectory(target); }
    writer(path.join(privateRoot, 'migration-receipt.json'), receipt, layout.identity.uid);
    return { state: 'migrated', receipt };
  }

  return {
    ACTIONS, ENDPOINT_SCHEMA, MIGRATION_SCHEMA, PRESERVATION_SCHEMA, STATES, assertPreserved, capture,
    endpointFor, endpointRecord, inventoryDocument, migrateLegacy, readPreservation, validateEndpointRecord, writePreservation,
  };
};
