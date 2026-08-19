'use strict';

const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const test = require('node:test');

const ROOT = path.resolve(__dirname, '..', '..');
const launcher = require('../voice-agent.cjs');
const FIXTURES = path.join(ROOT, 'launcher', 'fixtures');
const PUBLIC_KEY = fs.readFileSync(path.join(ROOT, 'launcher', 'keys', 'release-fixture-ed25519-public.pem'));
const NOW = new Date('2026-08-21T00:00:00Z');

function fixture(name) { return fs.readFileSync(path.join(FIXTURES, name)); }
function hash(bytes) { return crypto.createHash('sha256').update(bytes).digest('hex'); }
function expectCode(code, action) {
  assert.throws(action, (error) => error && error.code === code);
}
function channel() {
  return launcher.verifySignedChannel(
    fixture('release-channel.v1.json'), fixture('release-channel.v1.json.sig'), PUBLIC_KEY,
    { now: NOW, trustedSequence: 7 },
  );
}
function manifest() { return JSON.parse(fixture('platform-artifact-manifest.v1.json')); }
function archiveIndex() {
  const bytes = fixture('platform-artifact-manifest.v1.json');
  return [
    { path: 'release-manifest.json', type: 'file', mode: '0444', size: bytes.length, sha256: hash(bytes), target: null },
    ...manifest().entries,
  ];
}

function makeRelease(releases, buildCharacter, unitBytes) {
  const temporary = path.join(releases, `stage-${buildCharacter}`);
  fs.mkdirSync(path.join(temporary, 'config'), { recursive: true, mode: 0o700 });
  fs.mkdirSync(path.join(temporary, 'ops', 'systemd'), { recursive: true, mode: 0o700 });
  fs.mkdirSync(path.join(temporary, 'scripts'), { recursive: true, mode: 0o700 });
  fs.writeFileSync(path.join(temporary, 'config', 'operations-v1.json'), '{"python_runtimes":"historical-incompatible-shape"}\n');
  fs.writeFileSync(path.join(temporary, 'ops', 'systemd', 'voice-agent-v2.service'), unitBytes);
  fs.writeFileSync(path.join(temporary, 'scripts', 'run_slice6.py'), '# legacy fixture\n');
  fs.chmodSync(temporary, 0o700);
  const operations = fs.readFileSync(path.join(temporary, 'config', 'operations-v1.json'));
  const document = {
    schema_version: 'voice-agent.operational-release.v2', release_id: '0'.repeat(24),
    build_id: buildCharacter.repeat(40), source_tree: buildCharacter.toUpperCase().toLowerCase().repeat(40),
    operations_schema: 'voice-agent.operations.v1', operations_manifest_sha256: hash(operations),
    configuration_path: `/private-fixture/${buildCharacter}/do-not-report-secret-marker`,
    configuration_locator_sha256: hash(Buffer.from(`locator-${buildCharacter}`)),
    configuration_fingerprint: hash(Buffer.from(`configuration-${buildCharacter}`)),
    provider_mode: 'local', external_provider_supervised: false, automatic_fallback: false,
    release_tree_sha256: launcher.legacyTreeDigest(temporary),
  };
  document.release_id = launcher.legacyReleaseId(document);
  const release = path.join(releases, document.release_id);
  fs.renameSync(temporary, release);
  fs.writeFileSync(path.join(release, 'release.json'), JSON.stringify(document, null, 2) + '\n', { mode: 0o600 });
  fs.chmodSync(path.join(release, 'release.json'), 0o600);
  return { release, document };
}

function snapshotTree(root) {
  const records = [];
  function walk(current, relative = '.') {
    const metadata = fs.lstatSync(current);
    const record = { relative, mode: metadata.mode & 0o777, size: metadata.size, type: metadata.isDirectory() ? 'd' : metadata.isSymbolicLink() ? 'l' : 'f' };
    if (metadata.isFile()) record.sha256 = hash(fs.readFileSync(current));
    if (metadata.isSymbolicLink()) record.target = fs.readlinkSync(current);
    records.push(record);
    if (metadata.isDirectory()) for (const name of fs.readdirSync(current).sort()) walk(path.join(current, name), relative === '.' ? name : `${relative}/${name}`);
  }
  walk(root);
  return records;
}

function legacyFixture() {
  const parent = fs.mkdtempSync(path.join(os.tmpdir(), 'voice-agent-launcher-test-'));
  const legacyRoot = path.join(parent, 'legacy');
  const installRoot = path.join(parent, 'install');
  const releases = path.join(legacyRoot, 'releases');
  fs.mkdirSync(releases, { recursive: true, mode: 0o700 });
  fs.chmodSync(legacyRoot, 0o700);
  fs.chmodSync(releases, 0o700);
  const unit = Buffer.from('[Unit]\nDescription=fixture\n[Service]\nType=notify\n[Install]\nWantedBy=multi-user.target\n');
  const running = makeRelease(releases, 'b', unit);
  const selected = makeRelease(releases, 'a', unit);
  fs.symlinkSync(`releases/${selected.document.release_id}`, path.join(legacyRoot, 'current'));
  const serviceUnitPath = path.join(parent, 'voice-agent-v2.service');
  fs.writeFileSync(serviceUnitPath, unit);
  const runtimeRoot = path.join(parent, 'runtime');
  fs.mkdirSync(path.join(runtimeRoot, 'bin'), { recursive: true });
  const realPython = path.join(runtimeRoot, 'python-real');
  fs.copyFileSync(process.execPath, realPython);
  fs.chmodSync(realPython, 0o755);
  const expectedPythonPath = path.join(runtimeRoot, 'bin', 'python');
  fs.symlinkSync('../python-real', expectedPythonPath);
  const uid = process.geteuid();
  const snapshot = {
    service: { name: 'voice-agent-v2.service', fragment_path: serviceUnitPath, load: 'loaded', active: 'active', substate: 'running', main_pid: 4242, control_group: '/system.slice/voice-agent-v2.service' },
    process: { pid: 4242, uid, cwd: running.release, executable: fs.realpathSync(expectedPythonPath), argv: [expectedPythonPath, '-B', path.join(running.release, 'scripts', 'run_slice6.py')], control_group: '/system.slice/voice-agent-v2.service' },
    runtime: {
      release_id: running.document.release_id, build_id: running.document.build_id, accepting: true,
      health: { overall_readiness: 'ready', components: ['livekit', 'controller', 'stt', 'selected_llm', 'tts'].map((component) => ({ component, liveness: 'alive', readiness: 'ready', compatible: true })) },
    },
    docker: { endpoint: `unix:///run/user/${uid}/docker.sock`, socket_uid: uid, socket_type: 'socket', rootless: true, daemon_identity_verified: true, explicit_host: true },
    listener: { host: '127.0.0.1', port: 8000, owner_uid: uid, owner: 'service' },
  };
  const serviceProbe = { inspectLegacy: async () => structuredClone(snapshot) };
  return { parent, legacyRoot, installRoot, serviceUnitPath, expectedPythonPath, uid, snapshot, serviceProbe, selected, running };
}

async function statusFor(value, overrides = {}) {
  return launcher.collectStatus({
    installRoot: value.installRoot, legacyRoot: value.legacyRoot, serviceUnitPath: value.serviceUnitPath,
    serviceUnitOwner: value.uid, expectedPythonPath: value.expectedPythonPath, expectedUid: value.uid,
    serviceProbe: value.serviceProbe, home: value.parent, ...overrides,
  });
}

test('signed stable channel is canonical, Ed25519-authenticated, fresh, and monotonic', () => {
  const document = channel();
  assert.equal(document.sequence, 7);
  assert.equal(document.releases[0].platform, launcher.SUPPORTED_PLATFORM);
});

test('forged, wrong-key, expired, rollback, and noncanonical channel metadata fail closed', () => {
  const original = channel();
  const forged = structuredClone(original);
  forged.sequence = 8;
  expectCode('channel_signature_invalid', () => launcher.verifySignedChannel(
    Buffer.from(launcher.canonicalJson(forged)), fixture('release-channel.v1.json.sig'), PUBLIC_KEY, { now: NOW },
  ));
  const wrong = crypto.generateKeyPairSync('ed25519').publicKey.export({ type: 'spki', format: 'pem' });
  expectCode('channel_signature_invalid', () => launcher.verifySignedChannel(
    fixture('release-channel.v1.json'), fixture('release-channel.v1.json.sig'), wrong, { now: NOW },
  ));
  expectCode('channel_expired', () => launcher.verifySignedChannel(
    fixture('release-channel.v1.json'), fixture('release-channel.v1.json.sig'), PUBLIC_KEY, { now: new Date('2026-10-01T00:00:00Z') },
  ));
  expectCode('channel_sequence_rollback', () => launcher.verifySignedChannel(
    fixture('release-channel.v1.json'), fixture('release-channel.v1.json.sig'), PUBLIC_KEY, { now: NOW, trustedSequence: 8 },
  ));
  expectCode('contract_invalid', () => launcher.parseCanonicalJson(Buffer.from('{"b":1,"a":2}')));
});

test('artifact verification binds exact bytes, manifest, platform, and launcher protocol', () => {
  const release = channel().releases[0];
  const observed = launcher.verifyPlatformArtifact(
    fixture('platform-artifact.bin'), fixture('platform-artifact-manifest.v1.json'), release,
  );
  assert.equal(observed.build_id, release.build_id);
  expectCode('artifact_identity_mismatch', () => launcher.verifyPlatformArtifact(
    Buffer.concat([fixture('platform-artifact.bin'), Buffer.from('x')]), fixture('platform-artifact-manifest.v1.json'), release,
  ));
  expectCode('platform_incompatible', () => launcher.verifyPlatformArtifact(
    fixture('platform-artifact.bin'), fixture('platform-artifact-manifest.v1.json'), release, { platform: 'linux-arm64' },
  ));
  expectCode('launcher_protocol_incompatible', () => launcher.verifyPlatformArtifact(
    fixture('platform-artifact.bin'), fixture('platform-artifact-manifest.v1.json'), { ...release, minimum_launcher_protocol: 2 },
  ));
});

test('archive index admits only declared paths and safe regular/directory/internal-link types', () => {
  const bytes = fixture('platform-artifact-manifest.v1.json');
  assert.equal(launcher.validateArchiveEntries(archiveIndex(), manifest(), bytes), true);
  for (const [label, mutate] of [
    ['traversal', (entries) => { entries[1].path = '../escape'; }],
    ['absolute', (entries) => { entries[1].path = '/escape'; }],
    ['symlink escape', (entries) => { entries[3].target = '../../escape'; }],
    ['device', (entries) => { entries[2].type = 'device'; }],
  ]) {
    const entries = structuredClone(archiveIndex());
    mutate(entries);
    assert.throws(() => launcher.validateArchiveEntries(entries, manifest(), bytes), undefined, label);
  }
  const duplicate = structuredClone(archiveIndex());
  duplicate[2].path = duplicate[1].path;
  expectCode('archive_duplicate_entry', () => launcher.validateArchiveEntries(duplicate, manifest(), bytes));
});

test('legacy discovery retains exact ready running-old custody despite selected-new and missing rollback', async (context) => {
  const value = legacyFixture();
  context.after(() => fs.rmSync(value.parent, { recursive: true, force: true }));
  const status = await statusFor(value);
  assert.equal(status.selected.release_id, value.selected.document.release_id);
  assert.equal(status.running.release_id, value.running.document.release_id);
  assert.equal(status.alignment, 'selected-new-running-old');
  assert.equal(status.rollback.state, 'missing');
  assert.equal(status.running.state, 'legacy_unsupported');
  assert.equal(status.running.ready, true);
  assert.equal(status.legacy.adoption_eligible, true);
  assert.deepEqual(status.docker, { state: 'available', endpoint_kind: 'rootless', ownership_verified: true });
});

test('invalid, unowned, or unrelated runtime candidate is never adoption eligible', async (context) => {
  const value = legacyFixture();
  context.after(() => fs.rmSync(value.parent, { recursive: true, force: true }));
  value.snapshot.runtime.release_id = 'c'.repeat(24);
  const unrelated = await statusFor(value);
  assert.equal(unrelated.legacy.adoption_eligible, false);
  assert.equal(unrelated.legacy.state, 'invalid');
  const unowned = await statusFor(value, { expectedUid: value.uid + 1 });
  assert.equal(unowned.legacy.adoption_eligible, false);
  assert.equal(unowned.legacy.state, 'invalid');
});

test('status and doctor are zero-mutation and their JSON/human output is content-free', async (context) => {
  const value = legacyFixture();
  context.after(() => fs.rmSync(value.parent, { recursive: true, force: true }));
  const before = snapshotTree(value.parent);
  const status = await statusFor(value);
  const doctor = await launcher.collectDoctor({
    installRoot: value.installRoot, legacyRoot: value.legacyRoot, serviceUnitPath: value.serviceUnitPath,
    serviceUnitOwner: value.uid, expectedPythonPath: value.expectedPythonPath, expectedUid: value.uid,
    serviceProbe: value.serviceProbe, home: value.parent,
  });
  const after = snapshotTree(value.parent);
  assert.deepEqual(after, before);
  assert.equal(status.read_only, true);
  assert.equal(doctor.read_only, true);
  assert.equal(doctor.repair_performed, false);
  const output = JSON.stringify({ status, doctor, human: launcher.humanStatus(status) + launcher.humanDoctor(doctor) });
  for (const forbidden of ['do-not-report-secret-marker', '/private-fixture/', value.parent, '4242', 'run_slice6.py']) {
    assert.equal(output.includes(forbidden), false, forbidden);
  }
});

test('Node 26 SEA packaging is deterministic and executable without another host runtime', (context) => {
  assert.ok(Number(process.versions.node.split('.')[0]) >= 26, process.versions.node);
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'voice-agent-sea-test-'));
  context.after(() => fs.rmSync(temporary, { recursive: true, force: true }));
  const first = path.join(temporary, 'voice-agent-one');
  const second = path.join(temporary, 'voice-agent-two');
  for (const output of [first, second]) {
    const result = spawnSync(path.join(ROOT, 'launcher', 'build'), [output], {
      cwd: ROOT, encoding: 'utf8', timeout: 30000,
      env: { ...process.env, VOICE_AGENT_SEA_NODE: process.execPath, HOME: path.join(temporary, 'unused-home') },
    });
    assert.equal(result.status, 0, result.stderr);
  }
  assert.equal(hash(fs.readFileSync(first)), hash(fs.readFileSync(second)));
  const home = path.join(temporary, 'fresh-home');
  fs.mkdirSync(home);
  const execution = spawnSync(first, ['doctor', '--json'], { encoding: 'utf8', timeout: 5000, env: { HOME: home, PATH: '/usr/bin:/bin' } });
  assert.equal(execution.status, 0, execution.stderr);
  assert.equal(JSON.parse(execution.stdout).read_only, true);
});

test('all launcher schemas, protocol, public keys, and signed fixtures are bounded public material', () => {
  for (const name of [
    'release-channel.v1.schema.json', 'platform-artifact-manifest.v1.schema.json', 'release-record.v1.schema.json',
    'install-transaction.v1.schema.json', 'update-transaction.v1.schema.json', 'legacy-adoption.v1.schema.json', 'legacy-import-release.v1.schema.json',
    'legacy-config-migration.v1.schema.json', 'docker-endpoint.v1.schema.json', 'config-migrations.v1.schema.json', 'installation.v1.schema.json',
    'launcher-status.v1.schema.json', 'launcher-doctor.v1.schema.json', 'legacy-discovery.v1.schema.json',
    'agent-environment-preservation.v1.schema.json',
  ]) JSON.parse(fs.readFileSync(path.join(ROOT, 'contracts', name), 'utf8'));
  const protocol = JSON.parse(fs.readFileSync(path.join(ROOT, 'config', 'launcher-protocol-v1.json'), 'utf8'));
  assert.equal(protocol.protocol, 1);
  assert.deepEqual(protocol.read_only_commands, ['status', 'doctor']);
  assert.deepEqual(protocol.mutation_commands, ['install', 'update']);
  const releaseRecord = launcher.validateReleaseRecord({
    schema: 'voice-agent.release-record.v1', release_id: '1.2.3-abcdef012345', version: '1.2.3',
    build_id: 'a'.repeat(40), platform: 'linux-x86_64-nvidia', channel: 'stable', channel_sequence: 7,
    artifact_sha256: 'b'.repeat(64), artifact_bytes: 42, manifest_sha256: 'c'.repeat(64), launcher_protocol: 1,
    application_protocol: { minimum: 1, maximum: 1 }, data_schema: { minimum: 2, maximum: 3 },
    service_template_sha256: 'd'.repeat(64), verified_at: '2026-08-21T00:00:00Z',
    readiness: { state: 'not_verified', checked_at: null },
  });
  assert.equal(releaseRecord.readiness.state, 'not_verified');
  const trackedNames = spawnSync('git', ['ls-files', 'launcher'], { cwd: ROOT, encoding: 'utf8' }).stdout;
  assert.equal(/private.*(?:pem|key)|\.key\b/i.test(trackedNames), false);
  assert.equal(channel().schema, 'voice-agent.channel.v1');
});
