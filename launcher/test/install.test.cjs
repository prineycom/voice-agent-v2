'use strict';

const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');

const launcher = require('../voice-agent.cjs');
const installer = require('../install.cjs')(launcher);
const NOW = new Date('2026-08-21T00:00:00Z');
const UID = process.geteuid();

function hash(bytes) { return crypto.createHash('sha256').update(bytes).digest('hex'); }
function mode(filename) { return fs.lstatSync(filename).mode & 0o777; }
function errorCode(code, action) { return assert.rejects(action, (reason) => reason && reason.code === code); }
function assetDescriptor(id, kind, bytes) {
  const sha256 = hash(bytes); const image = kind === 'agent_environment_image';
  return { authority: { origin: 'https://assets.example.invalid', path_prefix: '/voice-agent/' }, compatibility: { minimum_launcher_protocol: 1, maximum_launcher_protocol: 1, minimum_application_protocol: 1, maximum_application_protocol: 1 }, digest: `sha256:${sha256}`, id, kind, license: { id: 'Fixture-Test-Only', acceptance: 'accepted' }, platform: launcher.SUPPORTED_PLATFORM, reachability: image ? 'optional' : 'required', required_free_space_reserve: 1024, sha256, size: bytes.length, url: image ? `https://assets.example.invalid/voice-agent/images/environment@sha256:${sha256}` : `https://assets.example.invalid/voice-agent/${kind}/${sha256}` };
}

function fixtureArtifact(overrides = {}) {
  const contents = new Map([
    ['bin/voice-agent-runtime', Buffer.from('#!/bin/sh\nexit 0\n')],
    ['descriptors/local-models.json', Buffer.from('{"llm":"lfm2.5-q4","stt":"whisper-large-v3-turbo","tts":"silero-v5_5_ru-kseniya"}')],
    ['descriptors/runtime.json', Buffer.from('{"cuda":"compatible","livekit":"pinned","provider":"local","fallback":false}')],
  ]);
  const entries = [
    { path: 'bin', type: 'directory', mode: '0555', size: 0, sha256: null, target: null },
    { path: 'bin/voice-agent-runtime', type: 'file', mode: '0555', size: contents.get('bin/voice-agent-runtime').length, sha256: hash(contents.get('bin/voice-agent-runtime')), target: null },
    { path: 'descriptors', type: 'directory', mode: '0555', size: 0, sha256: null, target: null },
    { path: 'descriptors/local-models.json', type: 'file', mode: '0444', size: contents.get('descriptors/local-models.json').length, sha256: hash(contents.get('descriptors/local-models.json')), target: null },
    { path: 'descriptors/runtime.json', type: 'file', mode: '0444', size: contents.get('descriptors/runtime.json').length, sha256: hash(contents.get('descriptors/runtime.json')), target: null },
  ];
  const manifest = {
    application_protocol: { maximum: 1, minimum: 1 }, build_id: 'd'.repeat(40), config_schema: { maximum: 2, minimum: 2 },
    data_schema: { maximum: 2, minimum: 2 }, entries, launcher_protocol: { maximum: 1, minimum: 1 },
    platform: launcher.SUPPORTED_PLATFORM, schema: 'voice-agent.platform-artifact-manifest.v1',
    service_template_sha256: installer.UNIT_CONTRACT_SHA256, version: '1.0.0',
    ...overrides.manifest,
  };
  if (overrides.entries) manifest.entries = overrides.entries(entries);
  const manifestBytes = Buffer.from(launcher.canonicalJson(manifest));
  const artifactBytes = Buffer.from('deterministic signed Linux artifact archive bytes');
  const assetContents = new Map([['model', Buffer.from('model-exact')], ['runtime', Buffer.from('runtime-exact')], ['agent_environment_image', Buffer.from('image-exact')]]);
  const assets = [...assetContents].map(([kind, bytes]) => assetDescriptor(`${kind}-fixture`, kind, bytes));
  const release = {
    artifact_bytes: artifactBytes.length, artifact_sha256: hash(artifactBytes), artifact_url: 'https://releases.example.invalid/voice-agent-1.0.0.tar.zst', assets,
    build_id: manifest.build_id, launcher: null, manifest_sha256: hash(manifestBytes), maximum_data_schema: 2, minimum_data_schema: 2,
    minimum_launcher_protocol: 1, platform: launcher.SUPPORTED_PLATFORM, version: manifest.version,
    ...overrides.release,
  };
  const channel = {
    channel: 'stable', expires_at: '2026-09-20T00:00:00Z', generated_at: '2026-08-20T00:00:00Z',
    releases: [release], schema: 'voice-agent.channel.v1', sequence: 9,
    ...overrides.channel,
  };
  const pair = crypto.generateKeyPairSync('ed25519');
  const channelBytes = Buffer.from(launcher.canonicalJson(channel));
  const signatureBytes = Buffer.from(`${crypto.sign(null, channelBytes, pair.privateKey).toString('base64')}\n`);
  const publicKeyPem = pair.publicKey.export({ type: 'spki', format: 'pem' });
  const archiveEntries = [
    { path: 'release-manifest.json', type: 'file', mode: '0444', size: manifestBytes.length, sha256: hash(manifestBytes), target: null },
    ...manifest.entries,
  ];
  const readEntry = async (name) => contents.get(name);
  return { artifactBytes, manifestBytes, archiveEntries, readEntry, channelBytes, signatureBytes, publicKeyPem, release, manifest, assetContents };
}

function readyDocument(artifact, active = true, optional = { agent_environment: 'disabled', telegram: 'disabled' }) {
  return {
    service_active: active, service_enabled: active, process_uid: UID,
    runtime: {
      release_id: `${artifact.release.version}-${artifact.release.artifact_sha256.slice(0, 12)}`, build_id: artifact.release.build_id,
      accepting: true, provider: 'local', automatic_fallback: false,
      health: { overall_readiness: 'ready', components: installer.REQUIRED_COMPONENTS.map((component) => ({ component, liveness: 'alive', readiness: 'ready', compatible: true })) },
      optional,
    },
    listener: { host: '127.0.0.1', port: 8000, owner_uid: UID, owner: 'service' },
  };
}

function makeHarness(options = {}) {
  const parent = fs.mkdtempSync(path.join(os.tmpdir(), 'voice-agent-install-test-'));
  fs.chmodSync(parent, 0o700);
  const identity = {
    uid: UID, username: 'fixture-user', home: path.join(parent, 'home'), dataHome: path.join(parent, 'data-home'),
    configHome: path.join(parent, 'config-home'), cacheHome: path.join(parent, 'cache-home'), stateHome: path.join(parent, 'state-home'), runtimeHome: path.join(parent, 'runtime-home'),
  };
  const artifact = options.artifact || fixtureArtifact();
  let monotonic = 0;
  const clock = {
    now: () => new Date(NOW), monotonic: () => monotonic,
    sleep: async (milliseconds) => { monotonic += milliseconds; },
  };
  const calls = [];
  let active = false;
  const service = {
    enableLinger: async (request) => { calls.push(['linger', request]); if (options.lingerFailure) throw new launcher.LauncherError('linger_privilege_unavailable', 'fixture'); },
    installUnit: async (request) => { calls.push(['installUnit', request]); fs.mkdirSync(path.dirname(request.path), { recursive: true, mode: 0o700 }); fs.chmodSync(path.dirname(request.path), 0o700); fs.writeFileSync(request.path, request.bytes, { mode: request.mode }); fs.chmodSync(request.path, request.mode); },
    enableAndStart: async (request) => { calls.push(['start', request]); active = true; },
    probe: async () => options.unready ? { ...readyDocument(artifact, false), runtime: { ...readyDocument(artifact, false).runtime, accepting: false } } : readyDocument(artifact, active),
    stop: async (request) => { calls.push(['stop', request]); active = false; },
    disable: async (request) => { calls.push(['disable', request]); },
  };
  const lines = [];
  const host = {
    inspectBase: async () => options.hostFacts || {
      kernel: 'linux', kernel_release: '6.12.0-fixture', architecture: 'x86_64', systemd: true, user: { uid: UID, name: identity.username, home: identity.home },
      nvidia: { available: true, gpu_name: 'NVIDIA GeForce RTX 4070', runtime_compatible: true, driver_version: '610.57.04', vram_bytes: 12 * 1024 ** 3, devices: ['gpu', 'control', 'uvm'] },
    },
    inspectCompatibility: async ({ requirements }) => ({
      free_bytes: options.freeBytes ?? 20 * 1024 ** 3,
      assets: { ...requirements, model_available: true, runtime_available: true, runtime_compatible: true, ...(options.assets || {}) },
    }),
  };
  const source = {
    acquireChannel: async () => ({ channelBytes: artifact.channelBytes, signatureBytes: options.signatureBytes || artifact.signatureBytes, publicKeyPem: artifact.publicKeyPem }),
    acquireArtifact: async () => ({ artifactBytes: artifact.artifactBytes, manifestBytes: artifact.manifestBytes, archiveEntries: options.archiveEntries || artifact.archiveEntries, readEntry: artifact.readEntry }),
    downloadAsset: async ({ descriptor, offset }) => ({ status: offset ? 206 : 200, bytes: (descriptor.kind === 'program' ? artifact.artifactBytes : artifact.assetContents.get(descriptor.kind)).subarray(offset), validator: `fixture-${descriptor.sha256}`, content_range: offset ? `bytes ${offset}-${descriptor.size - 1}/${descriptor.size}` : null, redirected: false, url: descriptor.url }),
  };
  const dependencies = {
    identity, clock, host, source, service, output: { info: (line) => lines.push(line) },
    randomBytes: (length) => Buffer.alloc(length, 0x5a), fault: options.fault,
  };
  const layout = installer.layoutFor(identity, true);
  return { parent, identity, artifact, dependencies, layout, calls, lines, setActive(value) { active = value; } };
}

function cleanup(context, harness) {
  context.after(() => {
    function makeWritable(filename) {
      let metadata;
      try { metadata = fs.lstatSync(filename); } catch { return; }
      if (metadata.isSymbolicLink()) return;
      if (metadata.isDirectory()) {
        fs.chmodSync(filename, 0o700);
        for (const name of fs.readdirSync(filename)) makeWritable(path.join(filename, name));
      } else if (metadata.isFile()) fs.chmodSync(filename, 0o600);
    }
    makeWritable(harness.parent);
    fs.rmSync(harness.parent, { recursive: true, force: true });
  });
}

async function install(harness) { return installer.installVoiceAgent({ testMode: true, dependencies: harness.dependencies }); }

test('pristine install creates safe XDG defaults, immutable exact release, user unit, and five-component ready truth', async (context) => {
  const value = makeHarness(); cleanup(context, value);
  const result = await install(value);
  assert.equal(result.state, 'installed_healthy');
  assert.equal(result.optional.agent_environment, 'disabled');
  assert.equal(fs.readlinkSync(value.layout.current), `releases/${result.release_id}`);
  assert.equal(mode(value.layout.data), 0o700);
  for (const filename of [value.layout.installRecord, path.join(value.layout.config, 'config.yaml'), path.join(value.layout.private, 'service.env'), path.join(value.layout.private, 'credentials.json'), value.layout.unit]) assert.equal(mode(filename), 0o600, filename);
  const releaseRoot = path.join(value.layout.releases, result.release_id);
  assert.equal(mode(releaseRoot), 0o500);
  assert.equal(mode(path.join(releaseRoot, 'release-record.json')), 0o400);
  assert.equal(exists(value.layout.journal), false);
  const config = fs.readFileSync(path.join(value.layout.config, 'config.yaml'), 'utf8');
  assert.match(config, /enabled: false/); assert.match(config, /additional_mounts: \[\]/); assert.match(config, /publish_ports: \[\]/);
  const privateConfig = fs.readFileSync(path.join(value.layout.private, 'service.env'), 'utf8');
  for (const expected of ['VOICE_AGENT_LISTEN_HOST=127.0.0.1', 'VOICE_AGENT_PROVIDER=local', 'VOICE_AGENT_AUTOMATIC_FALLBACK=false', 'VOICE_AGENT_DIAGNOSTIC_CONTENT_CAPTURE=false', 'VOICE_AGENT_AGENT_RUN_ENABLED=false', 'VOICE_AGENT_TELEGRAM_ENABLED=false', 'VOICE_AGENT_DOCKER_HOST=']) assert.match(privateConfig, new RegExp(expected));
  const unit = fs.readFileSync(value.layout.unit, 'utf8');
  assert.match(unit, /WantedBy=default.target/); assert.match(unit, /NoNewPrivileges=yes/); assert.match(unit, /ProtectSystem=strict/); assert.doesNotMatch(unit, /User=|priney|multi-user.target/);
  assert.deepEqual(value.calls.map((item) => item[0]), ['linger', 'installUnit', 'start']);
  assert.match(value.lines.join('\n'), /five-component ready/);
});

function exists(filename) { try { fs.lstatSync(filename); return true; } catch { return false; } }

test('optional AgentEnvironment and Telegram absence degrades independently after ordinary voice success', async (context) => {
  const value = makeHarness(); cleanup(context, value);
  const result = await install(value);
  assert.equal(result.state, 'installed_healthy');
  assert.match(value.lines.at(-1), /agent tools: unavailable/i);
  assert.match(value.lines.at(-1), /Telegram: disabled/);
});

test('repeated identical install probes and reports already healthy without another service action', async (context) => {
  const value = makeHarness(); cleanup(context, value);
  await install(value);
  const before = value.calls.length;
  const result = await install(value);
  assert.equal(result.state, 'already_healthy');
  assert.equal(value.calls.length, before);
});

test('unsupported host stops with one actionable class and zero managed mutation', async (context) => {
  const value = makeHarness({ hostFacts: { kernel: 'darwin', architecture: 'arm64', systemd: false, user: { uid: UID, name: 'fixture-user' }, nvidia: null } }); cleanup(context, value);
  await errorCode('host_unsupported', () => install(value));
  assert.equal(exists(value.layout.data), false);
  assert.equal(value.calls.length, 0);
});

test('forged signature, unsafe archive, incomplete release, and genuine space shortage fail before mutation', async (context) => {
  const cases = [
    ['channel_signature_invalid', { signatureBytes: Buffer.from('A'.repeat(88)) }],
    ['archive_invalid', { archiveEntries: [{ path: '../escape', type: 'file', mode: '0444', size: 0, sha256: hash(Buffer.alloc(0)), target: null }] }],
    ['insufficient_space', { freeBytes: 1 }],
  ];
  for (const [code, options] of cases) {
    const value = makeHarness(options); cleanup(context, value);
    await errorCode(code, () => install(value));
    assert.equal(exists(value.layout.data), false, code);
    assert.equal(value.calls.length, 0, code);
  }
  const incompleteArtifact = fixtureArtifact({ entries: (entries) => entries.filter((entry) => entry.path !== 'descriptors/runtime.json') });
  const incomplete = makeHarness({ artifact: incompleteArtifact }); cleanup(context, incomplete);
  await errorCode('release_incomplete', () => install(incomplete));
  assert.equal(exists(incomplete.layout.data), false);
});

test('missing noninteractive linger privilege leaves no selected/healthy install or service unit', async (context) => {
  const value = makeHarness({ lingerFailure: true }); cleanup(context, value);
  await errorCode('linger_privilege_unavailable', () => install(value));
  assert.equal(exists(value.layout.current), false);
  assert.equal(exists(value.layout.installRecord), false);
  assert.equal(exists(value.layout.unit), false);
  assert.equal(JSON.parse(fs.readFileSync(value.layout.journal)).phase, 'failed');
  assert.deepEqual(value.calls.map((item) => item[0]), ['linger']);
});

test('failed readiness stops/disables candidate and retains only diagnosable unselected staged evidence', async (context) => {
  const value = makeHarness({ unready: true }); cleanup(context, value);
  await errorCode('candidate_not_ready', () => install(value));
  assert.equal(exists(value.layout.current), false);
  assert.equal(exists(value.layout.installRecord), false);
  assert.deepEqual(value.calls.map((item) => item[0]), ['linger', 'installUnit', 'start', 'stop', 'disable']);
  assert.equal(JSON.parse(fs.readFileSync(value.layout.journal)).phase, 'failed');
});

test('interruptions before and after durable service phases resume exactly once and converge healthy', async (context) => {
  for (const interruptedPhase of ['layout_created', 'release_promoted', 'service_started', 'ready_verified', 'healthy']) {
    let fired = false;
    const value = makeHarness({ fault: { afterDurablePhase(phase) { if (!fired && phase === interruptedPhase) { fired = true; throw new launcher.LauncherError('install_interrupted', 'fixture power loss'); } } } });
    cleanup(context, value);
    await errorCode('install_interrupted', () => install(value));
    const result = await install(value);
    assert.equal(result.state, 'installed_healthy', interruptedPhase);
    assert.equal(exists(value.layout.journal), false, interruptedPhase);
    assert.equal(value.calls.filter((item) => item[0] === 'start').length, 1, interruptedPhase);
  }
});

test('partial/foreign/symlinked state routes to doctor and preserves pre-existing data', async (context) => {
  const partial = makeHarness(); cleanup(context, partial);
  fs.mkdirSync(path.join(partial.layout.data, 'data'), { recursive: true, mode: 0o700 });
  const sentinel = path.join(partial.layout.data, 'data', 'keep.txt'); fs.writeFileSync(sentinel, 'keep');
  await errorCode('existing_install_requires_doctor', () => install(partial));
  assert.equal(fs.readFileSync(sentinel, 'utf8'), 'keep');
  assert.equal(partial.calls.length, 0);

  const linked = makeHarness(); cleanup(context, linked);
  fs.mkdirSync(linked.identity.dataHome, { recursive: true, mode: 0o700 });
  const foreign = path.join(linked.parent, 'foreign'); fs.mkdirSync(foreign, { mode: 0o700 });
  fs.symlinkSync(foreign, linked.layout.data);
  await errorCode('path_custody_invalid', () => install(linked));
  assert.equal(fs.readdirSync(foreign).length, 0);

  const unowned = makeHarness(); cleanup(context, unowned);
  unowned.identity.uid = UID + 1;
  unowned.dependencies.host.inspectBase = async () => ({
    kernel: 'linux', kernel_release: '6.12.0-fixture', architecture: 'x86_64', systemd: true,
    user: { uid: UID + 1, name: unowned.identity.username, home: unowned.identity.home },
    nvidia: { available: true, gpu_name: 'NVIDIA GeForce RTX 4070', runtime_compatible: true, driver_version: '610.57.04', vram_bytes: 12 * 1024 ** 3, devices: ['gpu', 'control', 'uvm'] },
  });
  fs.mkdirSync(unowned.layout.data, { recursive: true, mode: 0o700 }); fs.chmodSync(unowned.layout.data, 0o700);
  await errorCode('path_custody_invalid', () => install(unowned));
});

test('production CLI accepts no arbitrary install root and install output/records leak no secret or content bytes', async (context) => {
  assert.throws(() => launcher.parseCli(['install', '--install-root', '/tmp/x']));
  assert.deepEqual(launcher.parseCli(['update']), { command: 'update', json: false, offline: false });
  assert.throws(() => launcher.parseCli(['rollback']));
  const value = makeHarness(); cleanup(context, value);
  await install(value);
  const secretFile = fs.readFileSync(path.join(value.layout.private, 'service.env'), 'utf8');
  const apiSecret = secretFile.match(/^VOICE_AGENT_API_SECRET=(.+)$/m)[1];
  const visible = value.lines.join('\n') + fs.readFileSync(value.layout.installRecord, 'utf8') + fs.readFileSync(path.join(value.layout.releases, fs.readlinkSync(value.layout.current).split('/')[1], 'release-record.json'), 'utf8');
  assert.equal(visible.includes(apiSecret), false);
  assert.equal(visible.includes('LIVEKIT_API_SECRET='), false);
  assert.equal(visible.includes(value.parent), false);
});
