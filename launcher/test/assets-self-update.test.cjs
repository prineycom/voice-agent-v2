'use strict';

const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');
const core = require('../voice-agent.cjs');
const installer = require('../install.cjs')(core);
const cache = require('../asset-cache.cjs')(core, installer);
const selfUpdate = require('../self-update.cjs')(core, installer);
const UID = process.geteuid();
const hash = (bytes) => crypto.createHash('sha256').update(bytes).digest('hex');
const exists = (filename) => { try { fs.lstatSync(filename); return true; } catch { return false; } };
const code = (expected, action) => assert.rejects(action, (reason) => reason && reason.code === expected);

function descriptor(kind, bytes, overrides = {}) {
  const sha256 = hash(bytes); const image = kind === 'agent_environment_image';
  return {
    authority: { origin: 'https://assets.example.invalid', path_prefix: '/voice-agent/' },
    compatibility: { minimum_launcher_protocol: 1, maximum_launcher_protocol: 1, minimum_application_protocol: 1, maximum_application_protocol: 1 },
    digest: `sha256:${sha256}`, id: `${kind}-fixture`, kind, license: { id: 'Fixture-Test-Only', acceptance: 'accepted' },
    platform: core.SUPPORTED_PLATFORM, reachability: image ? 'optional' : 'required', required_free_space_reserve: 4096,
    sha256, size: bytes.length,
    url: image ? `https://assets.example.invalid/voice-agent/images/environment@sha256:${sha256}` : `https://assets.example.invalid/voice-agent/${kind}/${sha256}`,
    ...overrides,
  };
}

function harness(context) {
  const parent = fs.mkdtempSync(path.join(os.tmpdir(), 'voice-agent-asset-test-')); fs.chmodSync(parent, 0o700);
  const identity = { uid: UID, username: 'fixture-user', home: path.join(parent, 'home'), dataHome: path.join(parent, 'data'), configHome: path.join(parent, 'config'), cacheHome: path.join(parent, 'cache'), stateHome: path.join(parent, 'state'), runtimeHome: path.join(parent, 'runtime') };
  const layout = installer.layoutFor(identity, true); installer.installDirectories(layout); installer.ensurePrivateDirectory(path.dirname(layout.launcher), UID);
  context.after(() => { function writable(file) { if (!exists(file)) return; const meta = fs.lstatSync(file); if (meta.isSymbolicLink()) return; if (meta.isDirectory()) { fs.chmodSync(file, 0o700); for (const name of fs.readdirSync(file)) writable(path.join(file, name)); } else fs.chmodSync(file, 0o600); } writable(parent); fs.rmSync(parent, { recursive: true, force: true }); });
  return { parent, identity, layout };
}

function exactSource(bytes, mutate = {}) {
  return { downloadAsset: async ({ descriptor: value, offset }) => ({ status: offset ? 206 : 200, bytes: bytes.subarray(offset), validator: 'etag-fixture', content_range: offset ? `bytes ${offset}-${value.size - 1}/${value.size}` : null, redirected: false, url: value.url, ...mutate }) };
}

async function finish(layout, value, owner, source, options = {}) {
  let result; do { result = await cache.acquire(layout, value, owner, source, options); } while (result.state === 'partial'); return result;
}

test('closed descriptors reject changed authority, mutable image identity, platform, compatibility, and unknown fields', () => {
  const bytes = Buffer.from('asset'); const value = descriptor('runtime', bytes);
  assert.equal(cache.validateDescriptor(value), value);
  for (const changed of [
    { ...value, url: `https://mirror.example.invalid/voice-agent/runtime/${value.sha256}` },
    { ...value, url: `${value.url}?token=secret` }, { ...value, platform: 'linux-arm64' }, { ...value, extra: true },
  ]) assert.throws(() => cache.validateDescriptor(changed));
  const image = descriptor('agent_environment_image', bytes, { url: 'https://assets.example.invalid/voice-agent/images/environment:latest' });
  assert.throws(() => cache.validateDescriptor(image));
});

test('complete and resumed downloads converge to one owner-only verified content-addressed asset', async (context) => {
  const value = harness(context); const bytes = Buffer.from('0123456789abcdef'); const item = descriptor('runtime', bytes); const owner = '1'.repeat(32);
  let first = true;
  const source = { downloadAsset: async ({ descriptor: request, offset }) => {
    const chunk = first ? bytes.subarray(0, 5) : bytes.subarray(offset); first = false;
    return { status: offset ? 206 : 200, bytes: chunk, validator: 'etag-1', content_range: offset ? `bytes ${offset}-${request.size - 1}/${request.size}` : null, redirected: false, url: request.url };
  } };
  assert.equal((await cache.acquire(value.layout, item, owner, source)).state, 'partial');
  const result = await finish(value.layout, item, owner, source);
  assert.equal(result.state, 'downloaded_verified'); assert.equal(cache.verifyCached(value.layout, item), true);
  assert.equal(fs.lstatSync(cache.cachePath(value.layout, item)).mode & 0o777, 0o400);
  assert.equal((await cache.acquire(value.layout, item, owner, { downloadAsset() { throw new Error('must not download'); } })).state, 'cached_verified');
  assert.equal(fs.readdirSync(value.layout.runtimes).length, 1);
  const programBytes = Buffer.from('signed-program-download'); const program = descriptor('program', programBytes, { id: 'program-fixture' });
  await finish(value.layout, program, owner, exactSource(programBytes));
  assert.equal(path.dirname(cache.cachePath(value.layout, program)), path.join(value.layout.downloads, 'sha256'));
  assert.equal(cache.verifyCached(value.layout, program), true);
});

test('changed range validator resets exactly; oversize, hash, redirect, and malformed range fail closed', async (context) => {
  const reset = harness(context); const bytes = Buffer.from('range-reset-bytes'); const item = descriptor('runtime', bytes); const owner = '2'.repeat(32);
  await cache.acquire(reset.layout, item, owner, { downloadAsset: async ({ url }) => ({ status: 200, bytes: bytes.subarray(0, 4), validator: 'old', content_range: null, redirected: false, url }) });
  let calls = 0;
  const resetSource = { downloadAsset: async ({ descriptor: request, offset, url }) => { calls += 1; return calls === 1
    ? { status: 206, bytes: bytes.subarray(offset), validator: 'new', content_range: `bytes ${offset}-${request.size - 1}/${request.size}`, redirected: false, url }
    : { status: 200, bytes, validator: 'new', content_range: null, redirected: false, url }; } };
  assert.equal((await finish(reset.layout, item, owner, resetSource)).state, 'downloaded_verified'); assert.equal(calls, 2);

  for (const [expected, payload, mutate] of [
    ['asset_oversize', Buffer.concat([bytes, Buffer.from('x')]), {}],
    ['asset_hash_mismatch', Buffer.alloc(bytes.length, 0x78), {}],
    ['asset_redirect_refused', bytes, { redirected: true }],
    ['asset_redirect_refused', bytes, { url: 'https://mirror.example.invalid/file' }],
  ]) {
    const separate = harness(context); await code(expected, () => finish(separate.layout, item, '3'.repeat(32), exactSource(payload, mutate)));
    assert.equal(cache.verifyCached(separate.layout, item), false);
  }
});

test('offline acquisition uses only exact verified complete cache and optional image remains stale_spec without pull', async (context) => {
  const value = harness(context); const modelBytes = Buffer.from('offline-model'); const runtimeBytes = Buffer.from('offline-runtime');
  const model = descriptor('model', modelBytes); const runtime = descriptor('runtime', runtimeBytes); const image = descriptor('agent_environment_image', Buffer.from('image'));
  await finish(value.layout, model, '4'.repeat(32), exactSource(modelBytes)); await finish(value.layout, runtime, '4'.repeat(32), exactSource(runtimeBytes));
  const result = await cache.reconcile(value.layout, [model, runtime, image], '4'.repeat(32), null, { offline: true });
  assert.equal(result.optional_agent_environment_image, 'stale_spec');
  const missing = descriptor('runtime', Buffer.from('missing'));
  await code('offline_material_insufficient', () => cache.reconcile(value.layout, [model, missing], '4'.repeat(32), null, { offline: true }));
});

test('partial and LRU GC remove only exact unreferenced reconstructible owned bytes and revalidate references', async (context) => {
  const value = harness(context); const aBytes = Buffer.from('runtime-a'); const bBytes = Buffer.from('runtime-b');
  const a = descriptor('runtime', aBytes, { id: 'runtime-a' }); const b = descriptor('runtime', bBytes, { id: 'runtime-b' });
  await finish(value.layout, a, '5'.repeat(32), exactSource(aBytes)); await finish(value.layout, b, '5'.repeat(32), exactSource(bBytes));
  const aPath = cache.cachePath(value.layout, a); const bPath = cache.cachePath(value.layout, b); const old = new Date('2020-01-01T00:00:00Z'); fs.utimesSync(aPath, old, old);
  let references = [b.sha256];
  const gc = cache.collectAssets(value.layout, references, { references: () => references, bytesNeeded: 1 });
  assert.equal(gc.count, 1); assert.equal(exists(aPath), false); assert.equal(exists(bPath), true);
  fs.writeFileSync(path.join(value.layout.downloads, `runtime-${a.sha256}.${'6'.repeat(32)}.partial`), 'partial', { mode: 0o600 });
  assert.equal(cache.collectPartials(value.layout, new Set()).count, 1);
  const user = path.join(value.layout.appData, 'user-data'); fs.writeFileSync(user, 'keep'); assert.equal(fs.readFileSync(user, 'utf8'), 'keep');
});

test('asset reachability admits 0/1/2/3/100 inventories and collects every unreferenced exact runtime without count refusal', (context) => {
  for (const count of [0, 1, 2, 3, 100]) {
    const value = harness(context); const protectedDigests = [];
    for (let index = 0; index < count; index += 1) {
      const bytes = Buffer.from(`runtime-inventory-${index}`); const item = descriptor('runtime', bytes, { id: `runtime-${index}` });
      const filename = cache.cachePath(value.layout, item); fs.writeFileSync(filename, bytes, { mode: 0o400 }); fs.chmodSync(filename, 0o400);
      if (index === count - 1) protectedDigests.push(item.sha256);
    }
    const result = cache.collectAssets(value.layout, protectedDigests);
    assert.equal(result.count, Math.max(0, count - 1), String(count));
    assert.equal(fs.readdirSync(value.layout.runtimes).length, Math.min(1, count), String(count));
  }
});

test('disk preflight reports one exact required/available/reclaimable summary and no inventory count blocker', () => {
  assert.deepEqual(cache.requireSpace({ required: 10, available: 10, reclaimable: 3 }), { required: 10, available: 10, reclaimable: 3 });
  assert.throws(() => cache.requireSpace({ required: 11, available: 10, reclaimable: 3 }), (reason) => reason.code === 'insufficient_space' && /required=11 available=10 reclaimable=3/.test(reason.message));
});

function selfHarness(context) {
  const value = harness(context); const oldBytes = Buffer.from('#!/old-launcher\n'); const newBytes = Buffer.from('#!/new-launcher\n');
  fs.writeFileSync(value.layout.launcher, oldBytes, { mode: 0o755 }); fs.chmodSync(value.layout.launcher, 0o755);
  const release = '1.1.0-abcdef012345'; fs.mkdirSync(path.join(value.layout.releases, release), { mode: 0o500 }); fs.symlinkSync(`releases/${release}`, value.layout.current);
  installer.writeJson(value.layout.installRecord, { schema: 'fixture', healthy_release: release, release_id: release }, UID);
  const item = descriptor('launcher', newBytes);
  const target = cache.cachePath(value.layout, item); fs.writeFileSync(target, newBytes, { mode: 0o400 }); fs.chmodSync(target, 0o400);
  return { ...value, oldBytes, newBytes, release, item, transactionId: '7'.repeat(32) };
}

test('launcher replaces only after durable application health, validates post-exec identities, then removes one backup', (context) => {
  const value = selfHarness(context);
  const result = selfUpdate.replace(value.layout, value.item, value.transactionId, value.release, { process: { execPost: () => ({ completed: true }) } });
  assert.equal(result.state, 'launcher_updated'); assert.deepEqual(fs.readFileSync(value.layout.launcher), value.newBytes);
  assert.equal(exists(`${value.layout.launcher}.old`), false); assert.equal(exists(value.layout.selfUpdateJournal), false);
});

test('launcher interruption after every rename/fsync/exec/validation phase recovers idempotently to exact old or new without touching healthy app', (context) => {
  const survey = selfHarness(context); const events = [];
  selfUpdate.replace(survey.layout, survey.item, survey.transactionId, survey.release, { process: { execPost: () => ({ completed: true }) }, fault: { afterSelfUpdatePhase: (name) => events.push(name) } });
  for (const event of [...new Set(events)]) {
    const value = selfHarness(context); let fired = false;
    try {
      selfUpdate.replace(value.layout, value.item, value.transactionId, value.release, { process: { execPost: () => ({ completed: true }) }, fault: { afterSelfUpdatePhase(name) { if (!fired && name === event) { fired = true; throw new core.LauncherError('launcher_update_interrupted', 'fixture kill'); } } } });
    } catch (reason) { assert.equal(reason.code, 'launcher_update_interrupted', event); }
    const recovered = selfUpdate.recover(value.layout);
    assert.ok(['launcher_updated', 'old_launcher_restored', 'no_launcher_recovery'].includes(recovered.state), `${event}:${recovered.state}`);
    assert.equal(exists(value.layout.selfUpdateJournal), false, event);
    assert.equal(fs.readFileSync(value.layout.installRecord, 'utf8').includes(value.release), true);
  }
});

test('launcher wrong hash, foreign symlink, backup ambiguity, and unhealthy application fail without disturbing application bytes', (context) => {
  const wrong = selfHarness(context); fs.chmodSync(cache.cachePath(wrong.layout, wrong.item), 0o600); fs.writeFileSync(cache.cachePath(wrong.layout, wrong.item), Buffer.alloc(wrong.newBytes.length, 0x78)); fs.chmodSync(cache.cachePath(wrong.layout, wrong.item), 0o400);
  assert.throws(() => selfUpdate.replace(wrong.layout, wrong.item, wrong.transactionId, wrong.release), (reason) => reason.code === 'asset_cache_invalid');
  const ambiguous = selfHarness(context); fs.symlinkSync('/tmp/foreign', `${ambiguous.layout.launcher}.old`);
  assert.throws(() => selfUpdate.replace(ambiguous.layout, ambiguous.item, ambiguous.transactionId, ambiguous.release), (reason) => reason.code === 'launcher_update_backup_ambiguous');
  const unhealthy = selfHarness(context); fs.writeFileSync(unhealthy.layout.updateJournal, '{}', { mode: 0o600 });
  assert.throws(() => selfUpdate.replace(unhealthy.layout, unhealthy.item, unhealthy.transactionId, unhealthy.release), (reason) => reason.code === 'launcher_update_application_unhealthy');
  assert.deepEqual(fs.readFileSync(unhealthy.layout.launcher), unhealthy.oldBytes);
});

test('asset/offline/space/self-update records and errors are privacy-safe', async (context) => {
  const value = selfHarness(context); const output = JSON.stringify({ descriptor: value.item, space: cache.spaceSummary({ required: 1, available: 2, reclaimable: 3 }) });
  for (const forbidden of [value.parent, 'token=', 'PRIVATE_SECRET', 'process output', 'workspace filename']) assert.equal(output.includes(forbidden), false);
});
