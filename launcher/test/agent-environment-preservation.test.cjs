'use strict';

const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');

const core = require('../voice-agent.cjs');
const preservation = require('../agent-environment.cjs')(core);
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
  assert.equal(preservation.endpointRecord(UID).endpoint, null);
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
  assert.notEqual(rootfs.identity_digest, first.identity_digest);
  assert.notEqual(workspace.identity_digest, first.identity_digest);
  assert.notEqual(extra.identity_digest, first.identity_digest);
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
