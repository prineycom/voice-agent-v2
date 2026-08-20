'use strict';

const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');

const launcher = require('../voice-agent.cjs');
const installer = require('../install.cjs')(launcher);
const updater = require('../update.cjs')(launcher, installer);
const UID = process.geteuid();
const NOW = new Date('2026-08-21T00:00:00Z');

function hash(bytes) { return crypto.createHash('sha256').update(bytes).digest('hex'); }
function exists(filename) { try { fs.lstatSync(filename); return true; } catch { return false; } }
function code(expected, action) { return assert.rejects(action, (reason) => reason && reason.code === expected); }
function assetDescriptor(id, kind, bytes, overrides = {}) {
  const sha256 = hash(bytes);
  const image = kind === 'agent_environment_image';
  return {
    authority: { origin: 'https://assets.example.invalid', path_prefix: '/voice-agent/' },
    compatibility: { minimum_launcher_protocol: 1, maximum_launcher_protocol: 1, minimum_application_protocol: 1, maximum_application_protocol: 1 },
    digest: `sha256:${sha256}`, id, kind, license: { id: image ? 'OCI-fixture' : 'Fixture-Test-Only', acceptance: 'accepted' },
    platform: launcher.SUPPORTED_PLATFORM, reachability: image ? 'optional' : 'required', required_free_space_reserve: 1024,
    sha256, size: bytes.length,
    url: image ? `https://assets.example.invalid/voice-agent/images/environment@sha256:${sha256}` : `https://assets.example.invalid/voice-agent/${kind}/${sha256}`,
    ...overrides,
  };
}

function migration(overrides) {
  const value = { id: 'config-v2-v3', from: 2, to: 3, scope: 'config', operation: 'schema-version', reversible: true, destructive: false, product_choice: false, ...overrides };
  value.sha256 = hash(Buffer.from(launcher.canonicalJson(value)));
  return value;
}

function artifact(version, sequence, options = {}) {
  const configSchema = options.configSchema || 2;
  const contents = new Map([
    ['bin/voice-agent-runtime', Buffer.from('#!/bin/sh\nexit 0\n')],
    ['descriptors/local-models.json', Buffer.from('{"models":"exact"}')],
    ['descriptors/runtime.json', Buffer.from('{"runtime":"exact"}')],
  ]);
  if (options.migrations) contents.set('descriptors/config-migrations.json', Buffer.from(launcher.canonicalJson({ schema: 'voice-agent.config-migrations.v1', migrations: options.migrations })));
  const entries = [
    { path: 'bin', type: 'directory', mode: '0555', size: 0, sha256: null, target: null },
    { path: 'bin/voice-agent-runtime', type: 'file', mode: '0555', size: contents.get('bin/voice-agent-runtime').length, sha256: hash(contents.get('bin/voice-agent-runtime')), target: null },
    { path: 'descriptors', type: 'directory', mode: '0555', size: 0, sha256: null, target: null },
    ...[...contents.entries()].filter(([name]) => name.startsWith('descriptors/')).map(([name, bytes]) => ({ path: name, type: 'file', mode: '0444', size: bytes.length, sha256: hash(bytes), target: null })),
  ];
  const manifest = {
    application_protocol: { minimum: 1, maximum: 1 }, build_id: hash(Buffer.from(`build-${version}`)).slice(0, 40),
    config_schema: { minimum: configSchema, maximum: configSchema }, data_schema: { minimum: 2, maximum: 2 }, entries,
    launcher_protocol: { minimum: 1, maximum: 1 }, platform: launcher.SUPPORTED_PLATFORM,
    schema: 'voice-agent.platform-artifact-manifest.v1', service_template_sha256: installer.UNIT_CONTRACT_SHA256, version,
  };
  const manifestBytes = Buffer.from(launcher.canonicalJson(manifest));
  const artifactBytes = Buffer.from(`signed-artifact-${version}`);
  const assetContents = new Map([
    ['model', Buffer.from(`model-${version}`)], ['runtime', Buffer.from(`runtime-${version}`)], ['agent_environment_image', Buffer.from(`image-${version}`)],
  ]);
  const assets = [assetDescriptor(`model-${version.replaceAll('.', '-')}`, 'model', assetContents.get('model')),
    assetDescriptor(`runtime-${version.replaceAll('.', '-')}`, 'runtime', assetContents.get('runtime')),
    assetDescriptor(`environment-${version.replaceAll('.', '-')}`, 'agent_environment_image', assetContents.get('agent_environment_image'))];
  const release = {
    artifact_bytes: artifactBytes.length, artifact_sha256: hash(artifactBytes), artifact_url: `https://releases.example.invalid/${version}.tar.zst`, assets,
    build_id: manifest.build_id, launcher: options.launcher || null, manifest_sha256: hash(manifestBytes), maximum_data_schema: 2, minimum_data_schema: 2,
    minimum_launcher_protocol: 1, platform: launcher.SUPPORTED_PLATFORM, version,
  };
  const channel = { channel: 'stable', expires_at: options.expires || '2026-09-20T00:00:00Z', generated_at: '2026-08-20T00:00:00Z', releases: [release], schema: 'voice-agent.channel.v1', sequence };
  const keys = options.keys || crypto.generateKeyPairSync('ed25519');
  const channelBytes = Buffer.from(launcher.canonicalJson(channel));
  return {
    artifactBytes, manifestBytes, release, manifest, channelBytes, assetContents,
    signatureBytes: Buffer.from(`${crypto.sign(null, channelBytes, keys.privateKey).toString('base64')}\n`),
    publicKeyPem: keys.publicKey.export({ type: 'spki', format: 'pem' }),
    archiveEntries: [{ path: 'release-manifest.json', type: 'file', mode: '0444', size: manifestBytes.length, sha256: hash(manifestBytes), target: null }, ...entries],
    readEntry: async (name) => contents.get(name),
  };
}

function ready(release, active, options = {}) {
  return {
    service_active: active, service_enabled: true, process_uid: options.process_uid ?? UID,
    runtime: {
      release_id: options.release_id || `${release.release.version}-${release.release.artifact_sha256.slice(0, 12)}`,
      build_id: options.build_id || release.release.build_id, accepting: options.accepting ?? true, provider: 'local', automatic_fallback: false,
      health: { overall_readiness: options.overall || 'ready', components: installer.REQUIRED_COMPONENTS.map((component) => ({ component, liveness: 'alive', readiness: options.component || 'ready', compatible: true })) },
    },
    listener: { host: options.host || '127.0.0.1', port: options.port || 8000, owner_uid: options.listener_uid ?? UID, owner: options.owner || 'service' },
  };
}

async function addOwnedRelease(value, releaseArtifact) {
  const id = `${releaseArtifact.release.version}-${releaseArtifact.release.artifact_sha256.slice(0, 12)}`;
  const stage = path.join(value.layout.transactions, `seed-${id}`);
  fs.mkdirSync(stage, { mode: 0o700 });
  await installer.extractVerifiedArchive(stage, releaseArtifact.manifest, releaseArtifact.manifestBytes, releaseArtifact);
  installer.atomicWrite(path.join(stage, 'release-record.json'), Buffer.from(launcher.canonicalJson({
    schema: 'voice-agent.release-record.v1', release_id: id, version: releaseArtifact.release.version,
    build_id: releaseArtifact.release.build_id, platform: releaseArtifact.release.platform, channel: 'stable',
    channel_sequence: 1, artifact_sha256: releaseArtifact.release.artifact_sha256,
    artifact_bytes: releaseArtifact.release.artifact_bytes, manifest_sha256: releaseArtifact.release.manifest_sha256,
    launcher_protocol: 1, application_protocol: releaseArtifact.manifest.application_protocol,
    data_schema: releaseArtifact.manifest.data_schema, service_template_sha256: releaseArtifact.manifest.service_template_sha256,
    verified_at: '2026-08-20T00:00:00Z', readiness: { state: 'not_verified', checked_at: null },
  })), 0o400, UID);
  fs.renameSync(stage, path.join(value.layout.releases, id)); fs.chmodSync(path.join(value.layout.releases, id), 0o500);
  return id;
}

function harness(options = {}) {
  const parent = fs.mkdtempSync(path.join(os.tmpdir(), 'voice-agent-update-test-')); fs.chmodSync(parent, 0o700);
  const identity = { uid: UID, username: 'fixture-user', home: path.join(parent, 'home'), dataHome: path.join(parent, 'data'), configHome: path.join(parent, 'config'), cacheHome: path.join(parent, 'cache'), stateHome: path.join(parent, 'state'), runtimeHome: path.join(parent, 'runtime') };
  const keys = crypto.generateKeyPairSync('ed25519');
  const initial = artifact('1.0.0', 9, { keys });
  const candidate = options.candidate || artifact('1.1.0', 10, { ...options.candidateOptions, keys });
  if (options.candidate) {
    candidate.signatureBytes = Buffer.from(`${crypto.sign(null, candidate.channelBytes, keys.privateKey).toString('base64')}\n`);
    candidate.publicKeyPem = keys.publicKey.export({ type: 'spki', format: 'pem' });
  }
  let sourceArtifact = initial;
  let seeding = true;
  let active = false;
  let running = initial;
  let stoppedOnce = false;
  let monotonic = 0;
  const calls = [];
  const lines = [];
  const service = {
    enableLinger: async () => calls.push('linger'),
    installUnit: async (request) => { calls.push('unit'); fs.mkdirSync(path.dirname(request.path), { recursive: true, mode: 0o700 }); fs.chmodSync(path.dirname(request.path), 0o700); fs.writeFileSync(request.path, request.bytes, { mode: 0o600 }); },
    enableAndStart: async () => { calls.push('install-start'); active = true; running = initial; },
    stop: async () => { calls.push('stop'); active = false; stoppedOnce = true; },
    disable: async () => calls.push('disable'),
    quiesce: async () => { calls.push('quiesce'); active = false; stoppedOnce = true; },
    probeStopped: async () => ({ process_generation_gone: true, listener_gone: true }),
    startExactlyOnce: async (request) => { calls.push(`start:${request.release_id}`); active = true; running = request.release_id === `${candidate.release.version}-${candidate.release.artifact_sha256.slice(0, 12)}` ? candidate : initial; },
    probe: async () => {
      if (!active) return ready(running, false, { accepting: false });
      const isCandidate = running === candidate;
      const fail = isCandidate ? options.candidateFailure : options.priorFailureAfterStop && stoppedOnce;
      const mismatch = isCandidate && options.candidateMismatch;
      return ready(running, true, fail ? { accepting: false, component: 'unavailable', overall: 'unready' } : mismatch ? { build_id: 'f'.repeat(40) } : options.priorIdentityMismatch && !seeding && !stoppedOnce ? { process_uid: UID + 1 } : {});
    },
  };
  const clock = { now: () => new Date(NOW), monotonic: () => monotonic, sleep: async (ms) => { monotonic += ms; } };
  const source = {
    acquireChannel: async (request = {}) => {
      if (options.metadataUnavailable && sourceArtifact === candidate) throw new launcher.LauncherError('network_unavailable', 'fixture');
      return { channelBytes: sourceArtifact.channelBytes, signatureBytes: sourceArtifact.signatureBytes, publicKeyPem: sourceArtifact.publicKeyPem, cached: request.offline === true || options.cached === true, local_authorized: request.offline === true || options.cached === true };
    },
    acquireArtifact: async (release, request = {}) => ({ artifactBytes: sourceArtifact.artifactBytes, manifestBytes: sourceArtifact.manifestBytes, archiveEntries: sourceArtifact.archiveEntries, readEntry: sourceArtifact.readEntry, cached: request.offline === true, url: release.artifact_url }),
    downloadAsset: async ({ descriptor, offset }) => {
      const bytes = (descriptor.kind === 'program' ? sourceArtifact.artifactBytes : sourceArtifact.assetContents.get(descriptor.kind)).subarray(offset);
      return { status: offset ? 206 : 200, bytes, validator: `fixture-${descriptor.sha256}`, content_range: offset ? `bytes ${offset}-${descriptor.size - 1}/${descriptor.size}` : null, redirected: false, url: descriptor.url };
    },
  };
  let environmentCapture = 0;
  const dependencies = {
    identity, clock, source, service, randomBytes: (length) => Buffer.alloc(length, 0x6b), output: { info: (line) => lines.push(line) },
    host: { inspectBase: async () => ({ kernel: 'linux', kernel_release: 'fixture', architecture: 'x86_64', systemd: true, user: { uid: UID, name: identity.username, home: identity.home }, nvidia: { available: true, gpu_name: 'RTX 4070', runtime_compatible: true, driver_version: '610.57.04', vram_bytes: 12 * 1024 ** 3, devices: ['gpu', 'control', 'uvm'] } }), inspectCompatibility: async ({ requirements }) => ({ free_bytes: seeding ? 20 * 1024 ** 3 : (options.freeBytes ?? 20 * 1024 ** 3), assets: { ...requirements, model_available: true, runtime_available: true, runtime_compatible: true } }) },
    lock: options.lock, fault: null,
  };
  if (options.environmentStatuses) dependencies.agentEnvironment = {
    capture: async () => options.environmentStatuses[seeding ? 0 : Math.min(environmentCapture++, options.environmentStatuses.length - 1)],
  };
  const layout = installer.layoutFor(identity, true);
  async function seed() { sourceArtifact = initial; seeding = true; await installer.installVoiceAgent({ testMode: true, dependencies }); seeding = false; environmentCapture = 0; sourceArtifact = candidate; calls.length = 0; lines.length = 0; }
  async function update(extra = {}) { return updater.updateVoiceAgent({ testMode: true, dependencies, ...extra }); }
  function cleanup(context) { context.after(() => { function writable(file) { if (!exists(file)) return; const meta = fs.lstatSync(file); if (meta.isSymbolicLink()) return; if (meta.isDirectory()) { fs.chmodSync(file, 0o700); for (const name of fs.readdirSync(file)) writable(path.join(file, name)); } else fs.chmodSync(file, 0o600); } writable(parent); fs.rmSync(parent, { recursive: true, force: true }); }); }
  return { parent, identity, initial, candidate, dependencies, layout, calls, lines, seed, update, cleanup, setSource(value) { sourceArtifact = value; } };
}

function cacheChannel(value, source, sequence = 10, expiresAt = '2026-09-20T00:00:00Z') {
  installer.writeJson(value.layout.channelReceipt, { schema: 'voice-agent.cached-channel.v1', channel_base64: source.channelBytes.toString('base64'), signature_base64: source.signatureBytes.toString('base64'), public_key_base64: Buffer.from(source.publicKeyPem).toString('base64'), authority_sha256: hash(Buffer.from(source.publicKeyPem)), sequence, expires_at: expiresAt, verified_at: '2026-08-21T00:00:00Z' }, UID);
}

test('canonical update stages fully, quiesces once, proves exact candidate, commits rollback, and clears durable journal', async (context) => {
  const value = harness(); value.cleanup(context); await value.seed();
  const result = await value.update();
  assert.equal(result.state, 'updated_healthy');
  assert.equal(fs.readlinkSync(value.layout.current), `releases/${result.release_id}`);
  assert.match(fs.readlinkSync(value.layout.rollback), /1\.0\.0-/);
  assert.equal(exists(value.layout.updateJournal), false);
  assert.deepEqual(value.calls.filter((item) => item === 'quiesce'), ['quiesce']);
  assert.equal(value.calls.filter((item) => item.startsWith('start:')).length, 1);
  assert.equal(fs.lstatSync(value.layout.updateLock).mode & 0o777, 0o600);
  assert.equal(fs.lstatSync(value.layout.config).mode & 0o777, 0o700);
  assert.equal(fs.readdirSync(value.layout.runtimes).length, 2);
  for (const source of [value.initial, value.candidate]) for (const item of source.release.assets.filter((asset) => asset.kind === 'runtime')) assert.equal(launcher.loadAssetCache(installer).verifyCached(value.layout, item), true);
});

test('candidate failure automatically restores exact prior release/config/unit and returns failed-safe; double failure retains recovery material', async (context) => {
  const safe = harness({ candidateFailure: true }); safe.cleanup(context); await safe.seed();
  const beforeConfig = fs.readFileSync(path.join(safe.layout.config, 'config.yaml'));
  await code('update_failed_safe', () => safe.update());
  assert.match(fs.readlinkSync(safe.layout.current), /1\.0\.0-/);
  for (const item of safe.candidate.release.assets.filter((asset) => asset.reachability === 'required')) assert.equal(launcher.loadAssetCache(installer).verifyCached(safe.layout, item), true);
  assert.deepEqual(fs.readFileSync(path.join(safe.layout.config, 'config.yaml')), beforeConfig);
  assert.equal(exists(safe.layout.updateJournal), false);

  const broken = harness({ candidateFailure: true, priorFailureAfterStop: true }); broken.cleanup(context); await broken.seed();
  await code('update_failed_needs_repair', () => broken.update());
  const retainedJournal = fs.readFileSync(broken.layout.updateJournal, 'utf8');
  assert.equal(JSON.parse(retainedJournal).phase, 'failed_needs_repair');
  assert.equal(fs.lstatSync(broken.layout.updateJournal).mode & 0o777, 0o600);
  assert.equal(retainedJournal.includes(broken.parent), false);
  assert.equal(retainedJournal.includes('schema_version:'), false);
  assert.equal(exists(path.join(broken.layout.migrations, '6b'.repeat(16))), true);
});

test('safe ordered migration preserves explicit values; destructive/product-choice migration stops before quiesce', async (context) => {
  const safe = harness({ candidate: artifact('1.1.0', 10, { configSchema: 3, migrations: [migration({})] }) }); safe.cleanup(context); await safe.seed();
  fs.appendFileSync(path.join(safe.layout.config, 'config.yaml'), 'user_explicit: keep-me\n');
  await safe.update();
  const config = fs.readFileSync(path.join(safe.layout.config, 'config.yaml'), 'utf8');
  assert.match(config, /voice-agent\.config\.v3/); assert.match(config, /user_explicit: keep-me/);

  const refused = harness({ candidate: artifact('1.1.0', 10, { configSchema: 3, migrations: [migration({ destructive: true })] }) }); refused.cleanup(context); await refused.seed();
  await code('migration_requires_decision', () => refused.update());
  assert.equal(refused.calls.includes('quiesce'), false);
  assert.match(fs.readlinkSync(refused.layout.current), /1\.0\.0-/);
});

test('all durable phase/action interruption points converge on retry to candidate or proved prior without duplicate blind start', async (context) => {
  const environment = { state: 'ready', action: 'none', identity_digest: 'f'.repeat(64), container_id_prefix: 'e'.repeat(12), runtime_state: 'running' };
  const survey = harness({ environmentStatuses: [environment] }); survey.cleanup(context); await survey.seed();
  const events = [];
  survey.dependencies.fault = { afterDurablePhase: (name) => events.push(`write:${name}`), afterAction: (name) => events.push(`action:${name}`) };
  await survey.update();
  for (const event of [...new Set(events)]) {
    const value = harness({ environmentStatuses: [environment] }); value.cleanup(context); await value.seed();
    let fired = false;
    value.dependencies.fault = {
      afterDurablePhase(name) { if (!fired && event === `write:${name}`) { fired = true; throw new launcher.LauncherError('update_interrupted', 'power loss'); } },
      afterAction(name) { if (!fired && event === `action:${name}`) { fired = true; throw new launcher.LauncherError('update_interrupted', 'power loss'); } },
    };
    await code('update_interrupted', () => value.update());
    value.dependencies.fault = null;
    try { await value.update(); } catch (reason) { if (reason.code !== 'update_failed_safe') throw reason; await value.update(); }
    const selected = fs.readlinkSync(value.layout.current);
    assert.match(selected, /1\.[01]\.0-/i, event);
    assert.equal(exists(value.layout.updateJournal), false, event);
  }
});

test('offline cached candidate reconciliation succeeds only with exact verified material and keeps latest unknown', async (context) => {
  const value = harness(); value.cleanup(context); await value.seed();
  const cache = launcher.loadAssetCache(installer);
  cacheChannel(value, value.candidate);
  for (const descriptor of [...value.candidate.release.assets.filter((item) => item.reachability === 'required'), cache.programDescriptor(value.candidate.release)]) {
    let outcome; do { outcome = await cache.acquire(value.layout, descriptor, '8'.repeat(32), value.dependencies.source); } while (outcome.state === 'partial');
  }
  const result = await value.update({ offline: true });
  assert.equal(result.state, 'updated_healthy'); assert.equal(result.latest_known, false); assert.equal(result.offline, true);

  const insufficient = harness(); insufficient.cleanup(context); await insufficient.seed(); cacheChannel(insufficient, insufficient.candidate);
  await code('offline_material_insufficient', () => insufficient.update({ offline: true }));
  assert.equal(insufficient.calls.includes('quiesce'), false);
});

test('already-current online/offline and unavailable metadata are honest and service-action free', async (context) => {
  const current = harness(); current.cleanup(context); await current.seed(); current.setSource(current.initial);
  let result = await current.update(); assert.equal(result.state, 'already_current_healthy'); assert.equal(result.latest_known, true);
  result = await current.update({ offline: true }); assert.equal(result.latest_known, false); assert.match(current.lines.at(-1), /latest is unknown/);

  const unavailable = harness({ metadataUnavailable: true }); unavailable.cleanup(context); await unavailable.seed();
  result = await unavailable.update(); assert.equal(result.state, 'metadata_unavailable_current_healthy'); assert.equal(result.latest_known, false);
  assert.equal(unavailable.calls.length, 0);
});

test('concurrent updater, channel rollback/expiry, disk shortage, prior identity mismatch, and exact candidate mismatch fail honestly', async (context) => {
  const concurrent = harness(); concurrent.cleanup(context); await concurrent.seed();
  const held = updater.acquireExclusiveLock(concurrent.layout, concurrent.dependencies);
  try { await code('update_in_progress', () => concurrent.update()); } finally { held.release(); }

  const expired = harness({ candidate: artifact('1.1.0', 10, { expires: '2026-08-20T12:00:00Z' }) }); expired.cleanup(context); await expired.seed();
  await code('channel_expired', () => expired.update()); assert.equal(expired.calls.length, 0);

  const rollback = harness({ candidate: artifact('0.9.0', 10) }); rollback.cleanup(context); await rollback.seed();
  await code('channel_release_rollback', () => rollback.update());

  const offlineExpired = harness({ candidate: artifact('1.1.0', 10, { expires: '2026-08-20T12:00:00Z' }) }); offlineExpired.cleanup(context); await offlineExpired.seed();
  cacheChannel(offlineExpired, offlineExpired.candidate, 10, '2026-08-20T12:00:00Z');
  await code('channel_expired', () => offlineExpired.update({ offline: true }));
  const offlineRollback = harness({ candidate: artifact('0.9.0', 10) }); offlineRollback.cleanup(context); await offlineRollback.seed();
  cacheChannel(offlineRollback, offlineRollback.candidate);
  await code('channel_release_rollback', () => offlineRollback.update({ offline: true }));

  const disk = harness({ freeBytes: 1 }); disk.cleanup(context); await disk.seed();
  await code('insufficient_space', () => disk.update()); assert.equal(disk.calls.includes('quiesce'), false);

  const identity = harness({ priorIdentityMismatch: true }); identity.cleanup(context); await identity.seed();
  await code('prior_runtime_unready', () => identity.update()); assert.equal(exists(identity.layout.updateJournal), false);

  const mismatch = harness({ candidateMismatch: true }); mismatch.cleanup(context); await mismatch.seed();
  await code('update_failed_safe', () => mismatch.update()); assert.match(fs.readlinkSync(mismatch.layout.current), /1\.0\.0-/);
});

test('interrupted pre-preservation update journal is upgraded without discarding prior transaction custody', () => {
  const id = '9'.repeat(32);
  const receipts = Object.fromEntries(updater.RECEIPT_KEYS
    .filter((name) => !['environment_before', 'environment_after'].includes(name)).map((name) => [name, name === 'artifact_verified']));
  const old = {
    schema: updater.UPDATE_SCHEMA, id, requested_channel: 'stable', phase: 'verified',
    prior_selected: '1.0.0-old', prior_running: '1.0.0-old', prior_healthy: '1.0.0-old', candidate: '1.1.0-new',
    config_snapshot: id, service: { was_active: true, was_enabled: true, unit_sha256: 'a'.repeat(64) },
    receipts, failure_code: null, started_at: '2026-08-21T00:00:00Z', updated_at: '2026-08-21T00:00:00Z',
  };
  const upgraded = updater.validateJournal(old);
  assert.deepEqual(upgraded.agent_environment, { before: null, after: null });
  assert.equal(upgraded.receipts.artifact_verified, true);
  assert.equal(upgraded.receipts.environment_before, false); assert.equal(upgraded.receipts.environment_after, false);
});

test('exact AgentEnvironment identity survives candidate success/failure rollback and replacement is detected without container lifecycle calls', async (context) => {
  const exact = { state: 'ready', action: 'none', identity_digest: 'a'.repeat(64), container_id_prefix: '1'.repeat(12), runtime_state: 'running' };
  const success = harness({ environmentStatuses: [exact] }); success.cleanup(context); await success.seed();
  assert.equal((await success.update()).state, 'updated_healthy');
  assert.equal(JSON.parse(fs.readFileSync(path.join(success.layout.agent, 'private', 'preservation.json'))).identity_digest, exact.identity_digest);
  assert.equal(success.calls.some((item) => /container|docker|stop-agent|start-agent/.test(item)), false);

  const failure = harness({ candidateFailure: true, environmentStatuses: [exact] }); failure.cleanup(context); await failure.seed();
  await code('update_failed_safe', () => failure.update());
  assert.equal(JSON.parse(fs.readFileSync(path.join(failure.layout.agent, 'private', 'preservation.json'))).identity_digest, exact.identity_digest);

  const replacement = { ...exact, identity_digest: 'b'.repeat(64), container_id_prefix: '2'.repeat(12) };
  const changed = harness({ environmentStatuses: [exact, replacement] }); changed.cleanup(context); await changed.seed();
  await code('update_failed_needs_repair', () => changed.update());
  assert.equal(JSON.parse(fs.readFileSync(changed.layout.updateJournal)).failure_code, 'agent_environment_identity_mismatch');
});

test('stopped/unhealthy/missing and stale-spec capability states never fail ordinary update when identity is stable', async (context) => {
  for (const state of [
    { state: 'degraded_identity_mismatch', action: 'restore_exact_environment', identity_digest: null, reason_code: 'container_missing' },
    { state: 'degraded_identity_mismatch', action: 'restore_exact_environment', identity_digest: 'c'.repeat(64), runtime_state: 'stopped' },
    { state: 'degraded_identity_mismatch', action: 'restore_exact_environment', identity_digest: 'd'.repeat(64), runtime_state: 'unhealthy' },
    { state: 'stale_spec', action: 'maintenance_rebuild_required', identity_digest: 'e'.repeat(64), runtime_state: 'running' },
  ]) {
    const value = harness({ environmentStatuses: [state] }); value.cleanup(context); await value.seed();
    assert.equal((await value.update()).state, 'updated_healthy', state.reason_code || state.state);
  }
});

test('reachability GC admits 0/1/2/3/100 release inventories and removes only exact unreachable owned releases', async (context) => {
  const empty = harness(); empty.cleanup(context); fs.mkdirSync(empty.layout.releases, { recursive: true, mode: 0o700 });
  assert.deepEqual(updater.collectReleases(empty.layout, null), { removed: 0, bytes: 0 });

  for (const count of [1, 2, 3, 100]) {
    const value = harness(); value.cleanup(context); await value.seed();
    for (let index = 1; index < count; index += 1) await addOwnedRelease(value, artifact(`0.0.${index + 1}`, 1));
    assert.equal(fs.readdirSync(value.layout.releases).length, count);
    const collected = updater.collectReleases(value.layout, null);
    assert.equal(collected.removed, count - 1, String(count));
    assert.equal(fs.readdirSync(value.layout.releases).length, 1, String(count));
  }
});

test('reachability GC refuses unsafe/unowned targets without deleting user/config/model/AgentEnvironment state', async (context) => {
  const value = harness(); value.cleanup(context); await value.seed();
  const sentinels = [path.join(value.layout.appData, 'user'), path.join(value.layout.agent, 'workspace', 'work'), path.join(value.layout.models, 'model')];
  for (const file of sentinels) { fs.mkdirSync(path.dirname(file), { recursive: true, mode: 0o700 }); fs.writeFileSync(file, 'keep'); }
  fs.mkdirSync(path.join(value.layout.releases, 'unowned-looking'), { mode: 0o700 }); fs.writeFileSync(path.join(value.layout.releases, 'unowned-looking', 'content'), 'foreign');
  const result = await value.update(); assert.equal(result.state, 'updated_healthy');
  for (const file of sentinels) assert.equal(fs.readFileSync(file, 'utf8'), 'keep');
  assert.equal(fs.readFileSync(path.join(value.layout.releases, 'unowned-looking', 'content'), 'utf8'), 'foreign');

});

test('CLI exposes only canonical update/offline surface and records/output are content-free', () => {
  assert.deepEqual(launcher.parseCli(['update']), { command: 'update', json: false, offline: false });
  assert.deepEqual(launcher.parseCli(['update', '--offline']), { command: 'update', json: false, offline: true });
  assert.deepEqual(launcher.parseCli(['__post-self-update', 'a'.repeat(32)]), { command: '__post-self-update', json: false, offline: false, transactionId: 'a'.repeat(32) });
  assert.throws(() => launcher.parseCli(['update', '--root', '/tmp/x']));
  assert.throws(() => launcher.parseCli(['update', '--channel', 'beta']));
});
