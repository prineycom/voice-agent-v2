'use strict';

const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');

const core = require('../voice-agent.cjs');
const installer = require('../install.cjs')(core);
const updater = require('../update.cjs')(core, installer);
const adopter = require('../adopt.cjs')(core, installer, updater);
const UID = process.geteuid();
const NOW = new Date('2026-08-21T00:00:00Z');

function hash(bytes) { return crypto.createHash('sha256').update(bytes).digest('hex'); }
function exists(filename) { try { fs.lstatSync(filename); return true; } catch { return false; } }
function code(expected, action) { return assert.rejects(action, (reason) => reason && reason.code === expected); }

function candidateArtifact(options = {}) {
  const contents = new Map([
    ['bin/voice-agent-runtime', Buffer.from('#!/bin/sh\nexit 0\n')],
    ['descriptors/local-models.json', Buffer.from('{"models":"exact"}')],
    ['descriptors/runtime.json', Buffer.from('{"runtime":"exact"}')],
  ]);
  const entries = [
    { path: 'bin', type: 'directory', mode: '0555', size: 0, sha256: null, target: null },
    { path: 'bin/voice-agent-runtime', type: 'file', mode: '0555', size: contents.get('bin/voice-agent-runtime').length, sha256: hash(contents.get('bin/voice-agent-runtime')), target: null },
    { path: 'descriptors', type: 'directory', mode: '0555', size: 0, sha256: null, target: null },
    ...[...contents.entries()].filter(([name]) => name.startsWith('descriptors/')).map(([name, bytes]) => ({ path: name, type: 'file', mode: '0444', size: bytes.length, sha256: hash(bytes), target: null })),
  ];
  const manifest = {
    application_protocol: { minimum: 1, maximum: 1 }, build_id: 'd'.repeat(40), config_schema: { minimum: 2, maximum: 2 },
    data_schema: { minimum: 2, maximum: 2 }, entries, launcher_protocol: { minimum: 1, maximum: 1 },
    platform: core.SUPPORTED_PLATFORM, schema: 'voice-agent.platform-artifact-manifest.v1',
    service_template_sha256: installer.UNIT_CONTRACT_SHA256, version: '1.0.0',
  };
  const manifestBytes = Buffer.from(core.canonicalJson(manifest)); const artifactBytes = Buffer.from('signed-adoption-candidate');
  const release = {
    artifact_bytes: artifactBytes.length, artifact_sha256: hash(artifactBytes), artifact_url: 'https://releases.example.invalid/1.0.0.tar.zst',
    build_id: manifest.build_id, manifest_sha256: hash(manifestBytes), maximum_data_schema: 2, minimum_data_schema: 2,
    minimum_launcher_protocol: 1, platform: core.SUPPORTED_PLATFORM, version: '1.0.0',
  };
  const channel = { channel: 'stable', expires_at: '2026-09-20T00:00:00Z', generated_at: '2026-08-20T00:00:00Z', releases: [release], schema: 'voice-agent.channel.v1', sequence: 11 };
  const pair = crypto.generateKeyPairSync('ed25519'); const channelBytes = Buffer.from(core.canonicalJson(channel));
  return {
    artifactBytes, manifestBytes, manifest, release, channelBytes,
    signatureBytes: Buffer.from(`${crypto.sign(null, channelBytes, pair.privateKey).toString('base64')}\n`),
    publicKeyPem: pair.publicKey.export({ type: 'spki', format: 'pem' }),
    archiveEntries: [{ path: 'release-manifest.json', type: 'file', mode: '0444', size: manifestBytes.length, sha256: hash(manifestBytes), target: null }, ...entries],
    readEntry: async (name) => contents.get(name), ...options,
  };
}

function makeLegacyRelease(releases, buildCharacter, unit, configPath, operations = '{"python_runtimes":"historical-incompatible-shape"}\n') {
  const temporary = path.join(releases, `stage-${buildCharacter}`);
  fs.mkdirSync(path.join(temporary, 'config'), { recursive: true, mode: 0o700 });
  fs.mkdirSync(path.join(temporary, 'ops', 'systemd'), { recursive: true, mode: 0o700 });
  fs.mkdirSync(path.join(temporary, 'scripts'), { recursive: true, mode: 0o700 });
  fs.writeFileSync(path.join(temporary, 'config', 'operations-v1.json'), operations);
  fs.writeFileSync(path.join(temporary, 'ops', 'systemd', 'voice-agent-v2.service'), unit);
  fs.writeFileSync(path.join(temporary, 'scripts', 'run_slice6.py'), '# fixture\n');
  const operationsBytes = fs.readFileSync(path.join(temporary, 'config', 'operations-v1.json'));
  const document = {
    schema_version: 'voice-agent.operational-release.v2', release_id: '0'.repeat(24), build_id: buildCharacter.repeat(40), source_tree: buildCharacter.repeat(40),
    operations_schema: 'voice-agent.operations.v1', operations_manifest_sha256: hash(operationsBytes), configuration_path: configPath,
    configuration_locator_sha256: hash(Buffer.from(configPath)), configuration_fingerprint: hash(Buffer.from('shared-config')),
    provider_mode: 'local', external_provider_supervised: false, automatic_fallback: false, release_tree_sha256: core.legacyTreeDigest(temporary),
  };
  document.release_id = core.legacyReleaseId(document); const release = path.join(releases, document.release_id);
  fs.renameSync(temporary, release); fs.writeFileSync(path.join(release, 'release.json'), JSON.stringify(document, null, 2) + '\n', { mode: 0o600 }); fs.chmodSync(path.join(release, 'release.json'), 0o600);
  return { release, document };
}

function readyCandidate(artifact, active, fail = false) {
  return {
    service_active: active, service_enabled: true, process_uid: UID,
    runtime: { release_id: `${artifact.release.version}-${artifact.release.artifact_sha256.slice(0, 12)}`, build_id: artifact.release.build_id,
      accepting: active && !fail, provider: 'local', automatic_fallback: false,
      health: { overall_readiness: active && !fail ? 'ready' : 'unready', components: installer.REQUIRED_COMPONENTS.map((component) => ({ component, liveness: 'alive', readiness: active && !fail ? 'ready' : 'unavailable', compatible: true })) } },
    listener: { host: '127.0.0.1', port: 8000, owner_uid: UID, owner: 'service' },
  };
}

function harness(options = {}) {
  const parent = fs.mkdtempSync(path.join(os.tmpdir(), 'voice-agent-adopt-test-')); fs.chmodSync(parent, 0o700);
  const identity = { uid: UID, username: 'fixture-user', home: path.join(parent, 'home'), dataHome: path.join(parent, 'data'), configHome: path.join(parent, 'config'), cacheHome: path.join(parent, 'cache'), stateHome: path.join(parent, 'state'), runtimeHome: path.join(parent, 'runtime') };
  const layout = installer.layoutFor(identity, true); const legacyRoot = path.join(parent, 'legacy'); const releases = path.join(legacyRoot, 'releases');
  fs.mkdirSync(releases, { recursive: true, mode: 0o700 }); fs.chmodSync(legacyRoot, 0o700); fs.chmodSync(releases, 0o700);
  const configPath = path.join(parent, 'legacy-service.env'); const secret = 'PRIVATE_SECRET=do-not-leak-or-change\nLIVEKIT_API_KEY=legacy-key\n';
  fs.writeFileSync(configPath, secret, { mode: 0o600 }); fs.chmodSync(configPath, 0o600);
  const unit = Buffer.from('[Unit]\nDescription=legacy\n[Service]\nType=notify\n[Install]\nWantedBy=multi-user.target\n');
  const running = makeLegacyRelease(releases, 'b', unit, configPath, options.operations);
  const selected = makeLegacyRelease(releases, 'a', unit, configPath, options.operations);
  fs.symlinkSync(`releases/${selected.document.release_id}`, path.join(legacyRoot, 'current'));
  const serviceUnitPath = path.join(parent, 'voice-agent-v2.service'); fs.writeFileSync(serviceUnitPath, unit, { mode: 0o644 });
  const runtime = process.execPath;
  const artifact = candidateArtifact(); const calls = []; const lines = []; let oldActive = true; let candidateActive = false; let candidateEnabled = false; let retired = false; let monotonic = 0;
  function legacySnapshot() {
    const docker = options.noDocker ? null : {
      endpoint: options.rootfulDocker ? 'unix:///var/run/docker.sock' : `unix:///run/user/${UID}/docker.sock`,
      socket_path: options.rootfulDocker ? '/var/run/docker.sock' : `/run/user/${UID}/docker.sock`,
      socket_uid: options.foreignDocker ? UID + 1 : UID, socket_type: 'socket', socket_mode: 0o600,
      socket_device: 31, socket_inode: 42, rootless: !options.rootfulDocker, daemon_uid: options.foreignDocker ? UID + 1 : UID,
      daemon_identity: 'fixture-rootless-daemon', user_namespace: options.rootfulDocker ? 'host' : 'rootless', cgroup_version: 2,
      cgroup_driver: 'systemd', ambient_context_used: false, explicit_host: true,
    };
    if (!oldActive) return { service: { name: 'voice-agent-v2.service', fragment_path: serviceUnitPath, active: 'inactive', main_pid: 0, control_group: '/system.slice/voice-agent-v2.service' }, process: null, runtime: null, docker, listener: { host: null, port: null, owner_uid: null, owner: 'unknown' } };
    const snapshot = {
      service: { name: 'voice-agent-v2.service', fragment_path: serviceUnitPath, load: 'loaded', active: 'active', substate: 'running', main_pid: 4242, control_group: '/system.slice/voice-agent-v2.service' },
      process: { pid: 4242, uid: UID, cwd: running.release, executable: fs.realpathSync(runtime), argv: [runtime, '-B', path.join(running.release, 'scripts', 'run_slice6.py')], control_group: '/system.slice/voice-agent-v2.service' },
      runtime: { release_id: running.document.release_id, build_id: running.document.build_id, accepting: true,
        health: { overall_readiness: 'ready', components: installer.REQUIRED_COMPONENTS.map((component) => ({ component, liveness: 'alive', readiness: 'ready', compatible: true })) } }, docker,
      listener: { host: '127.0.0.1', port: 8000, owner_uid: UID, owner: 'service' },
    };
    if (options.mutateSnapshot) options.mutateSnapshot(snapshot);
    return snapshot;
  }
  const service = {
    enableLinger: async () => calls.push('linger'),
    installUnit: async (request) => { calls.push('unit'); fs.mkdirSync(path.dirname(request.path), { recursive: true, mode: 0o700 }); fs.chmodSync(path.dirname(request.path), 0o700); fs.writeFileSync(request.path, request.bytes, { mode: 0o600 }); fs.chmodSync(request.path, 0o600); },
    enable: async () => { calls.push('candidate-enable'); candidateEnabled = true; }, isEnabled: async () => candidateEnabled,
    startExactlyOnce: async () => { calls.push('candidate-start'); candidateActive = true; },
    probe: async () => readyCandidate(artifact, candidateActive, options.candidateFailure),
    stop: async () => { calls.push('candidate-stop'); candidateActive = false; },
    disable: async () => { calls.push('candidate-disable'); candidateEnabled = false; },
  };
  const dependencies = {
    identity, service, legacyProbe: { inspectLegacy: async () => legacySnapshot() },
    legacy: {
      quiesce: async () => { calls.push('legacy-stop'); oldActive = false; },
      restore: async () => { calls.push('legacy-restore'); if (options.priorFailure) throw new core.LauncherError('legacy_service_restore_failed', 'fixture'); oldActive = true; },
      retire: async () => { calls.push('legacy-retire'); retired = true; }, retired: async () => retired,
    },
    host: {
      inspectBase: async () => ({
        kernel: 'linux', kernel_release: '6.12.0-fixture', architecture: 'x86_64', systemd: true,
        user: { uid: UID, name: identity.username, home: identity.home },
        nvidia: { available: true, gpu_name: 'NVIDIA GeForce RTX 4070', runtime_compatible: true, driver_version: '610.57.04', vram_bytes: 12 * 1024 ** 3, devices: ['gpu', 'control', 'uvm'] },
      }),
      inspectCompatibility: async ({ requirements }) => ({ free_bytes: 20 * 1024 ** 3, assets: { ...requirements, model_available: true, runtime_available: true, runtime_compatible: true } }),
    },
    source: { acquireChannel: async () => artifact, acquireArtifact: async () => artifact },
    clock: { now: () => new Date(NOW), monotonic: () => monotonic, sleep: async (ms) => { monotonic += ms; } },
    randomBytes: (length) => Buffer.alloc(length, 0x7c), output: { info: (line) => lines.push(line) }, fault: options.fault || null,
  };
  if (options.environmentStatus) dependencies.agentEnvironment = { capture: async () => options.environmentStatus };
  const adoptOptions = { testMode: true, dependencies, legacyRoot, serviceUnitPath, serviceUnitOwner: UID, expectedPythonPath: runtime };
  function cleanup(context) { context.after(() => { function writable(file) { if (!exists(file)) return; const metadata = fs.lstatSync(file); if (metadata.isSymbolicLink()) return; if (metadata.isDirectory()) { fs.chmodSync(file, 0o700); for (const name of fs.readdirSync(file)) writable(path.join(file, name)); } else fs.chmodSync(file, 0o600); } writable(parent); fs.rmSync(parent, { recursive: true, force: true }); }); }
  return { parent, identity, layout, legacyRoot, configPath, secret, running, selected, serviceUnitPath, runtime, artifact, calls, lines, dependencies, adoptOptions, cleanup, snapshot: legacySnapshot };
}

async function adopt(value) { return adopter.installVoiceAgent(value.adoptOptions); }

test('interrupted pre-preservation adoption journal upgrades with exact prior/candidate custody intact', () => {
  const id = '8'.repeat(32);
  const receipts = Object.fromEntries(adopter.RECEIPTS
    .filter((name) => !['environment_before', 'environment_after'].includes(name)).map((name) => [name, name === 'candidate_staged']));
  const old = {
    schema: adopter.ADOPTION_SCHEMA, id, phase: 'prepared', prior_running: `legacy-${'1'.repeat(24)}`,
    prior_healthy: `legacy-${'1'.repeat(24)}`, legacy_candidate: `legacy-${'2'.repeat(24)}`,
    candidate: '1.0.0-candidate', config_source_sha256: 'a'.repeat(64), config_canonical_sha256: 'b'.repeat(64),
    receipts, failure_code: null, started_at: '2026-08-21T00:00:00Z', updated_at: '2026-08-21T00:00:00Z',
  };
  const upgraded = adopter.validateJournal(old);
  assert.deepEqual(upgraded.agent_environment, { before: null, after: null });
  assert.equal(upgraded.receipts.candidate_staged, true);
  assert.equal(upgraded.receipts.environment_before, false); assert.equal(upgraded.receipts.environment_after, false);
});

test('exact selected-new/running-old split adopts healthy prior, uncommitted legacy candidate, private config, rootless endpoint, and signed candidate', async (context) => {
  const value = harness(); value.cleanup(context); const beforeLegacy = fs.readFileSync(value.configPath);
  const result = await adopt(value);
  assert.equal(result.state, 'legacy_adopted_healthy');
  assert.equal(fs.readlinkSync(value.layout.current), `releases/${result.release_id}`);
  assert.equal(fs.readlinkSync(value.layout.rollback), `releases/legacy-${value.running.document.release_id}`);
  assert.equal(core.validateLegacyImportRecord(JSON.parse(fs.readFileSync(path.join(value.layout.releases, result.rollback_release, 'legacy-import-record.json')))).readiness, 'ready');
  assert.equal(core.validateLegacyImportRecord(JSON.parse(fs.readFileSync(path.join(value.layout.releases, result.legacy_candidate, 'legacy-import-record.json')))).readiness, 'not_verified');
  const migrated = fs.readFileSync(path.join(value.layout.private, 'service.env'));
  assert.ok(migrated.subarray(0, beforeLegacy.length).equals(beforeLegacy)); assert.match(migrated.toString(), /VOICE_AGENT_AGENT_RUN_ENABLED=false/); assert.match(migrated.toString(), new RegExp(`VOICE_AGENT_DOCKER_HOST=unix:///run/user/${UID}/docker.sock`));
  assert.equal(fs.readFileSync(value.configPath).equals(beforeLegacy), true); assert.equal(fs.lstatSync(path.join(value.layout.private, 'service.env')).mode & 0o777, 0o600);
  assert.match(fs.readFileSync(path.join(value.layout.config, 'config.yaml'), 'utf8'), /agent:\n  enabled: false/);
  assert.equal(exists(path.join(value.layout.private, 'credentials.json')), false); assert.equal(exists(path.join(value.layout.agent, 'private', 'registry.json')), false);
  for (const imported of [result.rollback_release, result.legacy_candidate]) assert.equal(exists(path.join(value.layout.releases, imported, 'payload', 'release.json')), false);
  assert.equal([...fs.readdirSync(path.join(value.layout.releases, result.rollback_release, 'payload'))].join('\n').includes(value.configPath), false);
  assert.deepEqual(value.calls, ['linger', 'unit', 'candidate-enable', 'legacy-stop', 'candidate-start', 'legacy-retire']);
  assert.equal(exists(path.join(value.layout.transactions, 'adoption.json')), false); assert.equal(fs.readdirSync(value.layout.migrations).length, 0);
  assert.equal(value.lines.join('\n').includes('do-not-leak'), false);
  const again = await adopt(value); assert.equal(again.state, 'already_healthy'); assert.equal(value.calls.length, 6);
});

test('historical manifest shape does not erase custody, while process/unit/readiness/release mismatches fail before service action', async (context) => {
  const compatible = harness({ operations: '{"schema":"historical-compatible-control"}\n' }); compatible.cleanup(context); assert.equal((await adopt(compatible)).state, 'legacy_adopted_healthy');
  const mutations = [
    (snapshot) => { snapshot.service.main_pid = 99; },
    (snapshot) => { snapshot.process.cwd = snapshot.process.cwd + '-wrong'; },
    (snapshot) => { snapshot.process.argv[2] += '-wrong'; },
    (snapshot) => { snapshot.runtime.build_id = 'f'.repeat(40); },
    (snapshot) => { snapshot.runtime.health.components[0].readiness = 'unavailable'; },
  ];
  for (const mutateSnapshot of mutations) {
    const value = harness({ mutateSnapshot }); value.cleanup(context); await assert.rejects(() => adopt(value)); assert.equal(value.calls.length, 0);
  }
  const unit = harness(); unit.cleanup(context); fs.appendFileSync(unit.serviceUnitPath, '# tamper\n'); await assert.rejects(() => adopt(unit)); assert.equal(unit.calls.length, 0);
  const release = harness(); release.cleanup(context); fs.appendFileSync(path.join(release.running.release, 'scripts', 'run_slice6.py'), '# tamper\n'); await assert.rejects(() => adopt(release)); assert.equal(release.calls.length, 0);
});

test('canonical config conflict and rootful/foreign Docker are refused; absent Docker records no endpoint and never creates a container', async (context) => {
  const conflict = harness(); conflict.cleanup(context); installer.installDirectories(conflict.layout); fs.writeFileSync(path.join(conflict.layout.private, 'service.env'), 'UNEXPECTED=value\n', { mode: 0o600 });
  await code('canonical_config_conflict', () => adopt(conflict)); assert.equal(conflict.calls.length, 0); assert.equal(fs.readFileSync(path.join(conflict.layout.private, 'service.env'), 'utf8'), 'UNEXPECTED=value\n');
  for (const options of [{ rootfulDocker: true }, { foreignDocker: true }]) { const value = harness(options); value.cleanup(context); await code('legacy_docker_endpoint_invalid', () => adopt(value)); assert.equal(value.calls.length, 0); }
  const absent = harness({ noDocker: true }); absent.cleanup(context); await adopt(absent);
  const endpoint = JSON.parse(fs.readFileSync(path.join(absent.layout.private, 'docker-endpoint.json'))); assert.equal(endpoint.endpoint, null); assert.match(fs.readFileSync(path.join(absent.layout.private, 'service.env'), 'utf8'), /VOICE_AGENT_DOCKER_HOST=\n/);
  assert.equal(absent.calls.some((item) => /docker|container|pull|create/.test(item)), false);
});

test('legacy adoption preserves the exact existing AgentEnvironment identity without lifecycle mutation', async (context) => {
  const environmentStatus = { state: 'ready', action: 'none', identity_digest: '9'.repeat(64), container_id_prefix: '8'.repeat(12), runtime_state: 'running' };
  const value = harness({ environmentStatus }); value.cleanup(context);
  assert.equal((await adopt(value)).state, 'legacy_adopted_healthy');
  const receipt = JSON.parse(fs.readFileSync(path.join(value.layout.agent, 'private', 'preservation.json')));
  assert.equal(receipt.identity_digest, environmentStatus.identity_digest);
  assert.equal(value.calls.some((item) => /docker|container|agent-stop|agent-start/.test(item)), false);
});

test('candidate failure restores exact old service; double failure remains explicit with recovery evidence', async (context) => {
  const safe = harness({ candidateFailure: true }); safe.cleanup(context); await code('update_failed_safe', () => adopt(safe));
  assert.deepEqual(safe.calls.slice(-3), ['candidate-stop', 'candidate-disable', 'legacy-restore']); assert.equal(safe.snapshot().runtime.release_id, safe.running.document.release_id);
  const journal = JSON.parse(fs.readFileSync(path.join(safe.layout.transactions, 'adoption.json'))); assert.equal(journal.phase, 'failed_safe'); assert.equal(exists(path.join(safe.layout.migrations, journal.id)), true);
  const broken = harness({ candidateFailure: true, priorFailure: true }); broken.cleanup(context); await code('update_failed_needs_repair', () => adopt(broken));
  const retained = fs.readFileSync(path.join(broken.layout.transactions, 'adoption.json'), 'utf8'); assert.equal(JSON.parse(retained).phase, 'failed_needs_repair'); assert.equal(retained.includes(broken.parent), false); assert.equal(exists(path.join(broken.layout.migrations, '7c'.repeat(16))), true);
});

test('interruption after every durable write/action converges without duplicate service actions, config copy, release, or container', async (context) => {
  const environmentStatus = { state: 'ready', action: 'none', identity_digest: '7'.repeat(64), container_id_prefix: '6'.repeat(12), runtime_state: 'running' };
  const survey = harness({ environmentStatus }); survey.cleanup(context); const events = [];
  survey.dependencies.fault = { afterDurablePhase: (name) => events.push(`write:${name}`), afterAction: (name) => events.push(`action:${name}`) };
  await adopt(survey);
  for (const event of [...new Set(events)]) {
    const value = harness({ environmentStatus }); value.cleanup(context); let fired = false;
    value.dependencies.fault = {
      afterDurablePhase(name) { if (!fired && event === `write:${name}`) { fired = true; throw new core.LauncherError('legacy_adoption_interrupted', 'fixture'); } },
      afterAction(name) { if (!fired && event === `action:${name}`) { fired = true; throw new core.LauncherError('legacy_adoption_interrupted', 'fixture'); } },
    };
    await code('legacy_adoption_interrupted', () => adopt(value)); value.dependencies.fault = null;
    const result = await adopt(value); assert.equal(result.state, 'legacy_adopted_healthy', event);
    for (const action of ['legacy-stop', 'candidate-start', 'legacy-retire']) assert.ok(value.calls.filter((item) => item === action).length <= 1, `${event}:${action}`);
    assert.equal(fs.readdirSync(value.layout.releases).length, 3, event);
    assert.equal(fs.readFileSync(path.join(value.layout.private, 'service.env'), 'utf8').match(/PRIVATE_SECRET=/g).length, 1, event);
    assert.equal(value.calls.some((item) => /container|pull|create/.test(item)), false, event);
  }
});
