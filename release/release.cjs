#!/usr/bin/env node
'use strict';

const crypto = require('node:crypto');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const archive = require('./archive.cjs');
const core = require('../launcher/voice-agent.cjs');
const installer = require('../launcher/install.cjs')(core);
const sourceModule = require('../launcher/release-source.cjs')(core);

const ROOT = path.resolve(__dirname, '..');
const PLATFORM = 'linux-x86_64-nvidia';
const VERSION = /^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-[0-9A-Za-z.-]+)?$/;
const SHA256 = /^[0-9a-f]{64}$/;
const COMMIT = /^[0-9a-f]{40}$/;
const SAFE_PATH = /^(?!\/)(?!.*(?:^|\/)\.\.(?:\/|$))(?!.*[\\\0])[\x20-\x7e]{1,1024}$/;

class ReleaseError extends Error { constructor(code, message) { super(message); this.code = code; } }
function fail(code, message) { throw new ReleaseError(code, message); }
function canonical(value) { return core.canonicalJson(value); }
function sha256(bytes) { return archive.sha256(bytes); }
function writeCanonical(filename, value, mode = 0o644) { fs.writeFileSync(filename, Buffer.from(canonical(value)), { mode }); fs.chmodSync(filename, mode); }
function exactKeys(value, keys, code) {
  if (!value || typeof value !== 'object' || Array.isArray(value) || Object.keys(value).sort().join('\0') !== [...keys].sort().join('\0')) fail(code, 'document fields are not closed');
}
function run(command, args, options = {}) {
  const result = spawnSync(command, args, { cwd: options.cwd || ROOT, encoding: 'utf8', timeout: options.timeout || 120000, env: options.env || process.env, input: options.input });
  if (result.status !== 0) fail(options.code || 'release_command_failed', options.message || `${command} failed without content`);
  return result.stdout;
}
function git(...args) { return run('git', args, { code: 'source_state_invalid', message: 'Git source state is unavailable' }).trim(); }

function validateSourceState(top, status, commit) {
  if (path.resolve(top) !== ROOT || status !== '') fail('dirty_source_refused', 'release assembly requires a clean current worktree');
  if (!COMMIT.test(commit)) fail('source_state_invalid', 'release source is not one exact commit');
  return commit;
}
function requireCleanCurrentCommit() {
  return validateSourceState(git('rev-parse', '--show-toplevel'), git('status', '--porcelain=v1', '--untracked-files=all'), git('rev-parse', 'HEAD'));
}

function readCanonical(filename, maximum = 4 * 1024 * 1024) {
  const bytes = fs.readFileSync(filename);
  if (bytes.length > maximum) fail('release_input_invalid', 'release input exceeds its bound');
  return core.parseCanonicalJson(bytes, maximum);
}
function regularFile(filename, maximum = Number.MAX_SAFE_INTEGER) {
  const metadata = fs.lstatSync(filename);
  if (!metadata.isFile() || metadata.isSymbolicLink() || metadata.nlink !== 1 || metadata.size > maximum) fail('release_input_invalid', 'release input file custody is invalid');
  return metadata;
}
function cleanRelative(value) {
  if (typeof value !== 'string' || !SAFE_PATH.test(value) || value.split('/').some((part) => part === '' || part === '.' || part === '..')) fail('release_path_invalid', 'release path is unsafe');
  return value;
}

function validateRuntimeReceipt(document, root, production = true) {
  exactKeys(document, ['architecture', 'cuda', 'files', 'libc', 'node', 'platform', 'python', 'schema', 'source', 'test_only'], 'runtime_receipt_invalid');
  exactKeys(document.libc, ['family', 'minimum'], 'runtime_receipt_invalid');
  exactKeys(document.cuda, ['minimum_driver', 'runtime'], 'runtime_receipt_invalid');
  exactKeys(document.node, ['version'], 'runtime_receipt_invalid'); exactKeys(document.python, ['version'], 'runtime_receipt_invalid');
  exactKeys(document.source, ['immutable_url', 'license_evidence_url', 'redistribution_authorized', 'sha256'], 'runtime_receipt_invalid');
  if (document.schema !== 'voice-agent.runtime-bundle.v1' || document.platform !== PLATFORM || document.architecture !== 'x86_64'
    || document.libc.family !== 'glibc' || !/^2\.[0-9]+$/.test(document.libc.minimum)
    || !/^[0-9]+(?:\.[0-9]+){1,2}$/.test(document.cuda.minimum_driver) || !/^cuda-1[23]\.[0-9]+$/.test(document.cuda.runtime)
    || !/^3\.(?:1[2-9]|[2-9][0-9])\.[0-9]+$/.test(document.python.version) || !/^(?:22|2[4-9]|[3-9][0-9])\.[0-9]+\.[0-9]+$/.test(document.node.version)
    || typeof document.test_only !== 'boolean' || !SHA256.test(document.source.sha256)
    || typeof document.source.redistribution_authorized !== 'boolean') fail('runtime_receipt_invalid', 'runtime platform identity is invalid');
  for (const name of ['immutable_url', 'license_evidence_url']) sourceModule.parseHttpsUrl(document.source[name], 'runtime_receipt_invalid');
  if (production && (document.test_only || !document.source.redistribution_authorized)) fail('redistribution_authority_missing', 'production runtime redistribution authority is absent');
  if (!Array.isArray(document.files) || document.files.length < 2 || document.files.length > 20000) fail('runtime_receipt_invalid', 'runtime inventory count is invalid');
  const expected = new Map();
  for (const item of document.files) {
    exactKeys(item, ['mode', 'path', 'sha256', 'size'], 'runtime_receipt_invalid');
    const relative = cleanRelative(item.path);
    if (expected.has(relative) || !/^0(?:444|555)$/.test(item.mode) || !SHA256.test(item.sha256) || !Number.isSafeInteger(item.size) || item.size < 0) fail('runtime_receipt_invalid', 'runtime inventory entry is invalid');
    expected.set(relative, item);
  }
  for (const required of ['python/bin/python3', 'node/bin/node']) if (!expected.has(required) || expected.get(required).mode !== '0555') fail('runtime_receipt_invalid', 'runtime bundle lacks an exact executable Python or Node runtime');
  const observed = [];
  function walk(directory, prefix = '') {
    for (const name of fs.readdirSync(directory).sort()) {
      const relative = prefix ? `${prefix}/${name}` : name; const filename = path.join(directory, name); const metadata = fs.lstatSync(filename);
      if (metadata.isDirectory() && !metadata.isSymbolicLink()) walk(filename, relative);
      else if (metadata.isFile() && !metadata.isSymbolicLink() && metadata.nlink === 1) observed.push(relative);
      else fail('runtime_receipt_invalid', 'runtime bundle contains a link or special file');
    }
  }
  walk(root);
  if (observed.length !== expected.size || observed.some((name) => !expected.has(name))) fail('runtime_undeclared_file', 'runtime bundle contains missing or undeclared files');
  const hash = crypto.createHash('sha256');
  for (const relative of observed.sort()) {
    const item = expected.get(relative); const filename = path.join(root, ...relative.split('/')); const bytes = fs.readFileSync(filename);
    if (bytes.length !== item.size || sha256(bytes) !== item.sha256 || (fs.lstatSync(filename).mode & 0o777) !== Number.parseInt(item.mode, 8)) fail('runtime_hash_mismatch', 'runtime bundle bytes differ from their receipt');
    hash.update(Buffer.from(`${relative}\0${item.mode}\0${item.size}\0${item.sha256}\n`));
  }
  if (hash.digest('hex') !== document.source.sha256) fail('runtime_hash_mismatch', 'runtime aggregate digest differs');
  return document;
}

function elfMachine(filename) {
  const bytes = fs.readFileSync(filename).subarray(0, 20);
  if (bytes.length < 20 || !bytes.subarray(0, 4).equals(Buffer.from([0x7f, 0x45, 0x4c, 0x46])) || bytes[4] !== 2 || bytes[5] !== 1 || bytes.readUInt16LE(18) !== 62) fail('runtime_architecture_mismatch', 'bundled runtime executable is not Linux x86_64 ELF');
}

function validateExactDependencies(packageJson, lock, pythonLines) {
  for (const group of ['dependencies', 'devDependencies']) for (const [name, value] of Object.entries(packageJson[group] || {})) {
    if (!/^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-[0-9A-Za-z.-]+)?$/.test(value)) fail('floating_dependency_refused', `web dependency ${name} is not exact`);
  }
  for (const [name, item] of Object.entries(lock.packages || {})) {
    if (!name || !item.resolved || item.link) continue;
    if (!item.integrity || !/^sha512-[A-Za-z0-9+/]+={0,2}$/.test(item.integrity)) fail('dependency_hash_missing', 'npm lock contains an unhashed external package');
  }
  if (!pythonLines.length || pythonLines.some((line) => !line.includes('--hash=sha256:'))) fail('dependency_hash_missing', 'Python release dependency lock lacks artifact hashes');
  return { lock, pythonLines };
}
function validateLocks() {
  const packageJson = JSON.parse(fs.readFileSync(path.join(ROOT, 'web/package.json')));
  const lock = JSON.parse(fs.readFileSync(path.join(ROOT, 'web/package-lock.json')));
  const pythonLines = fs.readFileSync(path.join(ROOT, 'requirements-slice6.lock'), 'utf8').split('\n').map((line) => line.trim()).filter((line) => line && !line.startsWith('#'));
  return validateExactDependencies(packageJson, lock, pythonLines);
}

function pathLeakScan(bytes, forbidden = []) {
  const text = bytes.toString('latin1');
  const patterns = [...forbidden, ROOT, os.homedir(), '/tmp/', '/home/priney/', 'ghp_', 'github_pat_', 'TELEGRAM_BOT_TOKEN='];
  for (const value of patterns.filter(Boolean)) if (text.includes(value)) fail('release_content_leak', 'release output contains a forbidden host path or secret marker');
  if (/-----BEGIN (?:ENCRYPTED )?PRIVATE KEY-----[\r\n]+[A-Za-z0-9+/=]{16}/.test(text)) fail('release_content_leak', 'release output contains a private-key block');
}

function addTreeFile(tree, relative, bytes, mode) {
  cleanRelative(relative);
  if (tree.has(relative)) fail('release_undeclared_file', 'release path is duplicated');
  pathLeakScan(bytes);
  tree.set(relative, { path: relative, type: 'file', mode, bytes: Buffer.from(bytes) });
}
function addDirectories(tree) {
  const directories = new Set();
  for (const name of tree.keys()) {
    const parts = name.split('/');
    for (let index = 1; index < parts.length; index += 1) directories.add(parts.slice(0, index).join('/'));
  }
  for (const name of [...directories].sort()) if (!tree.has(name)) tree.set(name, { path: name, type: 'directory', mode: '0555', bytes: Buffer.alloc(0) });
}
function manifestEntries(tree) {
  addDirectories(tree);
  return [...tree.values()].sort((a, b) => a.path.localeCompare(b.path)).map((item) => ({ path: item.path, type: item.type, mode: item.mode,
    size: item.type === 'file' ? item.bytes.length : 0, sha256: item.type === 'file' ? sha256(item.bytes) : null, target: null }));
}

function assembleFromTree(options) {
  const { output, tree, version, commit, timestamp, sequence, assets, artifactUrl, sourceReceipt, platformContract } = options;
  if (!VERSION.test(version) || !COMMIT.test(commit) || !Number.isSafeInteger(sequence) || sequence < 1) fail('release_input_invalid', 'candidate identity is invalid');
  fs.mkdirSync(output, { recursive: false, mode: 0o700 });
  const entries = manifestEntries(tree);
  const manifest = { application_protocol: { maximum: 1, minimum: 1 }, build_id: commit, config_schema: { maximum: 2, minimum: 2 }, data_schema: { maximum: 2, minimum: 2 }, entries,
    launcher_protocol: { maximum: 1, minimum: 1 }, platform: PLATFORM, schema: 'voice-agent.platform-artifact-manifest.v1', service_template_sha256: installer.UNIT_CONTRACT_SHA256, version };
  const manifestBytes = Buffer.from(canonical(manifest));
  const tarEntries = [{ path: 'release-manifest.json', type: 'file', mode: '0444', bytes: manifestBytes, target: null }, ...[...tree.values()].sort((a, b) => a.path.localeCompare(b.path))];
  const epoch = Date.parse(timestamp) / 1000;
  const artifactBytes = archive.createTarZstd(tarEntries, epoch);
  const artifactName = `voice-agent-${version}-linux-x86_64-nvidia.tar.zst`;
  fs.writeFileSync(path.join(output, artifactName), artifactBytes, { mode: 0o444 });
  fs.writeFileSync(path.join(output, 'release-manifest.json'), manifestBytes, { mode: 0o444 });
  const sbom = { schema: 'voice-agent.sbom.v1', build_id: commit, platform: PLATFORM, version, files: entries.filter((item) => item.type === 'file').map((item) => ({ path: item.path, sha256: item.sha256, size: item.size })), external_assets: assets.map((item) => ({ id: item.id, kind: item.kind, license: item.license.id, locator: item.url, sha256: item.sha256, size: item.size })) };
  const provenance = { schema: 'voice-agent.application-provenance.v1', artifact_bytes: artifactBytes.length, artifact_sha256: sha256(artifactBytes), build_id: commit, manifest_sha256: sha256(manifestBytes), platform: PLATFORM, platform_contract: platformContract, runtime_source_receipt_sha256: sha256(Buffer.from(canonical(sourceReceipt))), source_commit: commit, source_timestamp: timestamp, version };
  writeCanonical(path.join(output, 'sbom.spdx.json'), sbom); writeCanonical(path.join(output, 'provenance.json'), provenance);
  const channel = { channel: 'stable', expires_at: options.expiresAt, generated_at: timestamp, releases: [{ artifact_bytes: artifactBytes.length, artifact_sha256: sha256(artifactBytes), artifact_url: artifactUrl,
    assets, build_id: commit, launcher: options.launcher || null, manifest_sha256: sha256(manifestBytes), maximum_data_schema: 2, minimum_data_schema: 2, minimum_launcher_protocol: 1, platform: PLATFORM, version }], schema: 'voice-agent.channel.v1', sequence };
  core.validateChannel(channel, { now: new Date(timestamp), trustedSequence: sequence });
  writeCanonical(path.join(output, 'stable.json'), channel);
  writeCanonical(path.join(output, 'candidate-receipt.json'), { artifact: artifactName, artifact_bytes: artifactBytes.length, artifact_sha256: sha256(artifactBytes), manifest_sha256: sha256(manifestBytes), schema: 'voice-agent.release-candidate.v1', sequence, signed: false, version });
  return { artifactName, channel, manifest, provenance, sbom };
}

function buildRuntimeTree(runtimeRoot, runtimeReceipt, webRoot) {
  const tree = new Map();
  for (const item of runtimeReceipt.files) addTreeFile(tree, `runtime/${item.path}`, fs.readFileSync(path.join(runtimeRoot, ...item.path.split('/'))), item.mode);
  for (const relative of git('ls-files', 'src/voice_agent_v2', 'scripts/run_slice6.py', 'scripts/silero_kseniya_worker.py', 'config').split('\n').filter(Boolean)) {
    addTreeFile(tree, `app/${relative}`, fs.readFileSync(path.join(ROOT, relative)), '0444');
  }
  function copyWeb(directory, prefix = 'web') {
    for (const name of fs.readdirSync(directory).sort()) {
      const filename = path.join(directory, name); const relative = `${prefix}/${name}`; const metadata = fs.lstatSync(filename);
      if (metadata.isDirectory() && !metadata.isSymbolicLink()) copyWeb(filename, relative);
      else if (metadata.isFile() && !metadata.isSymbolicLink()) addTreeFile(tree, relative, fs.readFileSync(filename), '0444');
      else fail('release_undeclared_file', 'built web output contains a link or special file');
    }
  }
  copyWeb(webRoot);
  const wrapper = Buffer.from('#!/bin/sh\nset -eu\nHERE=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)\nexec "$HERE/runtime/python/bin/python3" -I -B "$HERE/app/scripts/run_slice6.py" "$@"\n');
  addTreeFile(tree, 'bin/voice-agent-runtime', wrapper, '0555');
  addTreeFile(tree, 'descriptors/local-models.json', fs.readFileSync(path.join(ROOT, 'config/local-lfm-v1.json')), '0444');
  addTreeFile(tree, 'descriptors/runtime.json', Buffer.from(canonical({ cuda: runtimeReceipt.cuda, libc: runtimeReceipt.libc, node: runtimeReceipt.node, platform: PLATFORM, python: runtimeReceipt.python, schema: 'voice-agent.bundled-runtime.v1' })), '0444');
  addTreeFile(tree, 'descriptors/service-compatibility.json', Buffer.from(canonical({ application_protocol: { maximum: 1, minimum: 1 }, config_schema: { maximum: 2, minimum: 2 }, data_schema: { maximum: 2, minimum: 2 }, launcher_protocol: { maximum: 1, minimum: 1 }, schema: 'voice-agent.service-compatibility.v1', service_template_sha256: installer.UNIT_CONTRACT_SHA256 })), '0444');
  return tree;
}

function parseInput(filename) {
  const input = readCanonical(filename);
  exactKeys(input, ['artifact_base_url', 'asset_evidence', 'assets', 'channel_url', 'expires_at', 'generated_at', 'launcher_protocol', 'launcher_version', 'platform_contract', 'sequence', 'version'], 'release_input_invalid');
  if (!VERSION.test(input.version) || !VERSION.test(input.launcher_version) || !Number.isSafeInteger(input.launcher_protocol) || input.launcher_protocol !== core.LAUNCHER_PROTOCOL
    || !Number.isSafeInteger(input.sequence) || input.sequence < 1 || !Array.isArray(input.assets) || input.assets.length < 2) fail('release_input_invalid', 'release input identity is invalid');
  sourceModule.parseHttpsUrl(input.channel_url, 'release_input_invalid'); sourceModule.parseHttpsUrl(input.artifact_base_url, 'release_input_invalid');
  core.loadAssetCache(installer).validateDescriptors(input.assets);
  if (!Array.isArray(input.asset_evidence) || input.asset_evidence.length !== input.assets.length) fail('asset_evidence_missing', 'every external asset requires one source/license receipt');
  const evidence = new Map();
  for (const item of input.asset_evidence) {
    exactKeys(item, ['id', 'immutable_locator', 'license_evidence_url', 'redistribution_authorized', 'sha256', 'size'], 'asset_evidence_missing');
    sourceModule.parseHttpsUrl(item.immutable_locator, 'asset_evidence_missing'); sourceModule.parseHttpsUrl(item.license_evidence_url, 'asset_evidence_missing');
    if (evidence.has(item.id) || typeof item.redistribution_authorized !== 'boolean' || !SHA256.test(item.sha256) || !Number.isSafeInteger(item.size) || item.size < 1) fail('asset_evidence_missing', 'external asset evidence is invalid');
    evidence.set(item.id, item);
  }
  for (const descriptor of input.assets) {
    const item = evidence.get(descriptor.id);
    if (!item || item.immutable_locator !== descriptor.url || item.sha256 !== descriptor.sha256 || item.size !== descriptor.size) fail('asset_evidence_missing', 'external asset evidence differs from its descriptor');
  }
  return input;
}

function candidateCommand(values) {
  const commit = requireCleanCurrentCommit(); const input = parseInput(values.input);
  validateLocks();
  const runtimeReceipt = readCanonical(values.runtimeReceipt); validateRuntimeReceipt(runtimeReceipt, values.runtimeRoot, true);
  elfMachine(path.join(values.runtimeRoot, 'python/bin/python3')); elfMachine(path.join(values.runtimeRoot, 'node/bin/node'));
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'voice-agent-launcher-candidate-'));
  try {
    const launcherPath = path.join(temporary, 'voice-agent-launcher-linux-x86_64');
    run(path.join(ROOT, 'launcher/build'), ['--output', launcherPath, '--public-key', values.publicKey, '--channel-url', input.channel_url, '--launcher-version', input.launcher_version,
      '--launcher-protocol', String(input.launcher_protocol), '--source-timestamp', input.generated_at, '--source-commit', commit], { code: 'launcher_build_failed', message: 'exact SEA launcher build failed' });
    const launcherBytes = fs.readFileSync(launcherPath); const launcherSha = sha256(launcherBytes); const launcherUrl = `${input.artifact_base_url}/voice-agent-launcher-linux-x86_64`;
    const parsed = new URL(launcherUrl);
    const launcherDescriptor = core.loadAssetCache(installer).validateDescriptor({ authority: { origin: parsed.origin, path_prefix: parsed.pathname.slice(0, parsed.pathname.lastIndexOf('/') + 1) },
      compatibility: { maximum_application_protocol: 1, maximum_launcher_protocol: input.launcher_protocol, minimum_application_protocol: 1, minimum_launcher_protocol: input.launcher_protocol },
      digest: `sha256:${launcherSha}`, id: `launcher-${input.launcher_version.replaceAll('.', '-')}`, kind: 'launcher', license: { acceptance: 'not_required', id: 'Voice-Agent-Launcher' },
      platform: PLATFORM, reachability: 'required', required_free_space_reserve: 0, sha256: launcherSha, size: launcherBytes.length, url: launcherUrl });
    run('npm', ['ci', '--offline', '--ignore-scripts'], { cwd: path.join(ROOT, 'web'), code: 'web_dependency_unavailable', message: 'exact locked web dependencies are unavailable offline' });
    run('npm', ['run', 'build:production-only'], { cwd: path.join(ROOT, 'web'), env: { ...process.env, VITE_APP_VERSION: commit }, code: 'web_build_failed', message: 'production web build failed' });
    const tree = buildRuntimeTree(values.runtimeRoot, runtimeReceipt, path.join(ROOT, 'web/dist'));
    addTreeFile(tree, 'descriptors/asset-evidence.json', Buffer.from(canonical({ assets: input.asset_evidence, schema: 'voice-agent.asset-source-evidence.v1' })), '0444');
    const result = assembleFromTree({ output: values.output, tree, version: input.version, commit, timestamp: input.generated_at, expiresAt: input.expires_at, sequence: input.sequence, assets: input.assets,
      artifactUrl: `${input.artifact_base_url}/voice-agent-${input.version}-linux-x86_64-nvidia.tar.zst`, sourceReceipt: runtimeReceipt, platformContract: input.platform_contract, launcher: launcherDescriptor });
    for (const suffix of ['', '.sha256', '.provenance.json']) {
      fs.copyFileSync(`${launcherPath}${suffix}`, path.join(values.output, `voice-agent-launcher-linux-x86_64${suffix}`));
      fs.chmodSync(path.join(values.output, `voice-agent-launcher-linux-x86_64${suffix}`), suffix === '' ? 0o555 : 0o444);
    }
    return { ...verifyCandidate(values.output), artifact: result.artifactName };
  } finally { fs.rmSync(temporary, { recursive: true, force: true }); }
}

function verifyCandidate(directory) {
  const receipt = readCanonical(path.join(directory, 'candidate-receipt.json')); exactKeys(receipt, ['artifact', 'artifact_bytes', 'artifact_sha256', 'manifest_sha256', 'schema', 'sequence', 'signed', 'version'], 'candidate_invalid');
  const artifactBytes = fs.readFileSync(path.join(directory, receipt.artifact)); const manifestBytes = fs.readFileSync(path.join(directory, 'release-manifest.json'));
  if (receipt.schema !== 'voice-agent.release-candidate.v1' || artifactBytes.length !== receipt.artifact_bytes || sha256(artifactBytes) !== receipt.artifact_sha256 || sha256(manifestBytes) !== receipt.manifest_sha256) fail('candidate_invalid', 'candidate artifact receipt differs');
  const indexed = sourceModule.indexArchive(artifactBytes); if (!indexed.manifestBytes.equals(manifestBytes)) fail('candidate_invalid', 'candidate archive manifest differs');
  for (const name of ['sbom.spdx.json', 'provenance.json', 'stable.json']) readCanonical(path.join(directory, name));
  const channel = readCanonical(path.join(directory, 'stable.json')); core.validateChannel(channel, { now: new Date(channel.generated_at), trustedSequence: channel.sequence });
  const release = channel.releases[0]; core.verifyPlatformArtifact(artifactBytes, manifestBytes, release);
  if (release.launcher) {
    const launcherPath = path.join(directory, 'voice-agent-launcher-linux-x86_64'); const bytes = fs.readFileSync(launcherPath);
    if (bytes.length !== release.launcher.size || sha256(bytes) !== release.launcher.sha256
      || fs.readFileSync(`${launcherPath}.sha256`, 'utf8').trim() !== release.launcher.sha256
      || readCanonical(`${launcherPath}.provenance.json`).executable_sha256 !== release.launcher.sha256) fail('candidate_invalid', 'candidate launcher receipt differs');
    pathLeakScan(bytes);
  }
  pathLeakScan(artifactBytes); pathLeakScan(fs.readFileSync(path.join(directory, 'sbom.spdx.json'))); pathLeakScan(fs.readFileSync(path.join(directory, 'provenance.json')));
  return { artifact_sha256: receipt.artifact_sha256, sequence: receipt.sequence, version: receipt.version };
}

function signChannel(values) {
  const keyPath = path.resolve(values.privateKey); const metadata = regularFile(keyPath, 64 * 1024);
  if (metadata.uid !== process.geteuid() || metadata.nlink !== 1 || ![0o400, 0o600].includes(metadata.mode & 0o777)) fail('private_key_permission_invalid', 'signing key must be owner-only regular file');
  const channelBytes = fs.readFileSync(values.channel); const document = core.parseCanonicalJson(channelBytes, 256 * 1024);
  core.validateChannel(document, { now: new Date(document.generated_at), trustedSequence: document.sequence });
  let key; try { key = crypto.createPrivateKey(fs.readFileSync(keyPath)); } catch { fail('private_key_invalid', 'signing key is invalid'); }
  if (key.asymmetricKeyType !== 'ed25519') fail('private_key_invalid', 'signing key is not Ed25519');
  fs.mkdirSync(values.output, { recursive: true, mode: 0o700 });
  const signature = Buffer.from(`${crypto.sign(null, channelBytes, key).toString('base64')}\n`);
  fs.writeFileSync(path.join(values.output, 'stable.json.sig'), signature, { mode: 0o644 });
  writeCanonical(path.join(values.output, 'signing-receipt.json'), { channel_bytes: channelBytes.length, channel_sha256: sha256(channelBytes), schema: 'voice-agent.channel-signing-receipt.v1', sequence: document.sequence, signature_sha256: sha256(signature), version: document.releases[0].version });
  return { channel_sha256: sha256(channelBytes), sequence: document.sequence };
}

function publicationPlan(values) {
  if (!/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(values.repository)) fail('publication_input_invalid', 'repository identity is invalid');
  regularFile(values.publicKey, 64 * 1024);
  let publicationKey; try { publicationKey = crypto.createPublicKey(fs.readFileSync(values.publicKey)); } catch { fail('publication_input_invalid', 'publication public key is invalid'); }
  if (publicationKey.asymmetricKeyType !== 'ed25519') fail('publication_input_invalid', 'publication public key is not Ed25519');
  const publicKeySha256 = sha256(publicationKey.export({ type: 'spki', format: 'der' }));
  const candidate = verifyCandidate(values.candidate); const channel = readCanonical(path.join(values.candidate, 'stable.json')); 
  const signaturePath = path.join(values.candidate, 'stable.json.sig'); const signingPath = path.join(values.candidate, 'signing-receipt.json');
  regularFile(signaturePath, 1024); const signing = readCanonical(signingPath);
  if (signing.channel_sha256 !== sha256(fs.readFileSync(path.join(values.candidate, 'stable.json'))) || signing.sequence !== candidate.sequence || signing.version !== candidate.version) fail('unsigned_publication_refused', 'publication requires matching signed channel inputs');
  const confirmation = `${candidate.version}:${candidate.sequence}`;
  if (values.confirm !== confirmation) fail('publication_confirmation_required', `explicit confirmation must equal ${confirmation}`);
  const tag = `v${candidate.version}`;
  const expectedChannelUrl = `https://raw.githubusercontent.com/${values.repository}/release-channel/stable.json`;
  const launcherProvenance = path.join(values.candidate, 'voice-agent-launcher-linux-x86_64.provenance.json');
  if (fs.existsSync(launcherProvenance)) {
    const provenance = readCanonical(launcherProvenance);
    if (provenance.channel_url !== expectedChannelUrl || provenance.public_key_sha256 !== publicKeySha256) fail('publication_input_invalid', 'compiled stable URL or trust root differs from publication authority');
  }
  const assets = fs.readdirSync(values.candidate).filter((name) => !name.startsWith('.') && !['stable.json', 'stable.json.sig'].includes(name)).sort().map((name) => { const bytes = fs.readFileSync(path.join(values.candidate, name)); return { bytes: bytes.length, name, sha256: sha256(bytes) }; });
  const channel_documents = ['stable.json', 'stable.json.sig'].map((name) => { const bytes = fs.readFileSync(path.join(values.candidate, name)); return { bytes: bytes.length, name, sha256: sha256(bytes) }; });
  return { schema: 'voice-agent.publication-plan.v1', repository: values.repository, tag, version: candidate.version, sequence: candidate.sequence, source_commit: channel.releases[0].build_id, assets, channel_documents, channel_url: expectedChannelUrl, collision_policy: 'refuse', readback: 'exact-sha256' };
}

function publishWithRemote(plan, values, remote) {
  if (remote.exists(plan)) fail('publication_collision', 'remote release tag already exists');
  const remoteSequence = remote.sequence(plan);
  if (!Number.isSafeInteger(remoteSequence) || remoteSequence < 0 || remoteSequence >= plan.sequence) fail('publication_sequence_collision', 'remote channel sequence is invalid or not older');
  if (remote.verifyTag(plan) !== true) fail('publication_tag_mismatch', 'immutable release tag does not resolve to the candidate source commit');
  remote.create(plan);
  for (const asset of plan.assets) remote.upload(plan, asset, path.join(values.candidate, asset.name));
  const observed = remote.readback(plan);
  for (const asset of plan.assets) {
    const bytes = observed.get(asset.name);
    if (!Buffer.isBuffer(bytes) || sha256(bytes) !== asset.sha256) fail('publication_readback_mismatch', 'published asset readback digest differs');
  }
  remote.publishChannel(plan, new Map(plan.channel_documents.map((item) => [item.name, fs.readFileSync(path.join(values.candidate, item.name))])));
  const channelObserved = remote.readbackChannel(plan);
  for (const document of plan.channel_documents) {
    const bytes = channelObserved.get(document.name);
    if (!Buffer.isBuffer(bytes) || sha256(bytes) !== document.sha256) fail('publication_readback_mismatch', 'published channel readback digest differs');
  }
  return plan;
}

function publish(values) {
  const plan = publicationPlan(values);
  if (values.dryRun) return plan;
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'voice-agent-publication-readback-'));
  const ghApi = (method, endpoint, body = null, allowMissing = false) => {
    const arguments_ = ['api', '--method', method, endpoint];
    if (body !== null) arguments_.push('--input', '-');
    const result = spawnSync('gh-axi', arguments_, { encoding: 'utf8', timeout: 30000, input: body === null ? undefined : JSON.stringify(body) });
    if (result.status !== 0) { if (allowMissing) return null; fail('publication_remote_invalid', 'remote channel authority is unavailable'); }
    try { return JSON.parse(result.stdout); } catch { fail('publication_remote_invalid', 'remote channel response is invalid'); }
  };
  const readChannelDocument = (repository, name, allowMissing = false) => {
    const document = ghApi('GET', `repos/${repository}/contents/${name}?ref=release-channel`, null, allowMissing);
    if (document === null) return null;
    if (!document || document.encoding !== 'base64' || typeof document.content !== 'string') fail('publication_remote_invalid', 'remote channel content is invalid');
    return Buffer.from(document.content.replace(/\s/g, ''), 'base64');
  };
  const remote = {
    exists(value) { return spawnSync('gh-axi', ['release', 'view', value.tag, '--repo', value.repository, '--json', 'tagName'], { encoding: 'utf8', timeout: 30000 }).status === 0; },
    sequence(value) {
      const channelBytes = readChannelDocument(value.repository, 'stable.json', true);
      const signatureBytes = readChannelDocument(value.repository, 'stable.json.sig', true);
      if (channelBytes === null && signatureBytes === null) return 0;
      if (!channelBytes || !signatureBytes) fail('publication_remote_invalid', 'remote channel pair is incomplete');
      const publicKey = fs.readFileSync(values.publicKey);
      const parsed = core.parseCanonicalJson(channelBytes, 256 * 1024);
      const verified = core.verifySignedChannel(channelBytes, signatureBytes, publicKey, { now: new Date(parsed.generated_at), trustedSequence: 0 });
      return verified.sequence;
    },
    verifyTag(value) {
      const commit = ghApi('GET', `repos/${value.repository}/commits/${encodeURIComponent(value.tag)}`);
      return commit && commit.sha === value.source_commit;
    },
    create(value) { run('gh-axi', ['release', 'create', value.tag, '--repo', value.repository, '--verify-tag', '--title', `Voice Agent ${value.version}`, '--notes', `Voice Agent signed release ${value.version}`], { code: 'publication_failed', message: 'release creation failed without content' }); },
    upload(value, asset, filename) { run('gh-axi', ['release', 'upload', value.tag, filename, '--repo', value.repository], { code: 'publication_failed', message: 'asset upload failed without content' }); },
    readback(value) {
      run('gh-axi', ['release', 'download', value.tag, '--repo', value.repository, '--dir', temporary], { code: 'publication_readback_failed', message: 'release readback failed without content' });
      return new Map(value.assets.map((asset) => [asset.name, fs.readFileSync(path.join(temporary, asset.name))]));
    },
    publishChannel(value, documents) {
      const current = ghApi('GET', `repos/${value.repository}/git/ref/heads/release-channel`, null, true);
      const parent = current && current.object && current.object.sha;
      const blobs = {};
      for (const [name, bytes] of documents) blobs[name] = ghApi('POST', `repos/${value.repository}/git/blobs`, { content: bytes.toString('base64'), encoding: 'base64' }).sha;
      const tree = ghApi('POST', `repos/${value.repository}/git/trees`, { tree: Object.entries(blobs).map(([name, sha]) => ({ path: name, mode: '100644', type: 'blob', sha })) });
      const commit = ghApi('POST', `repos/${value.repository}/git/commits`, { message: `release-channel sequence ${value.sequence}`, tree: tree.sha, parents: parent ? [parent] : [] });
      if (parent) ghApi('PATCH', `repos/${value.repository}/git/refs/heads/release-channel`, { sha: commit.sha, force: false });
      else ghApi('POST', `repos/${value.repository}/git/refs`, { ref: 'refs/heads/release-channel', sha: commit.sha });
    },
    readbackChannel(value) { return new Map(value.channel_documents.map((item) => [item.name, readChannelDocument(value.repository, item.name)])); },
  };
  try { return publishWithRemote(plan, values, remote); }
  finally { fs.rmSync(temporary, { recursive: true, force: true }); }
}

function args(argv) {
  const command = argv.shift(); const values = {};
  while (argv.length) { const name = argv.shift(); if (!name.startsWith('--')) fail('usage', 'invalid release option'); if (name === '--dry-run') values.dryRun = true; else { if (!argv.length) fail('usage', 'release option value is missing'); values[name.slice(2).replace(/-([a-z])/g, (_, value) => value.toUpperCase())] = argv.shift(); } }
  return { command, values };
}
function required(values, names) { for (const name of names) if (!values[name]) fail('usage', `--${name.replace(/[A-Z]/g, (v) => `-${v.toLowerCase()}`)} is required`); }
function main(argv = process.argv.slice(2)) {
  try {
    const parsed = args([...argv]); let result;
    if (parsed.command === 'candidate') { required(parsed.values, ['input', 'runtimeRoot', 'runtimeReceipt', 'publicKey', 'output']); result = candidateCommand(parsed.values); }
    else if (parsed.command === 'verify') { required(parsed.values, ['candidate']); result = verifyCandidate(parsed.values.candidate); }
    else if (parsed.command === 'sign-channel') { required(parsed.values, ['channel', 'privateKey', 'output']); result = signChannel(parsed.values); }
    else if (parsed.command === 'publish') { required(parsed.values, ['candidate', 'repository', 'publicKey', 'confirm']); result = publish(parsed.values); }
    else fail('usage', 'expected candidate, verify, sign-channel, or publish');
    process.stdout.write(`${canonical({ state: parsed.values.dryRun ? 'dry_run' : 'ok', ...result })}\n`); return 0;
  } catch (reason) {
    const code = reason instanceof ReleaseError ? reason.code : 'release_failed';
    process.stderr.write(`${code}: command failed safely\n`); return 2;
  }
}

module.exports = { ReleaseError, assembleFromTree, buildRuntimeTree, candidateCommand, main, pathLeakScan, publicationPlan, publish, publishWithRemote, signChannel, validateExactDependencies, validateLocks, validateRuntimeReceipt, validateSourceState, verifyCandidate };
if (require.main === module) process.exitCode = main();
