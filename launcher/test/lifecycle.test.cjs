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
const lifecycle = require('../lifecycle.cjs')(launcher, installer, updater);
const agentEnvironment = require('../agent-environment.cjs')(launcher);
const UID = process.geteuid();
const NOW = new Date('2026-08-22T00:00:00Z');

function exists(filename) { try { fs.lstatSync(filename); return true; } catch { return false; } }
function reject(code, action) { return assert.rejects(action, (reason) => reason && reason.code === code); }
function fixture(context, options = {}) {
  const parent = fs.mkdtempSync(path.join(os.tmpdir(), 'voice-agent-lifecycle-test-')); fs.chmodSync(parent, 0o700);
  const identity = { uid: UID, username: 'fixture-user', home: path.join(parent, 'home'), dataHome: path.join(parent, 'data-home'), configHome: path.join(parent, 'config-home'), cacheHome: path.join(parent, 'cache-home'), stateHome: path.join(parent, 'state-home'), runtimeHome: path.join(parent, 'runtime-home') };
  const layout = installer.layoutFor(identity, true);
  for (const directory of [layout.data, layout.releases, layout.transactions, layout.migrations, layout.appData, layout.agent, path.join(layout.agent, 'private'), path.join(layout.agent, 'workspace'), path.join(layout.agent, 'cache'), layout.agentRootfs, layout.config, layout.private, layout.cache, layout.downloads, path.dirname(layout.models), layout.models, path.dirname(layout.runtimes), layout.runtimes, path.dirname(layout.launchers), layout.launchers, layout.state, layout.logs, layout.diagnostics, layout.runtime, path.dirname(layout.launcher)]) installer.ensurePrivateDirectory(directory, UID);
  fs.writeFileSync(layout.launcher, 'fixture launcher', { mode: 0o755 });
  fs.writeFileSync(path.join(layout.downloads, 'program.partial'), 'program');
  fs.writeFileSync(path.join(layout.runtimes, 'runtime'), 'runtime', { mode: 0o400 });
  fs.writeFileSync(path.join(layout.models, 'model'), 'model', { mode: 0o400 });
  fs.writeFileSync(path.join(layout.appData, 'conversation.media'), 'media');
  fs.writeFileSync(path.join(layout.config, 'config.yaml'), 'schema_version: voice-agent.config.v2\n', { mode: 0o600 });
  fs.writeFileSync(path.join(layout.private, 'service.env'), 'SECRET_FIXTURE=preserve-me\n', { mode: 0o600 });
  fs.writeFileSync(path.join(layout.agent, 'workspace', 'work.txt'), 'workspace');
  fs.writeFileSync(path.join(layout.logs, 'metadata.log'), 'code-only');
  const calls = []; const lines = []; let enabled = false;
  const dependencies = {
    identity, clock: { now: () => new Date(NOW) }, randomBytes: (length) => Buffer.alloc(length, 0x4c), output: { info: (line) => lines.push(line) }, fault: options.fault,
    confirm: options.confirm || (async (request) => request.phrase),
    service: {
      stopExact: async () => calls.push('stop'), disableExact: async () => { calls.push('disable'); enabled = false; },
      isEnabled: async () => enabled, removeUnit: async () => calls.push('unit-remove'),
    },
    lock: { acquire: () => ({ release() {} }) },
  };
  context.after(() => { function writable(filename) { if (!exists(filename)) return; const meta = fs.lstatSync(filename); if (meta.isSymbolicLink()) return; if (meta.isDirectory()) { fs.chmodSync(filename, 0o700); for (const name of fs.readdirSync(filename)) writable(path.join(filename, name)); } else fs.chmodSync(filename, 0o600); } writable(parent); fs.rmSync(parent, { recursive: true, force: true }); });
  return { parent, identity, layout, dependencies, calls, lines };
}

function uninstall(value, arguments_ = {}) { return lifecycle.uninstallVoiceAgent({ testMode: true, dependencies: value.dependencies, arguments: { yes: true, dryRun: false, purgeProgramCache: false, purgeModels: false, purgeAgentEnvironment: false, confirmAgentDataLoss: false, purgeAll: false, confirmDataLoss: false, ...arguments_ } }); }

function preservationSentinels(value) { return [path.join(value.layout.config, 'config.yaml'), path.join(value.layout.private, 'service.env'), path.join(value.layout.models, 'model'), path.join(value.layout.appData, 'conversation.media'), path.join(value.layout.agent, 'workspace', 'work.txt')]; }

test('default uninstall inventories first, orders service disable before owned bytes, and preserves every durable category and linger', async (context) => {
  const value = fixture(context); const sentinels = preservationSentinels(value);
  const result = await uninstall(value);
  assert.equal(result.state, 'uninstalled');
  assert.deepEqual(value.calls, ['stop', 'disable']);
  assert.equal(exists(value.layout.launcher), false); assert.equal(exists(value.layout.downloads), false);
  for (const filename of sentinels) assert.equal(exists(filename), true, filename);
  assert.match(value.lines[0], /Preserved by default/); assert.match(value.lines[1], /Dry-run inventory/); assert.match(value.lines.at(-1), /linger remains unchanged/);
  assert.equal(JSON.parse(fs.readFileSync(value.layout.lifecycleResult)).state, 'uninstalled');
});

test('uninstall explicit typed CLI flags are closed and confirmations are non-ambiguous', () => {
  const base = launcher.parseCli(['uninstall', '--yes']); assert.equal(base.command, 'uninstall'); assert.equal(base.yes, true);
  assert.equal(launcher.parseCli(['uninstall', '--purge-models', '--yes']).purgeModels, true);
  assert.equal(launcher.parseCli(['uninstall', '--purge-agent-environment', '--confirm-agent-data-loss', '--yes']).purgeAgentEnvironment, true);
  assert.equal(launcher.parseCli(['uninstall', '--purge-all', '--confirm-agent-data-loss', '--confirm-data-loss', '--yes']).purgeAll, true);
  assert.throws(() => launcher.parseCli(['uninstall', '--purge-agent-environment']));
  assert.throws(() => launcher.parseCli(['uninstall', '--purge-all', '--confirm-data-loss']));
  assert.throws(() => launcher.parseCli(['uninstall', '--path', '/tmp/arbitrary']));
});

test('dry run and rejected typed confirmation perform no mutation', async (context) => {
  const dry = fixture(context); const result = await uninstall(dry, { yes: false, dryRun: true }); assert.equal(result.state, 'dry_run'); assert.equal(exists(dry.layout.launcher), true); assert.deepEqual(dry.calls, []);
  const denied = fixture(context, { confirm: async () => 'yes' }); denied.dependencies.confirm = async () => 'yes';
  await reject('confirmation_required', () => uninstall(denied, { yes: false })); assert.equal(exists(denied.layout.launcher), true); assert.deepEqual(denied.calls, []);
});

test('program cache and model purge delete only their exact roots', async (context) => {
  const cache = fixture(context); await uninstall(cache, { purgeProgramCache: true });
  assert.equal(exists(cache.layout.runtimes), false); assert.equal(exists(path.join(cache.layout.models, 'model')), true); assert.equal(exists(path.join(cache.layout.agent, 'workspace', 'work.txt')), true);
  const models = fixture(context); await uninstall(models, { purgeModels: true });
  assert.equal(exists(models.layout.models), false); assert.equal(exists(models.layout.runtimes), true); assert.equal(exists(path.join(models.layout.appData, 'conversation.media')), true);
});

test('AgentEnvironment purge requires immediate exact rootless endpoint/container owner reinspection', async (context) => {
  const denied = fixture(context); fs.writeFileSync(path.join(denied.layout.agent, 'private', 'registry.json'), JSON.stringify({ schema_version: 'voice-agent.agent-environment-registry.v1', selected_container_id: 'a'.repeat(64), owner_key: 'owner-fixture' }), { mode: 0o600 });
  await reject('rootless_docker_required', () => uninstall(denied, { purgeAgentEnvironment: true, confirmAgentDataLoss: true }));
  assert.equal(exists(path.join(denied.layout.agent, 'workspace', 'work.txt')), true);

  const value = fixture(context); const registry = { schema_version: 'voice-agent.agent-environment-registry.v1', selected_container_id: 'b'.repeat(64), owner_key: 'owner-fixture' };
  fs.writeFileSync(path.join(value.layout.agent, 'private', 'registry.json'), JSON.stringify(registry), { mode: 0o600 });
  const endpoint = agentEnvironment.endpointRecord(UID, { endpoint: `unix:///run/user/${UID}/docker.sock`, socket_path: `/run/user/${UID}/docker.sock`, socket_type: 'socket', socket_uid: UID, socket_mode: 0o600, socket_device: 1, socket_inode: 2, explicit_host: true, ambient_context_used: false, rootless: true, daemon_uid: UID, daemon_identity: 'fixture-daemon', user_namespace: 'rootless', cgroup_version: 2, cgroup_driver: 'systemd', service_endpoint: `unix:///run/user/${UID}/docker.sock` });
  installer.writeJson(path.join(value.layout.private, 'docker-endpoint.json'), endpoint, UID);
  const dockerCalls = [];
  value.dependencies.docker = { reinspect: async () => ({ endpoint_verified: true, rootless: true, container_id: registry.selected_container_id, managed: true, owner_key: registry.owner_key }), removeExact: async (request) => dockerCalls.push(request) };
  let interrupted = false; value.dependencies.fault = { afterLifecyclePhase(phase) { if (!interrupted && phase === 'agent_container_removed') { interrupted = true; throw new launcher.LauncherError('uninstall_interrupted', 'fixture'); } } };
  await reject('uninstall_interrupted', () => uninstall(value, { purgeAgentEnvironment: true, confirmAgentDataLoss: true }));
  assert.equal(dockerCalls.length, 1); assert.equal(exists(value.layout.agent), true);
  value.dependencies.fault = null; await uninstall(value, { purgeAgentEnvironment: true, confirmAgentDataLoss: true });
  assert.equal(dockerCalls.length, 1); assert.equal(dockerCalls[0].container_id, registry.selected_container_id); assert.equal(exists(value.layout.agent), false);
});

test('purge-all deletes only closed data roots and never constructs ambient, wildcard, prune, or external deletion', async (context) => {
  const value = fixture(context); const external = path.join(value.parent, 'external-mount'); fs.mkdirSync(external, { mode: 0o700 }); fs.writeFileSync(path.join(external, 'keep'), 'keep');
  await uninstall(value, { purgeAll: true, confirmAgentDataLoss: true, confirmDataLoss: true });
  assert.equal(exists(value.layout.config), false); assert.equal(exists(value.layout.models), false); assert.equal(exists(value.layout.agent), false); assert.equal(exists(value.layout.appData), false);
  assert.equal(fs.readFileSync(path.join(external, 'keep'), 'utf8'), 'keep');
  assert.equal(value.calls.some((call) => /prune|wildcard|context/.test(call)), false);
});

test('interrupted uninstall resumes only selected categories; foreign/symlink/race state fails closed', async (context) => {
  let fired = false; const interrupted = fixture(context, { fault: { afterLifecyclePhase(phase) { if (!fired && phase === 'program_removed') { fired = true; throw new launcher.LauncherError('uninstall_interrupted', 'fixture'); } } } });
  await reject('uninstall_interrupted', () => uninstall(interrupted)); assert.equal(exists(interrupted.layout.launcher), true, 'launcher remains available until every selected category is durable'); interrupted.dependencies.fault = null;
  await uninstall(interrupted); assert.equal(exists(interrupted.layout.uninstallJournal), false); assert.equal(interrupted.calls.filter((item) => item === 'stop').length, 1);
  const repeated = await uninstall(interrupted); assert.equal(repeated.state, 'uninstalled');

  const linked = fixture(context); fs.rmSync(linked.layout.downloads, { recursive: true }); const foreign = path.join(linked.parent, 'foreign'); fs.mkdirSync(foreign, { mode: 0o700 }); fs.writeFileSync(path.join(foreign, 'keep'), 'keep'); fs.symlinkSync(foreign, linked.layout.downloads);
  await reject('uninstall_target_invalid', () => uninstall(linked)); assert.equal(fs.readFileSync(path.join(foreign, 'keep'), 'utf8'), 'keep'); assert.deepEqual(linked.calls, []);
});

function statusOptions(value) {
  const absent = path.join(value.parent, 'absent');
  return { expectedUid: UID, home: value.identity.home, installRoot: path.join(absent, 'install'), legacyRoot: path.join(absent, 'legacy'), configRoot: path.join(absent, 'config'), stateRoot: path.join(absent, 'state'), serviceProbe: { inspectCanonical: async () => null, inspectLegacy: async () => null } };
}

test('support bundle is deterministic, owner-only, local-only, and reports checksum/size/category names', async (context) => {
  const value = fixture(context); let uploaded = false; value.dependencies.upload = async () => { uploaded = true; };
  const firstPath = path.join(value.parent, 'bundle-one.tar.gz'); const secondPath = path.join(value.parent, 'bundle-two.tar.gz');
  const first = await lifecycle.supportBundle({ testMode: true, dependencies: value.dependencies, output: firstPath, statusOptions: statusOptions(value) });
  const second = await lifecycle.supportBundle({ testMode: true, dependencies: value.dependencies, output: secondPath, statusOptions: statusOptions(value) });
  assert.deepEqual(fs.readFileSync(firstPath), fs.readFileSync(secondPath)); assert.equal(first.checksum_sha256, second.checksum_sha256); assert.equal(first.size, fs.lstatSync(firstPath).size);
  assert.deepEqual(first.categories, lifecycle.BUNDLE_CATEGORIES); assert.equal(fs.lstatSync(firstPath).mode & 0o777, 0o600); assert.equal(uploaded, false);
  assert.match(value.lines[0], /Local archive only/); assert.match(value.lines.at(-1), /Upload: not performed/);
});

test('support scanner fails closed for every forbidden content class while allowing schema hashes and ordinary error codes', () => {
  const forbidden = [
    { config: 'value' }, { secret: 'value' }, { environment: 'value' }, { workspace_filename: 'private.txt' }, { conversation: 'hello' },
    { prompt: 'ignore previous' }, { url: 'https://example.invalid/?token=x' }, { command_line: 'tool --password x' }, { process_output: 'raw output' },
    { model_bytes: 'AAAA' }, { docker_inspect: { Id: 'a'.repeat(64) } }, { credential: 'value' },
  ];
  for (const value of forbidden) assert.throws(() => lifecycle.scanValue(value, {}), (reason) => reason.code === 'support_secret_suspected');
  assert.throws(() => lifecycle.scanValue({ code: 'A'.repeat(48) }, {}), (reason) => reason.code === 'support_secret_suspected');
  assert.throws(() => lifecycle.scanValue({ code: 'contains-known-value' }, { secretValues: ['known-value'] }), (reason) => reason.code === 'support_secret_suspected');
  assert.doesNotThrow(() => lifecycle.scanValue({ digest: 'a'.repeat(64), code: 'tokenizer_unavailable' }, {}));
});

test('support bundle rejects unsafe output and leaves no archive on suspected secret or unexpected category', async (context) => {
  const linked = fixture(context); const outside = path.join(linked.parent, 'outside'); fs.mkdirSync(outside, { mode: 0o700 }); const symlink = path.join(linked.parent, 'linked'); fs.symlinkSync(outside, symlink);
  await reject('install_root_invalid', () => lifecycle.supportBundle({ testMode: true, dependencies: linked.dependencies, output: path.join(symlink, 'bundle.tar.gz'), statusOptions: statusOptions(linked) }));
  assert.equal(fs.readdirSync(outside).length, 0);
  assert.throws(() => lifecycle.makeArchive({ 'unexpected.json': {} }), (reason) => reason.code === 'support_category_invalid');
});
