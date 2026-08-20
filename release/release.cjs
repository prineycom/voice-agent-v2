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
const runtimeAssembler = require('./runtime-assembler.cjs');

const ROOT = path.resolve(__dirname, '..');
const PLATFORM = 'linux-x86_64-nvidia';
const VERSION = /^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-[0-9A-Za-z.-]+)?$/;
const SHA256 = /^[0-9a-f]{64}$/;
const COMMIT = /^[0-9a-f]{40}$/;
const BUILDER_IMAGE = /^docker\.io\/nvidia\/cuda@sha256:[0-9a-f]{64}$/;
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
  exactKeys(document, ['architecture', 'builder', 'components', 'cuda', 'elf', 'files', 'libc', 'platform', 'python', 'schema', 'source', 'test_only'], 'runtime_receipt_invalid');
  exactKeys(document.builder, ['image', 'manifest_digest'], 'runtime_receipt_invalid');
  exactKeys(document.libc, ['family', 'minimum'], 'runtime_receipt_invalid');
  exactKeys(document.cuda, ['minimum_driver', 'runtime'], 'runtime_receipt_invalid');
  exactKeys(document.python, ['version'], 'runtime_receipt_invalid');
  exactKeys(document.source, ['distribution_scope', 'immutable_url', 'license_evidence_url', 'sha256'], 'runtime_receipt_invalid');
  if (document.schema !== 'voice-agent.runtime-bundle.v1' || document.platform !== PLATFORM || document.architecture !== 'x86_64'
    || document.libc.family !== 'glibc' || !/^2\.[0-9]+$/.test(document.libc.minimum)
    || !/^[0-9]+(?:\.[0-9]+){1,2}$/.test(document.cuda.minimum_driver) || document.cuda.runtime !== 'cuda-12.9'
    || document.python.version !== '3.12.13' || !BUILDER_IMAGE.test(document.builder.image)
    || document.builder.manifest_digest !== document.builder.image.slice(document.builder.image.indexOf('@') + 1)
    || typeof document.test_only !== 'boolean' || !SHA256.test(document.source.sha256)
    || document.source.distribution_scope !== 'private-personal-noncommercial') fail('runtime_receipt_invalid', 'runtime platform identity is invalid');
  for (const name of ['immutable_url', 'license_evidence_url']) sourceModule.parseHttpsUrl(document.source[name], 'runtime_receipt_invalid');
  if (production && document.test_only) fail('distribution_authority_missing', 'private personal runtime distribution authority is absent');
  if (!Array.isArray(document.components) || document.components.length < 5 || document.components.length > 1000) fail('runtime_receipt_invalid', 'runtime component inventory is invalid');
  const componentNames = new Set();
  for (const component of document.components) {
    exactKeys(component, ['download_url', 'license', 'name', 'sha256', 'size', 'version'], 'runtime_receipt_invalid');
    sourceModule.parseHttpsUrl(component.download_url, 'runtime_receipt_invalid');
    if (componentNames.has(component.name) || typeof component.name !== 'string' || !component.name || typeof component.version !== 'string' || !component.version
      || typeof component.license !== 'string' || !component.license || !SHA256.test(component.sha256) || !Number.isSafeInteger(component.size) || component.size < 1) fail('runtime_receipt_invalid', 'runtime component receipt is invalid');
    componentNames.add(component.name);
  }
  if (!Array.isArray(document.files) || document.files.length < 2 || document.files.length > 100000) fail('runtime_receipt_invalid', 'runtime inventory count is invalid');
  const expected = new Map();
  for (const item of document.files) {
    exactKeys(item, ['mode', 'path', 'sha256', 'size'], 'runtime_receipt_invalid');
    const relative = cleanRelative(item.path);
    if (expected.has(relative) || !/^0(?:444|555)$/.test(item.mode) || !SHA256.test(item.sha256) || !Number.isSafeInteger(item.size) || item.size < 0) fail('runtime_receipt_invalid', 'runtime inventory entry is invalid');
    expected.set(relative, item);
  }
  for (const required of ['python/bin/python3', 'livekit/bin/livekit-server', 'llama/bin/llama-server']) if (!expected.has(required) || expected.get(required).mode !== '0555') fail('runtime_receipt_invalid', 'runtime bundle lacks an exact executable production component');
  if (!Array.isArray(document.elf) || document.elf.length < 3 || document.elf.length > document.files.length) fail('runtime_receipt_invalid', 'ELF closure receipt is invalid');
  const elfPaths = new Set(); const providedLibraries = new Set();
  for (const item of document.elf) {
    exactKeys(item, ['needed', 'path', 'required_glibc', 'runpath', 'soname', 'uses_libcuda'], 'runtime_receipt_invalid');
    if (!expected.has(item.path) || elfPaths.has(item.path) || !Array.isArray(item.needed) || item.needed.some((name) => typeof name !== 'string' || !/^[A-Za-z0-9_.+-]{1,255}$/.test(name))
      || typeof item.runpath !== 'string' || (item.runpath && !item.runpath.split(':').every((entry) => entry === '$ORIGIN' || entry.startsWith('$ORIGIN/')))
      || (item.soname !== null && (typeof item.soname !== 'string' || !item.soname)) || typeof item.uses_libcuda !== 'boolean'
      || (item.required_glibc !== null && !/^2\.[0-9]+$/.test(item.required_glibc)) || (item.required_glibc && Number(item.required_glibc.split('.')[1]) > 28)
      || item.uses_libcuda !== item.needed.includes('libcuda.so.1')) fail('runtime_receipt_invalid', 'ELF closure entry is invalid');
    elfPaths.add(item.path); providedLibraries.add(path.basename(item.path)); if (item.soname) providedLibraries.add(item.soname);
  }
  const hostLibraries = new Set(['linux-vdso.so.1', 'ld-linux-x86-64.so.2', 'libc.so.6', 'libm.so.6', 'libpthread.so.0', 'libdl.so.2', 'librt.so.1', 'libutil.so.1', 'libresolv.so.2', 'libcuda.so.1']);
  for (const item of document.elf) for (const needed of item.needed) if (!providedLibraries.has(needed) && !hostLibraries.has(needed)) fail('runtime_elf_unresolved', 'ELF dependency escaped the closed runtime and documented host boundary');
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

function validateRuntimeInputAuthority(document) {
  const wheelhouse = JSON.parse(fs.readFileSync(path.join(ROOT, 'release', 'inputs', 'python-wheelhouse.v1.json')));
  const sources = JSON.parse(fs.readFileSync(path.join(ROOT, 'release', 'inputs', 'runtime-sources.v1.json')));
  if (wheelhouse.schema !== 'voice-agent.python-wheelhouse.v1' || wheelhouse.python !== '3.12.13' || !Array.isArray(wheelhouse.wheels)
    || sources.schema !== 'voice-agent.runtime-sources.v1' || sources.node_in_application_runtime !== false || !Array.isArray(sources.inputs)
    || !sources.builder || document.builder.image !== sources.builder.image || document.builder.manifest_digest !== sources.builder.manifest_digest) fail('runtime_input_authority_invalid', 'committed runtime input authority is invalid');
  const components = new Map(document.components.map((item) => [item.sha256, item]));
  for (const item of wheelhouse.wheels) {
    const observed = components.get(item.sha256);
    if (!observed || observed.name !== item.name || observed.version !== item.version || observed.size !== item.size || observed.download_url !== item.url) fail('runtime_input_authority_invalid', 'runtime receipt omits or changes an exact wheel input');
  }
  for (const item of sources.inputs.filter((value) => value.purpose !== 'web-and-sea-build-only')) {
    const observed = components.get(item.sha256);
    if (!observed || observed.size !== item.size || observed.download_url !== item.url) fail('runtime_input_authority_invalid', 'runtime receipt omits or changes an exact source input');
  }
  if (document.files.some((item) => item.path.startsWith('node/'))) fail('runtime_input_authority_invalid', 'Node is forbidden in the application runtime');
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
  const pythonLines = ['gateway.lock', 'stt.lock', 'tts.lock'].flatMap((name) => fs.readFileSync(path.join(ROOT, 'requirements-release', name), 'utf8').split('\n').map((line) => line.trim()).filter((line) => line && !line.startsWith('#')));
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

function spdxLicense(value) { return /^[A-Za-z0-9.+-]+(?: WITH [A-Za-z0-9.+-]+)?$/.test(value) ? value : `LicenseRef-${String(value).replace(/[^A-Za-z0-9.-]+/g, '-').replace(/^-+|-+$/g, '')}`; }
function createSpdx({ entries, assets, commit, timestamp, version, artifactSha256, components = [] }) {
  const files = entries.filter((item) => item.type === 'file').map((item, index) => ({
    SPDXID: `SPDXRef-File-${index + 1}`, checksums: [{ algorithm: 'SHA256', checksumValue: item.sha256 }],
    copyrightText: 'NOASSERTION', fileName: `./${item.path}`, licenseConcluded: 'NOASSERTION',
  }));
  const packages = [{ SPDXID: 'SPDXRef-Package-Voice-Agent', name: 'Voice Agent', versionInfo: version, downloadLocation: 'NOASSERTION', filesAnalyzed: true,
    licenseConcluded: 'MIT', licenseDeclared: 'MIT', copyrightText: 'Copyright (c) 2026 Pasha' },
  ...components.map((item, index) => ({ SPDXID: `SPDXRef-Package-Runtime-${index + 1}`, name: item.name, versionInfo: item.version, downloadLocation: item.download_url,
    filesAnalyzed: false, checksums: [{ algorithm: 'SHA256', checksumValue: item.sha256 }], licenseConcluded: spdxLicense(item.license), licenseDeclared: spdxLicense(item.license), copyrightText: 'NOASSERTION' })),
  ...assets.map((item, index) => ({ SPDXID: `SPDXRef-Package-Asset-${index + 1}`, name: item.id, versionInfo: item.id, downloadLocation: item.url,
    filesAnalyzed: false, checksums: [{ algorithm: 'SHA256', checksumValue: item.sha256 }], licenseConcluded: spdxLicense(item.license.id), licenseDeclared: spdxLicense(item.license.id), copyrightText: 'NOASSERTION' }))];
  const relationships = [{ spdxElementId: 'SPDXRef-DOCUMENT', relationshipType: 'DESCRIBES', relatedSpdxElement: 'SPDXRef-Package-Voice-Agent' },
    ...files.map((item) => ({ spdxElementId: 'SPDXRef-Package-Voice-Agent', relationshipType: 'CONTAINS', relatedSpdxElement: item.SPDXID })),
    ...packages.slice(1).map((item) => ({ spdxElementId: 'SPDXRef-DOCUMENT', relationshipType: 'DESCRIBES', relatedSpdxElement: item.SPDXID }))];
  return { SPDXID: 'SPDXRef-DOCUMENT', creationInfo: { created: timestamp, creators: ['Tool: voice-agent-release-candidate'] }, dataLicense: 'CC0-1.0',
    documentNamespace: `https://github.com/prineycom/voice-agent-v2/releases/spdx/${commit}/${artifactSha256}`, files, name: `voice-agent-${version}`, packages, relationships, spdxVersion: 'SPDX-2.3' };
}
function validateSpdx(document) {
  exactKeys(document, ['SPDXID', 'creationInfo', 'dataLicense', 'documentNamespace', 'files', 'name', 'packages', 'relationships', 'spdxVersion'], 'spdx_invalid');
  if (document.spdxVersion !== 'SPDX-2.3' || document.dataLicense !== 'CC0-1.0' || document.SPDXID !== 'SPDXRef-DOCUMENT'
    || typeof document.documentNamespace !== 'string' || !document.documentNamespace.startsWith('https://')
    || !Array.isArray(document.files) || !Array.isArray(document.packages) || document.packages.length < 1 || !Array.isArray(document.relationships)) fail('spdx_invalid', 'SPDX 2.3 document identity is invalid');
  const ids = new Set(['SPDXRef-DOCUMENT']);
  for (const item of [...document.files, ...document.packages]) {
    if (!item || typeof item.SPDXID !== 'string' || !/^SPDXRef-[A-Za-z0-9.-]+$/.test(item.SPDXID) || ids.has(item.SPDXID)) fail('spdx_invalid', 'SPDX element identity is invalid');
    ids.add(item.SPDXID);
  }
  if (document.relationships.some((item) => !ids.has(item.spdxElementId) || !ids.has(item.relatedSpdxElement))) fail('spdx_invalid', 'SPDX relationship is unresolved');
  return document;
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
  const sbom = createSpdx({ entries, assets, commit, timestamp, version, artifactSha256: sha256(artifactBytes), components: sourceReceipt.components || [] });
  validateSpdx(sbom);
  const provenance = { schema: 'voice-agent.application-provenance.v1', artifact_bytes: artifactBytes.length, artifact_sha256: sha256(artifactBytes), build_id: commit, manifest_sha256: sha256(manifestBytes), platform: PLATFORM, platform_contract: platformContract, runtime_source_receipt_sha256: sha256(Buffer.from(canonical(sourceReceipt))), source_commit: commit, source_timestamp: timestamp, version };
  writeCanonical(path.join(output, 'sbom.spdx.json'), sbom); writeCanonical(path.join(output, 'provenance.json'), provenance);
  const channel = { channel: 'stable', expires_at: options.expiresAt, generated_at: timestamp, releases: [{ artifact_bytes: artifactBytes.length, artifact_sha256: sha256(artifactBytes), artifact_url: artifactUrl,
    assets, build_id: commit, launcher: options.launcher || null, manifest_sha256: sha256(manifestBytes), maximum_data_schema: 2, minimum_data_schema: 2, minimum_launcher_protocol: 1, platform: PLATFORM, version }], schema: 'voice-agent.channel.v1', sequence };
  core.validateChannel(channel, { now: new Date(timestamp), trustedSequence: sequence });
  writeCanonical(path.join(output, options.emitChannel === false ? 'channel-template.json' : 'stable.json'), channel);
  writeCanonical(path.join(output, 'candidate-receipt.json'), { artifact: artifactName, artifact_bytes: artifactBytes.length, artifact_sha256: sha256(artifactBytes), manifest_sha256: sha256(manifestBytes), schema: 'voice-agent.release-candidate.v1', sequence, signed: false, version });
  return { artifactName, channel, manifest, provenance, sbom };
}

function modelSetsForAssets(assets) {
  const authority = JSON.parse(fs.readFileSync(path.join(ROOT, 'release', 'inputs', 'model-assets.v1.json')));
  exactKeys(authority, ['distribution_scope', 'platform', 'schema', 'sets'], 'model_asset_authority_invalid');
  if (authority.schema !== 'voice-agent.model-assets.v1' || authority.platform !== PLATFORM || authority.distribution_scope !== 'private-personal-noncommercial'
    || !Array.isArray(authority.sets) || authority.sets.length !== 4) fail('model_asset_authority_invalid', 'committed model set authority is invalid');
  const descriptors = new Map(assets.map((item) => [item.id, item]));
  const sets = authority.sets.map((set) => {
    exactKeys(set, ['aggregate_sha256', 'files', 'id', 'kind', 'license', 'license_evidence_url'], 'model_asset_authority_invalid');
    const files = set.files.map((file) => {
      exactKeys(file, ['asset_id', 'relative_path', 'sha256', 'size', 'url'], 'model_asset_authority_invalid');
      const descriptor = descriptors.get(file.asset_id);
      if (!descriptor || descriptor.kind !== 'model' || descriptor.reachability !== 'required' || descriptor.sha256 !== file.sha256 || descriptor.size !== file.size) fail('model_asset_authority_invalid', 'signed candidate model asset differs from the closed committed set');
      return { asset_id: file.asset_id, relative_path: file.relative_path, sha256: file.sha256, size: file.size };
    });
    const hash = crypto.createHash('sha256'); for (const file of [...files].sort((a, b) => a.relative_path.localeCompare(b.relative_path))) hash.update(Buffer.from(`${file.relative_path}\0${file.size}\0${file.sha256}\n`));
    if (hash.digest('hex') !== set.aggregate_sha256) fail('model_asset_authority_invalid', 'committed model aggregate differs');
    return { aggregate_sha256: set.aggregate_sha256, files, id: set.id, kind: set.kind };
  });
  const document = { schema: 'voice-agent.model-sets.v1', sets };
  core.loadAssetCache(installer).validateModelSets(document, assets);
  return document;
}

function buildRuntimeTree(runtimeRoot, runtimeReceipt, webRoot, modelSets = null) {
  const tree = new Map();
  for (const item of runtimeReceipt.files) addTreeFile(tree, `runtime/${item.path}`, fs.readFileSync(path.join(runtimeRoot, ...item.path.split('/'))), item.mode);
  const productionExcluded = new Set(['src/voice_agent_v2/operations.py', 'src/voice_agent_v2/operations_cli.py', 'src/voice_agent_v2/local_tts.py', 'src/voice_agent_v2/cloud_llm.py']);
  const productionConfig = new Set(['config/agent-capabilities-v1.json', 'config/silero-kseniya-tts-v1.json']);
  for (const relative of git('ls-files', 'src/voice_agent_v2', 'scripts/run_slice6.py', 'scripts/silero_kseniya_worker.py', 'config', 'LICENSE', 'THIRD_PARTY_NOTICES.md').split('\n').filter(Boolean)) {
    if (productionExcluded.has(relative) || relative.startsWith('config/') && !productionConfig.has(relative)) continue;
    addTreeFile(tree, `app/${relative}`, fs.readFileSync(path.join(ROOT, relative)), '0444');
  }
  if (!tree.has('app/src/voice_agent_v2/faster_whisper_runner.py')) fail('runtime_source_incomplete', 'production source closure lacks its STT runner');
  function copyWeb(directory, prefix = 'web') {
    for (const name of fs.readdirSync(directory).sort()) {
      const filename = path.join(directory, name); const relative = `${prefix}/${name}`; const metadata = fs.lstatSync(filename);
      if (metadata.isDirectory() && !metadata.isSymbolicLink()) copyWeb(filename, relative);
      else if (metadata.isFile() && !metadata.isSymbolicLink()) addTreeFile(tree, relative, fs.readFileSync(filename), '0444');
      else fail('release_undeclared_file', 'built web output contains a link or special file');
    }
  }
  copyWeb(webRoot);
  const wrapper = Buffer.from('#!/bin/sh\nset -eu\nHERE=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)\n: "${XDG_CONFIG_HOME:=$HOME/.config}"\nexport VOICE_AGENT_RUNTIME_CONFIG="$XDG_CONFIG_HOME/voice-agent/runtime.json"\nexec "$HERE/runtime/python/bin/python3" -I -B "$HERE/app/scripts/run_slice6.py" "$@"\n');
  addTreeFile(tree, 'bin/voice-agent-runtime', wrapper, '0555');
  const closedModels = modelSets || { schema: 'voice-agent.model-sets.v1', sets: [] };
  addTreeFile(tree, 'descriptors/local-models.json', Buffer.from(canonical(closedModels)), '0444');
  addTreeFile(tree, 'descriptors/model-sets.json', Buffer.from(canonical(closedModels)), '0444');
  addTreeFile(tree, 'descriptors/runtime-receipt.json', Buffer.from(canonical(runtimeReceipt)), '0444');
  addTreeFile(tree, 'descriptors/runtime.json', Buffer.from(canonical({ application_protocol: 1, cuda: runtimeReceipt.cuda, libc: runtimeReceipt.libc, platform: PLATFORM, python: runtimeReceipt.python, schema: 'voice-agent.bundled-runtime.v1' })), '0444');
  addTreeFile(tree, 'descriptors/runtime-config-template.json', Buffer.from(canonical({ application_protocol: 1, executables: { livekit: 'runtime/livekit/bin/livekit-server', llama: 'runtime/llama/bin/llama-server', python: 'runtime/python/bin/python3' }, model_sets: Object.fromEntries(closedModels.sets.map((set) => [set.kind, set.aggregate_sha256])), mutable_paths: { agent_data: 'xdg-data/agent-environment', logs: 'xdg-state/logs', state: 'xdg-state/runtime', temp: 'xdg-runtime/service' }, schema: 'voice-agent.runtime-config-template.v1', web: 'web' })), '0444');
  addTreeFile(tree, 'descriptors/service-compatibility.json', Buffer.from(canonical({ application_protocol: { maximum: 1, minimum: 1 }, config_schema: { maximum: 2, minimum: 2 }, data_schema: { maximum: 2, minimum: 2 }, launcher_protocol: { maximum: 1, minimum: 1 }, schema: 'voice-agent.service-compatibility.v1', service_template_sha256: installer.UNIT_CONTRACT_SHA256 })), '0444');
  return tree;
}

function runtimeReceiptCommand(values) {
  const root = path.resolve(values.runtimeRoot); const commit = values.sourceCommit;
  if (!COMMIT.test(commit)) fail('runtime_receipt_invalid', 'runtime source commit is invalid');
  const files = []; const elf = [];
  function walk(directory, prefix = '') {
    for (const name of fs.readdirSync(directory).sort()) {
      const relative = prefix ? `${prefix}/${name}` : name; const filename = path.join(directory, name); const metadata = fs.lstatSync(filename);
      if (metadata.isDirectory() && !metadata.isSymbolicLink()) walk(filename, relative);
      else if (metadata.isFile() && !metadata.isSymbolicLink() && metadata.nlink === 1) {
        const mode = metadata.mode & 0o777; if (![0o444, 0o555].includes(mode)) fail('runtime_receipt_invalid', 'runtime file mode is not normalized');
        const bytes = fs.readFileSync(filename); files.push({ mode: `0${mode.toString(8)}`, path: relative, sha256: sha256(bytes), size: bytes.length });
        if (bytes.length >= 20 && bytes.subarray(0, 4).equals(Buffer.from([0x7f, 0x45, 0x4c, 0x46]))) {
          elfMachine(filename); const dynamic = run('readelf', ['-dW', filename], { code: 'runtime_elf_invalid', message: 'runtime ELF dynamic receipt is unavailable' });
          const needed = [...dynamic.matchAll(/\(NEEDED\).*\[([^\]]+)\]/g)].map((match) => match[1]).sort();
          const soname = /\(SONAME\).*\[([^\]]+)\]/.exec(dynamic); const runpath = /\((?:RUNPATH|RPATH)\).*\[([^\]]*)\]/.exec(dynamic);
          const versions = run('readelf', ['--version-info', '-W', filename], { code: 'runtime_elf_invalid', message: 'runtime ELF version receipt is unavailable' });
          const glibc = [...versions.matchAll(/GLIBC_2\.([0-9]+)/g)].map((match) => Number(match[1])); const requiredGlibc = glibc.length ? `2.${Math.max(...glibc)}` : null;
          elf.push({ needed, path: relative, required_glibc: requiredGlibc, runpath: runpath ? runpath[1] : '', soname: soname ? soname[1] : null, uses_libcuda: needed.includes('libcuda.so.1') });
        }
      } else fail('runtime_receipt_invalid', 'runtime tree contains a link or special file');
    }
  }
  walk(root); files.sort((a, b) => a.path.localeCompare(b.path)); elf.sort((a, b) => a.path.localeCompare(b.path));
  const hash = crypto.createHash('sha256'); for (const item of files) hash.update(Buffer.from(`${item.path}\0${item.mode}\0${item.size}\0${item.sha256}\n`));
  const wheelhouse = JSON.parse(fs.readFileSync(path.join(ROOT, 'release', 'inputs', 'python-wheelhouse.v1.json'))); const sources = JSON.parse(fs.readFileSync(path.join(ROOT, 'release', 'inputs', 'runtime-sources.v1.json')));
  const components = [...wheelhouse.wheels.map((item) => ({ download_url: item.url, license: item.license || 'NOASSERTION', name: item.name, sha256: item.sha256, size: item.size, version: item.version })),
    ...sources.inputs.filter((item) => item.purpose !== 'web-and-sea-build-only').map((item) => ({ download_url: item.url, license: item.license, name: item.name, sha256: item.sha256, size: item.size, version: item.version }))];
  const document = { architecture: 'x86_64', builder: { image: sources.builder.image, manifest_digest: sources.builder.manifest_digest }, components, cuda: { minimum_driver: '575.51.03', runtime: 'cuda-12.9' }, elf, files, libc: { family: 'glibc', minimum: '2.28' }, platform: PLATFORM,
    python: { version: '3.12.13' }, schema: 'voice-agent.runtime-bundle.v1', source: { distribution_scope: 'private-personal-noncommercial', immutable_url: `https://github.com/prineycom/voice-agent-v2/tree/${commit}/release/inputs`, license_evidence_url: `https://github.com/prineycom/voice-agent-v2/blob/${commit}/THIRD_PARTY_NOTICES.md`, sha256: hash.digest('hex') }, test_only: false };
  validateRuntimeReceipt(document, root, true); validateRuntimeInputAuthority(document); writeCanonical(values.output, document); return { files: files.length, elf: elf.length, runtime_sha256: document.source.sha256 };
}

function parseInput(filename) {
  const input = readCanonical(filename);
  exactKeys(input, ['artifact_base_url', 'asset_evidence', 'assets', 'expires_at', 'generated_at', 'github_channel_path', 'github_ref', 'github_repository', 'launcher_protocol', 'launcher_version', 'platform_contract', 'sequence', 'version'], 'release_input_invalid');
  if (!VERSION.test(input.version) || !VERSION.test(input.launcher_version) || !Number.isSafeInteger(input.launcher_protocol) || input.launcher_protocol !== core.LAUNCHER_PROTOCOL
    || !Number.isSafeInteger(input.sequence) || input.sequence < 1 || !Array.isArray(input.assets) || input.assets.length < 2) fail('release_input_invalid', 'release input identity is invalid');
  if (!/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(input.github_repository) || !/^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$/.test(input.github_ref)
    || !/^[A-Za-z0-9][A-Za-z0-9._/-]{0,255}\.json$/.test(input.github_channel_path)) fail('release_input_invalid', 'private GitHub identity is invalid');
  sourceModule.parseHttpsUrl(input.artifact_base_url, 'release_input_invalid');
  core.loadAssetCache(installer).validateDescriptors(input.assets);
  modelSetsForAssets(input.assets);
  if (!Array.isArray(input.asset_evidence) || input.asset_evidence.length !== input.assets.length) fail('asset_evidence_missing', 'every external asset requires one source/license receipt');
  const evidence = new Map();
  for (const item of input.asset_evidence) {
    exactKeys(item, ['distribution_scope', 'id', 'immutable_locator', 'license_evidence_url', 'sha256', 'size'], 'asset_evidence_missing');
    sourceModule.parseHttpsUrl(item.immutable_locator, 'asset_evidence_missing'); sourceModule.parseHttpsUrl(item.license_evidence_url, 'asset_evidence_missing');
    if (evidence.has(item.id) || item.distribution_scope !== 'private-personal-noncommercial' || !SHA256.test(item.sha256) || !Number.isSafeInteger(item.size) || item.size < 1) fail('asset_evidence_missing', 'external asset evidence is invalid');
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
  const runtimeReceipt = readCanonical(values.runtimeReceipt); validateRuntimeReceipt(runtimeReceipt, values.runtimeRoot, true); validateRuntimeInputAuthority(runtimeReceipt);
  for (const name of ['python/bin/python3', 'livekit/bin/livekit-server', 'llama/bin/llama-server']) elfMachine(path.join(values.runtimeRoot, name));
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'voice-agent-launcher-candidate-'));
  try {
    const launcherPath = path.join(temporary, 'voice-agent-launcher-linux-x86_64');
    run(path.join(ROOT, 'launcher/build'), ['--output', launcherPath, '--public-key', values.publicKey, '--github-repository', input.github_repository, '--github-ref', input.github_ref,
      '--github-channel-path', input.github_channel_path, '--launcher-version', input.launcher_version, '--launcher-protocol', String(input.launcher_protocol), '--source-timestamp', input.generated_at, '--source-commit', commit], { code: 'launcher_build_failed', message: 'exact SEA launcher build failed' });
    const launcherBytes = fs.readFileSync(launcherPath); const launcherSha = sha256(launcherBytes); const launcherUrl = `${input.artifact_base_url}/voice-agent-launcher-linux-x86_64`;
    const parsed = new URL(launcherUrl);
    const launcherDescriptor = core.loadAssetCache(installer).validateDescriptor({ authority: { origin: parsed.origin, path_prefix: parsed.pathname.slice(0, parsed.pathname.lastIndexOf('/') + 1) },
      compatibility: { maximum_application_protocol: 1, maximum_launcher_protocol: input.launcher_protocol, minimum_application_protocol: 1, minimum_launcher_protocol: input.launcher_protocol },
      digest: `sha256:${launcherSha}`, id: `launcher-${input.launcher_version.replaceAll('.', '-')}`, kind: 'launcher', license: { acceptance: 'not_required', id: 'Voice-Agent-Launcher' },
      platform: PLATFORM, reachability: 'required', required_free_space_reserve: 0, sha256: launcherSha, size: launcherBytes.length, url: launcherUrl });
    const webRoot = path.resolve(values.webRoot); regularFile(path.join(webRoot, 'index.html'), 16 * 1024 * 1024);
    const modelSets = modelSetsForAssets(input.assets);
    const tree = buildRuntimeTree(values.runtimeRoot, runtimeReceipt, webRoot, modelSets);
    addTreeFile(tree, 'descriptors/asset-evidence.json', Buffer.from(canonical({ assets: input.asset_evidence, schema: 'voice-agent.asset-source-evidence.v1' })), '0444');
    const result = assembleFromTree({ output: values.output, tree, version: input.version, commit, timestamp: input.generated_at, expiresAt: input.expires_at, sequence: input.sequence, assets: input.assets,
      artifactUrl: `${input.artifact_base_url}/voice-agent-${input.version}-linux-x86_64-nvidia.tar.zst`, sourceReceipt: runtimeReceipt, platformContract: input.platform_contract, launcher: launcherDescriptor, emitChannel: false });
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
  validateSpdx(readCanonical(path.join(directory, 'sbom.spdx.json')));
  readCanonical(path.join(directory, 'provenance.json'));
  const channelName = fs.existsSync(path.join(directory, 'stable.json')) ? 'stable.json' : 'channel-template.json';
  const channel = readCanonical(path.join(directory, channelName)); core.validateChannel(channel, { now: new Date(channel.generated_at), trustedSequence: channel.sequence });
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

function finalizeChannel(values) {
  const candidate = verifyCandidate(values.candidate); const template = readCanonical(path.join(values.candidate, 'channel-template.json'));
  const receipt = readCanonical(values.assetReceipt); exactKeys(receipt, ['assets', 'release_id', 'repository', 'schema', 'tag'], 'publication_receipt_invalid');
  if (receipt.schema !== 'voice-agent.immutable-assets.v1' || receipt.repository !== values.repository || !Number.isSafeInteger(receipt.release_id) || receipt.release_id < 1
    || receipt.tag !== `v${candidate.version}` || !Array.isArray(receipt.assets)) fail('publication_receipt_invalid', 'immutable asset receipt identity is invalid');
  const bySha = new Map();
  for (const item of receipt.assets) {
    exactKeys(item, ['id', 'name', 'sha256', 'size', 'url'], 'publication_receipt_invalid');
    const expectedUrl = `https://api.github.com/repos/${values.repository}/releases/assets/${item.id}`;
    if (!Number.isSafeInteger(item.id) || item.id < 1 || item.url !== expectedUrl || !SHA256.test(item.sha256) || !Number.isSafeInteger(item.size) || item.size < 1 || bySha.has(item.sha256)) fail('publication_receipt_invalid', 'immutable asset identity is invalid');
    bySha.set(item.sha256, item);
  }
  const release = template.releases[0];
  const program = bySha.get(release.artifact_sha256); if (!program || program.size !== release.artifact_bytes) fail('publication_receipt_invalid', 'application artifact immutable ID is missing');
  release.artifact_url = program.url;
  const replaceDescriptor = (descriptor) => {
    const item = bySha.get(descriptor.sha256); if (!item || item.size !== descriptor.size) fail('publication_receipt_invalid', 'signed dependency immutable ID is missing');
    const parsed = new URL(item.url); return { ...descriptor, url: item.url, authority: { origin: parsed.origin, path_prefix: parsed.pathname.slice(0, parsed.pathname.lastIndexOf('/') + 1) } };
  };
  release.assets = release.assets.map(replaceDescriptor); if (release.launcher) release.launcher = replaceDescriptor(release.launcher);
  core.validateChannel(template, { now: new Date(template.generated_at), trustedSequence: template.sequence });
  writeCanonical(path.join(values.candidate, 'stable.json'), template);
  writeCanonical(path.join(values.candidate, 'immutable-assets.json'), receipt);
  writeCanonical(path.join(values.candidate, 'channel-finalization-receipt.json'), { channel_sha256: sha256(Buffer.from(canonical(template))), immutable_assets_sha256: sha256(Buffer.from(canonical(receipt))), release_id: receipt.release_id, repository: receipt.repository, schema: 'voice-agent.channel-finalization.v1', sequence: template.sequence, tag: receipt.tag });
  return { channel_sha256: sha256(Buffer.from(canonical(template))), sequence: template.sequence, version: candidate.version };
}

function signChannel(values) {
  const channelPath = path.resolve(values.channel); const candidateRoot = path.dirname(channelPath);
  if (fs.existsSync(path.join(candidateRoot, 'channel-template.json'))) {
    const finalizationPath = path.join(candidateRoot, 'channel-finalization-receipt.json'); const immutablePath = path.join(candidateRoot, 'immutable-assets.json');
    if (!fs.existsSync(finalizationPath) || !fs.existsSync(immutablePath) || path.basename(channelPath) !== 'stable.json') fail('channel_not_finalized', 'channel must cite immutable private GitHub asset IDs before signing');
    const finalization = readCanonical(finalizationPath); exactKeys(finalization, ['channel_sha256', 'immutable_assets_sha256', 'release_id', 'repository', 'schema', 'sequence', 'tag'], 'channel_not_finalized');
    if (finalization.schema !== 'voice-agent.channel-finalization.v1' || finalization.channel_sha256 !== sha256(fs.readFileSync(channelPath))
      || finalization.immutable_assets_sha256 !== sha256(fs.readFileSync(immutablePath))) fail('channel_not_finalized', 'channel finalization receipt differs');
  }
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

function publishAssets(values) {
  const candidate = verifyCandidate(values.candidate); const template = readCanonical(path.join(values.candidate, 'channel-template.json'));
  const confirmation = `${candidate.version}:${candidate.sequence}`; if (values.confirm !== confirmation) fail('publication_confirmation_required', `explicit confirmation must equal ${confirmation}`);
  if (!/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(values.repository)) fail('publication_input_invalid', 'repository identity is invalid');
  const map = readCanonical(values.assetMap); exactKeys(map, ['assets', 'schema'], 'publication_input_invalid');
  if (map.schema !== 'voice-agent.publication-assets-input.v1' || !Array.isArray(map.assets)) fail('publication_input_invalid', 'publication asset input is invalid');
  const expected = new Map();
  const add = (name, filename, sha, size) => {
    regularFile(filename, size); const bytes = fs.readFileSync(filename); if (bytes.length !== size || sha256(bytes) !== sha || expected.has(sha)) fail('publication_input_invalid', 'publication asset bytes differ');
    expected.set(sha, { name, filename, sha256: sha, size });
  };
  const release = template.releases[0]; const artifactName = readCanonical(path.join(values.candidate, 'candidate-receipt.json')).artifact;
  add(artifactName, path.join(values.candidate, artifactName), release.artifact_sha256, release.artifact_bytes);
  if (release.launcher) add('voice-agent-launcher-linux-x86_64', path.join(values.candidate, 'voice-agent-launcher-linux-x86_64'), release.launcher.sha256, release.launcher.size);
  for (const item of map.assets) {
    exactKeys(item, ['id', 'path', 'sha256', 'size'], 'publication_input_invalid'); const descriptor = release.assets.find((value) => value.id === item.id);
    if (!descriptor || descriptor.sha256 !== item.sha256 || descriptor.size !== item.size) fail('publication_input_invalid', 'external publication asset differs from signed template');
    add(item.id, path.resolve(item.path), item.sha256, item.size);
  }
  if (expected.size !== release.assets.length + 1 + (release.launcher ? 1 : 0)) fail('publication_input_invalid', 'publication asset set is incomplete');
  const metadataNames = fs.readdirSync(values.candidate).filter((name) => !name.startsWith('.') && ![artifactName, 'voice-agent-launcher-linux-x86_64', 'stable.json', 'stable.json.sig', 'channel-template.json', 'channel-finalization-receipt.json', 'signing-receipt.json'].includes(name));
  for (const name of metadataNames) { const filename = path.join(values.candidate, name); const metadata = fs.lstatSync(filename); if (metadata.isFile()) { const bytes = fs.readFileSync(filename); add(name, filename, sha256(bytes), bytes.length); } }
  const tag = `v${candidate.version}`;
  if (values.dryRun) return { schema: 'voice-agent.immutable-assets-plan.v1', repository: values.repository, tag, source_commit: release.build_id, assets: [...expected.values()].map(({ filename, ...item }) => item) };
  if (spawnSync('gh-axi', ['release', 'view', tag, '--repo', values.repository, '--json', 'tagName'], { encoding: 'utf8', timeout: 30000 }).status === 0) fail('publication_collision', 'remote release tag already exists');
  run('gh-axi', ['release', 'create', tag, '--repo', values.repository, '--target', release.build_id, '--title', `Voice Agent ${candidate.version}`, '--notes', `Voice Agent immutable assets ${candidate.version}`], { code: 'publication_failed', message: 'release creation failed without content' });
  for (const item of expected.values()) run('gh-axi', ['release', 'upload', tag, `${item.filename}#${item.name}`, '--repo', values.repository], { code: 'publication_failed', message: 'asset upload failed without content' });
  const api = spawnSync('gh-axi', ['api', `repos/${values.repository}/releases/tags/${tag}`], { encoding: 'utf8', timeout: 30000 });
  let remote; try { if (api.status !== 0) throw new Error('api'); remote = JSON.parse(api.stdout); } catch { fail('publication_remote_invalid', 'immutable release response is invalid'); }
  if (!Number.isSafeInteger(remote.id) || !Array.isArray(remote.assets)) fail('publication_remote_invalid', 'immutable release identity is invalid');
  const receiptAssets = [];
  for (const expectedItem of expected.values()) {
    const item = remote.assets.find((value) => value.name === expectedItem.name);
    if (!item || !Number.isSafeInteger(item.id) || item.id < 1 || item.size !== expectedItem.size || item.url !== `https://api.github.com/repos/${values.repository}/releases/assets/${item.id}`) fail('publication_readback_mismatch', 'immutable asset API identity differs');
    receiptAssets.push({ id: item.id, name: item.name, sha256: expectedItem.sha256, size: item.size, url: item.url });
  }
  const receipt = { assets: receiptAssets.sort((a, b) => a.name.localeCompare(b.name)), release_id: remote.id, repository: values.repository, schema: 'voice-agent.immutable-assets.v1', tag };
  writeCanonical(values.output, receipt, 0o600); return receipt;
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
  const expectedChannelUrl = `https://api.github.com/repos/${values.repository}/contents/stable.json?ref=release-channel`;
  const launcherProvenance = path.join(values.candidate, 'voice-agent-launcher-linux-x86_64.provenance.json');
  if (fs.existsSync(launcherProvenance)) {
    const provenance = readCanonical(launcherProvenance);
    if (provenance.github_repository !== values.repository || provenance.github_ref !== 'release-channel' || provenance.github_channel_path !== 'stable.json'
      || provenance.github_api_origin !== 'https://api.github.com/' || provenance.public_key_sha256 !== publicKeySha256) fail('publication_input_invalid', 'compiled private GitHub identity or trust root differs from publication authority');
  }
  const assets = fs.readdirSync(values.candidate).filter((name) => !name.startsWith('.') && !['stable.json', 'stable.json.sig', 'channel-template.json', 'channel-finalization-receipt.json', 'immutable-assets.json', 'signing-receipt.json'].includes(name)).sort().map((name) => { const bytes = fs.readFileSync(path.join(values.candidate, name)); return { bytes: bytes.length, name, sha256: sha256(bytes) }; });
  const channel_documents = ['stable.json', 'stable.json.sig'].map((name) => { const bytes = fs.readFileSync(path.join(values.candidate, name)); return { bytes: bytes.length, name, sha256: sha256(bytes) }; });
  return { schema: 'voice-agent.publication-plan.v1', repository: values.repository, tag, version: candidate.version, sequence: candidate.sequence, source_commit: channel.releases[0].build_id, assets, channel_documents, channel_url: expectedChannelUrl, collision_policy: 'refuse', readback: 'exact-sha256' };
}

function publishWithRemote(plan, values, remote) {
  const exists = remote.exists(plan);
  if (remote.assetsStaged) { if (!exists) fail('publication_remote_invalid', 'immutable release is missing'); }
  else if (exists) fail('publication_collision', 'remote release tag already exists');
  const remoteSequence = remote.sequence(plan);
  if (!Number.isSafeInteger(remoteSequence) || remoteSequence < 0 || remoteSequence >= plan.sequence) fail('publication_sequence_collision', 'remote channel sequence is invalid or not older');
  if (remote.verifyTag(plan) !== true) fail('publication_tag_mismatch', 'immutable release tag does not resolve to the candidate source commit');
  if (remote.assetsStaged && remote.verifyStaged(plan) !== true) fail('publication_readback_mismatch', 'immutable release IDs changed after channel finalization');
  if (!remote.assetsStaged) {
    remote.create(plan);
    for (const asset of plan.assets) remote.upload(plan, asset, path.join(values.candidate, asset.name));
  }
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
    assetsStaged: true,
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
    verifyStaged(value) {
      const receipt = readCanonical(path.join(values.candidate, 'immutable-assets.json'));
      const release = ghApi('GET', `repos/${value.repository}/releases/tags/${encodeURIComponent(value.tag)}`);
      if (!release || release.id !== receipt.release_id || !Array.isArray(release.assets) || receipt.repository !== value.repository || receipt.tag !== value.tag) return false;
      return receipt.assets.every((expected) => release.assets.some((item) => item.id === expected.id && item.name === expected.name && item.size === expected.size && item.url === expected.url));
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

async function preflightRuntimeCommand(values) {
  requireCleanCurrentCommit();
  const result = await runtimeAssembler.preflight(values, { root: ROOT });
  return { builder_image: result.builder_image, node_runtime: result.node_runtime, npm_boundary: result.npm_boundary, tool_authority_sha256: result.tool_authority_sha256, tools: result.tools };
}

async function assembleRuntimeCommand(values) {
  const commit = requireCleanCurrentCommit();
  const assembled = await runtimeAssembler.assemble(values, { root: ROOT });
  const receiptPath = path.join(assembled.output, 'runtime-receipt.json');
  const result = runtimeReceiptCommand({ runtimeRoot: path.join(assembled.output, 'runtime'), sourceCommit: commit, output: receiptPath });
  const web = []; const webRoot = path.join(assembled.output, 'web');
  function walk(directory, prefix = '') { for (const name of fs.readdirSync(directory).sort()) { const filename = path.join(directory, name); const relative = prefix ? `${prefix}/${name}` : name; const metadata = fs.lstatSync(filename); if (metadata.isDirectory() && !metadata.isSymbolicLink()) walk(filename, relative); else if (metadata.isFile() && !metadata.isSymbolicLink() && metadata.nlink === 1) { const bytes = fs.readFileSync(filename); pathLeakScan(bytes); web.push({ path: relative, sha256: sha256(bytes), size: bytes.length }); } else fail('web_output_invalid', 'static web output contains a link or special file'); } }
  walk(webRoot); if (!web.some((item) => item.path === 'index.html') || web.some((item) => item.path.includes('node_modules'))) fail('web_output_invalid', 'static web closure is incomplete or contains Node');
  const document = { schema: 'voice-agent.runtime-assembly.v1', builder_image: assembled.authority.sources.builder.image, inputs: assembled.authority.inputs.map((item) => ({ sha256: item.sha256, size: item.size })).sort((a, b) => a.sha256.localeCompare(b.sha256)), node_runtime: assembled.node_runtime, npm_boundary: assembled.npm_boundary, runtime_receipt_sha256: sha256(fs.readFileSync(receiptPath)), source_commit: commit, tool_authority_sha256: assembled.tool_authority_sha256, tools: assembled.tools, web };
  writeCanonical(path.join(assembled.output, 'assembly-receipt.json'), document, 0o444);
  if (values.compareWith) {
    const prior = readCanonical(path.join(path.resolve(values.compareWith), 'assembly-receipt.json'), 16 * 1024 * 1024);
    if (canonical(prior) !== canonical(document)) fail('runtime_reproducibility_mismatch', 'isolated runtime assembly receipts differ');
  }
  return { ...result, builder_image: document.builder_image, web_files: web.length, assembly_receipt_sha256: sha256(Buffer.from(canonical(document))) };
}

function args(argv) {
  const command = argv.shift(); const values = {};
  while (argv.length) { const name = argv.shift(); if (!name.startsWith('--')) fail('usage', 'invalid release option'); if (name === '--dry-run') values.dryRun = true; else if (name === '--fetch') values.fetch = true; else { if (!argv.length) fail('usage', 'release option value is missing'); values[name.slice(2).replace(/-([a-z])/g, (_, value) => value.toUpperCase())] = argv.shift(); } }
  return { command, values };
}
function required(values, names) { for (const name of names) if (!values[name]) fail('usage', `--${name.replace(/[A-Z]/g, (v) => `-${v.toLowerCase()}`)} is required`); }
async function main(argv = process.argv.slice(2)) {
  try {
    const parsed = args([...argv]); let result;
    if (parsed.command === 'candidate') { required(parsed.values, ['input', 'runtimeRoot', 'runtimeReceipt', 'webRoot', 'publicKey', 'output']); result = candidateCommand(parsed.values); }
    else if (parsed.command === 'preflight-runtime') { required(parsed.values, ['cache']); result = await preflightRuntimeCommand(parsed.values); }
    else if (parsed.command === 'assemble-runtime') { required(parsed.values, ['cache', 'output']); result = await assembleRuntimeCommand(parsed.values); }
    else if (parsed.command === 'runtime-receipt') { required(parsed.values, ['runtimeRoot', 'sourceCommit', 'output']); result = runtimeReceiptCommand(parsed.values); }
    else if (parsed.command === 'publish-assets') { required(parsed.values, ['candidate', 'repository', 'assetMap', 'confirm']); if (!parsed.values.dryRun) required(parsed.values, ['output']); result = publishAssets(parsed.values); }
    else if (parsed.command === 'verify') { required(parsed.values, ['candidate']); result = verifyCandidate(parsed.values.candidate); }
    else if (parsed.command === 'finalize-channel') { required(parsed.values, ['candidate', 'assetReceipt', 'repository']); result = finalizeChannel(parsed.values); }
    else if (parsed.command === 'sign-channel') { required(parsed.values, ['channel', 'privateKey', 'output']); result = signChannel(parsed.values); }
    else if (parsed.command === 'publish') { required(parsed.values, ['candidate', 'repository', 'publicKey', 'confirm']); result = publish(parsed.values); }
    else fail('usage', 'expected preflight-runtime, assemble-runtime, runtime-receipt, candidate, verify, publish-assets, finalize-channel, sign-channel, or publish');
    process.stdout.write(`${canonical({ state: parsed.values.dryRun ? 'dry_run' : 'ok', ...result })}\n`); return 0;
  } catch (reason) {
    const code = reason instanceof ReleaseError || reason instanceof runtimeAssembler.AssemblyError ? reason.code : 'release_failed';
    const detail = code === 'runtime_tool_closure_invalid' ? `: ${reason.message}` : ': command failed safely';
    process.stderr.write(`${code}${detail}\n`); return 2;
  }
}

module.exports = { ReleaseError, assembleFromTree, assembleRuntimeCommand, buildRuntimeTree, candidateCommand, createSpdx, finalizeChannel, main, modelSetsForAssets, pathLeakScan, preflightRuntimeCommand, publicationPlan, publish, publishAssets, publishWithRemote, runtimeReceiptCommand, signChannel, validateExactDependencies, validateLocks, validateRuntimeInputAuthority, validateRuntimeReceipt, validateSourceState, validateSpdx, verifyCandidate };
if (require.main === module) main().then((code) => { process.exitCode = code; }, () => { process.stderr.write('release_failed: command failed safely\n'); process.exitCode = 2; });
