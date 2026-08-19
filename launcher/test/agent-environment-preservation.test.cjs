'use strict';

const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');

const core = require('../voice-agent.cjs');
const preservation = require('../agent-environment.cjs')(core);
const installer = require('../install.cjs')(core);
const UID = process.geteuid();
const sha = (value) => crypto.createHash('sha256').update(value).digest('hex');

function authority(overrides = {}) {
  return {
    endpoint: `unix:///run/user/${UID}/docker.sock`, socket_path: `/run/user/${UID}/docker.sock`,
    socket_type: 'socket', socket_uid: UID, socket_mode: 0o600, socket_device: 10, socket_inode: 20,
    explicit_host: true, ambient_context_used: false, rootless: true, daemon_uid: UID,
    daemon_identity: 'fixture-daemon', user_namespace: 'rootless', cgroup_version: 2,
    cgroup_driver: 'systemd', service_endpoint: `unix:///run/user/${UID}/docker.sock`, ...overrides,
  };
}

function container(overrides = {}) {
  return {
    container_id: 'a'.repeat(64), name: 'voice-agent-v2-owner-g1', image_digest: `sha256:${'b'.repeat(64)}`,
    config_digest: 'c'.repeat(64), rootfs: { storage_identity: 'd'.repeat(64) }, state: 'running', health: true,
    mounts: [
      { source: '/durable/workspace', destination: '/workspace', mode: 'read_write', device: 1, inode: 2, owner: UID },
      { source: '/durable/cache', destination: '/cache', mode: 'read_write', device: 1, inode: 3, owner: UID },
      { source: '/explicit/read-only', destination: '/mnt/reference', mode: 'read_only', device: 1, inode: 4, owner: UID },
    ],
    network: { enabled: true, mode: 'rootless_private', publish_ports: [] },
    resources: { cpus: 2, memory_mib: 4096, pids: 128 }, ...overrides,
  };
}

function code(expected, action) {
  assert.throws(action, (reason) => reason && reason.code === expected);
}

test('explicit Docker authority accepts only exact owner-only rootless socket, daemon, cgroup, namespace, and service endpoint', () => {
  const record = preservation.endpointRecord(UID, authority());
  assert.equal(record.endpoint, `unix:///run/user/${UID}/docker.sock`);
  assert.equal(record.kind, 'rootless');
  assert.equal(record.ownership_verified, true);
  for (const facts of [
    authority({ endpoint: 'unix:///var/run/docker.sock', socket_path: '/var/run/docker.sock', rootless: false, user_namespace: 'host' }),
    authority({ socket_uid: UID + 1 }), authority({ daemon_uid: UID + 1 }), authority({ socket_mode: 0o660 }),
    authority({ ambient_context_used: true }), authority({ service_endpoint: 'unix:///wrong/docker.sock' }),
    authority({ cgroup_version: 1 }), authority({ cgroup_driver: 'cgroupfs' }), authority({ user_namespace: 'host' }),
  ]) code('docker_authority_invalid', () => preservation.endpointRecord(UID, facts));
  const unavailable = preservation.endpointRecord(UID);
  assert.equal(unavailable.endpoint, null);
  assert.equal(preservation.validateEndpointRecord(unavailable, UID).kind, 'unavailable');
  code('docker_endpoint_record_invalid', () => preservation.validateEndpointRecord({ ...unavailable, rootless: true }, UID));
});

test('production Docker inspect parsing binds only exact selected custody and emits content-free inventory', (context) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'voice-agent-env-inspect-')); fs.chmodSync(root, 0o700);
  context.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const workspace = path.join(root, 'workspace'); const cache = path.join(root, 'cache'); const extra = path.join(root, 'extra');
  for (const directory of [workspace, cache, extra]) fs.mkdirSync(directory, { mode: 0o700 });
  const runtimeSpec = 'a'.repeat(52); const owner = 'owner-fixture';
  const raw = {
    Id: '1'.repeat(64), Name: '/voice-agent-v2-owner-g1', Image: `sha256:${'2'.repeat(64)}`,
    Config: { Labels: {
      'io.priney.voice-agent-v2.managed': '1', 'io.priney.voice-agent-v2.schema': '1',
      'io.priney.voice-agent-v2.owner': owner, 'io.priney.voice-agent-v2.spec': runtimeSpec,
      'io.priney.voice-agent-v2.generation': '7',
    } },
    GraphDriver: { Name: 'overlay2', Data: { UpperDir: '/daemon/private/upper', WorkDir: '/daemon/private/work' } },
    State: { Running: true, Health: { Status: 'healthy' } },
    Mounts: [
      { Source: workspace, Destination: '/workspace', RW: true },
      { Source: cache, Destination: '/cache', RW: true },
      { Source: extra, Destination: '/mnt/reference', RW: false },
    ],
    HostConfig: { NetworkMode: 'bridge', PortBindings: {}, NanoCpus: 2000000000, Memory: 4294967296, PidsLimit: 128, ShmSize: 1073741824 },
  };
  const facts = preservation.containerFactsFromInspect(raw, { identity: { uid: UID } }, { selected_container_id: raw.Id, owner_key: owner });
  const inventory = preservation.inventoryDocument(facts, { runtime_spec: runtimeSpec });
  assert.equal(inventory.runtime_state, 'running'); assert.equal(inventory.stale_spec, false);
  const encoded = JSON.stringify(inventory);
  for (const forbidden of [root, '/daemon/private', 'UpperDir', 'owner-fixture']) assert.equal(encoded.includes(forbidden), false);
  code('agent_environment_inventory_invalid', () => preservation.containerFactsFromInspect(
    { ...raw, Config: { Labels: { ...raw.Config.Labels, 'io.priney.voice-agent-v2.owner': 'replacement' } } },
    { identity: { uid: UID } }, { selected_container_id: raw.Id, owner_key: owner },
  ));
});

test('generated config enablement requires both explicit top-level capability booleans', (context) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'voice-agent-env-config-')); fs.chmodSync(root, 0o700);
  context.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const config = path.join(root, 'config'); fs.mkdirSync(config, { mode: 0o700 });
  const layout = { config, agent: path.join(root, 'agent'), identity: { uid: UID } };
  const filename = path.join(config, 'config.yaml');
  fs.writeFileSync(filename, installer.renderAgentConfig(layout, '1'.repeat(32), `unix:///run/user/${UID}/docker.sock`, true), { mode: 0o600 });
  assert.equal(preservation.configEnabled(layout), true);
  fs.writeFileSync(filename, installer.renderAgentConfig(layout, '1'.repeat(32), `unix:///run/user/${UID}/docker.sock`, false), { mode: 0o600 });
  assert.equal(preservation.configEnabled(layout), false);
  fs.writeFileSync(filename, 'agent:\n  enabled: true\nagent_environment:\n  enabled: false\n', { mode: 0o600 });
  assert.equal(preservation.configEnabled(layout), false);
});

test('content-free inventory binds exact container, rootfs, workspace/cache, extra mounts, image/config, network and resources', () => {
  const first = preservation.inventoryDocument(container());
  const same = preservation.inventoryDocument(container());
  assert.equal(first.identity_digest, same.identity_digest);
  assert.equal(first.container_id_prefix, 'a'.repeat(12));
  assert.equal(first.mount_identities.length, 3);
  assert.equal(JSON.stringify(first).includes('/durable/workspace'), false);
  assert.equal(JSON.stringify(first).includes('/explicit/read-only'), false);
  assert.equal(JSON.stringify(first).includes('prompt'), false);
  const rootfs = preservation.inventoryDocument(container({ rootfs: { storage_identity: 'e'.repeat(64) } }));
  const workspace = preservation.inventoryDocument(container({ mounts: container().mounts.map((item) => item.destination === '/workspace' ? { ...item, inode: 99 } : item) }));
  const extra = preservation.inventoryDocument(container({ mounts: container().mounts.map((item) => item.destination === '/mnt/reference' ? { ...item, inode: 100 } : item) }));
  const network = preservation.inventoryDocument(container({ network: { enabled: false, mode: 'none', publish_ports: [] } }));
  const resources = preservation.inventoryDocument(container({ resources: { cpus: 1, memory_mib: 2048, pids: 64 } }));
  assert.notEqual(rootfs.identity_digest, first.identity_digest);
  assert.notEqual(workspace.identity_digest, first.identity_digest);
  assert.notEqual(extra.identity_digest, first.identity_digest);
  assert.notEqual(network.identity_digest, first.identity_digest);
  assert.notEqual(resources.identity_digest, first.identity_digest);
  assert.equal(preservation.inventoryDocument(container(), { image_digest: `sha256:${'f'.repeat(64)}` }).stale_spec, true);
});

test('preservation assertion permits optional endpoint degradation but detects ID/rootfs/workspace/mount replacement', () => {
  const before = { state: 'ready', action: 'none', identity_digest: sha('same') };
  assert.equal(preservation.assertPreserved(before, { state: 'ready', action: 'none', identity_digest: sha('same') }), true);
  assert.equal(preservation.assertPreserved(before, { state: 'degraded_endpoint_unavailable', action: 'restore_rootless_endpoint', identity_digest: null }), true);
  code('agent_environment_identity_mismatch', () => preservation.assertPreserved(before, { state: 'degraded_identity_mismatch', action: 'restore_exact_environment', identity_digest: null }));
  code('agent_environment_identity_mismatch', () => preservation.assertPreserved(before, { state: 'ready', action: 'none', identity_digest: sha('replacement') }));
});

test('legacy durable tree migrates by no-follow same-filesystem rename with hash/CAS/fsync receipt outside releases and GC cache', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'voice-agent-env-migration-')); fs.chmodSync(root, 0o700);
  try {
    const identity = { uid: UID, home: path.join(root, 'home') };
    const source = path.join(identity.home, '.cache', 'voice-agent-v2', 'agent-environment');
    const target = path.join(root, 'data', 'voice-agent', 'agent-environment');
    fs.mkdirSync(path.join(source, 'private'), { recursive: true, mode: 0o700 });
    fs.mkdirSync(path.join(source, 'workspace'), { recursive: true, mode: 0o700 });
    fs.mkdirSync(path.join(source, 'cache'), { recursive: true, mode: 0o700 });
    fs.writeFileSync(path.join(source, 'private', 'registry.json'), '{}\n', { mode: 0o600 });
    fs.writeFileSync(path.join(source, 'workspace', 'sentinel'), 'durable bytes', { mode: 0o600 });
    fs.mkdirSync(path.dirname(target), { recursive: true, mode: 0o700 });
    const layout = { identity, agent: target };
    const writer = (filename, document) => fs.writeFileSync(filename, Buffer.from(core.canonicalJson(document)), { mode: 0o600 });
    const result = preservation.migrateLegacy(layout, '1'.repeat(32), writer);
    assert.equal(result.state, 'migrated');
    assert.equal(fs.existsSync(source), false);
    assert.equal(fs.readFileSync(path.join(target, 'workspace', 'sentinel'), 'utf8'), 'durable bytes');
    const receipt = JSON.parse(fs.readFileSync(path.join(target, 'private', 'migration-receipt.json')));
    assert.equal(receipt.target_class, 'durable_user_state');
    assert.equal(receipt.release_owned, false); assert.equal(receipt.gc_eligible, false);
    assert.equal(JSON.stringify(receipt).includes(root), false);
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
});

test('cross-filesystem legacy migration uses staged no-follow copy, CAS/fsync receipt, and retains the old tree', (context) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'voice-agent-env-cross-migration-')); fs.chmodSync(root, 0o700);
  context.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const identity = { uid: UID, home: path.join(root, 'home') };
  const source = path.join(identity.home, '.cache', 'voice-agent-v2', 'agent-environment');
  const target = path.join(root, 'data', 'voice-agent', 'agent-environment');
  fs.mkdirSync(path.join(source, 'private'), { recursive: true, mode: 0o700 });
  fs.mkdirSync(path.join(source, 'workspace'), { recursive: true, mode: 0o700 });
  fs.writeFileSync(path.join(source, 'workspace', 'sentinel'), 'retained durable bytes', { mode: 0o600 });
  fs.mkdirSync(path.dirname(target), { recursive: true, mode: 0o700 });
  const writer = (filename, document) => fs.writeFileSync(filename, Buffer.from(core.canonicalJson(document)), { mode: 0o600 });
  const result = preservation.migrateLegacy({ identity, agent: target }, '2'.repeat(32), writer, { forceCrossFilesystem: true });
  assert.equal(result.receipt.method, 'cross_filesystem_staged_copy');
  assert.equal(result.receipt.source_retained, true); assert.equal(result.receipt.source_removed_by_rename, false);
  assert.equal(fs.readFileSync(path.join(source, 'workspace', 'sentinel'), 'utf8'), 'retained durable bytes');
  assert.equal(fs.readFileSync(path.join(target, 'workspace', 'sentinel'), 'utf8'), 'retained durable bytes');
  const repeated = preservation.migrateLegacy({ identity, agent: target }, '2'.repeat(32), writer, { forceCrossFilesystem: true });
  assert.equal(repeated.state, 'already_migrated');
});
