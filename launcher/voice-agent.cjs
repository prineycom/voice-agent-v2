#!/usr/bin/env node
'use strict';

const crypto = require('node:crypto');
const fs = require('node:fs');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

const LAUNCHER_VERSION = '0.2.0';
const LAUNCHER_PROTOCOL = 1;
const SUPPORTED_PLATFORM = 'linux-x86_64-nvidia';
const SERVICE_NAME = 'voice-agent-v2.service';
const SHA256 = /^[0-9a-f]{64}$/;
const BUILD_ID = /^[0-9a-f]{40}$/;
const LEGACY_RELEASE_ID = /^[0-9a-f]{24}$/;
const RELEASE_ID = /^[0-9A-Za-z][0-9A-Za-z.+-]{0,95}$/;
const VERSION = /^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-[0-9A-Za-z.-]+)?$/;
const CHANNEL_KEYS = ['channel', 'expires_at', 'generated_at', 'releases', 'schema', 'sequence'];
const CHANNEL_RELEASE_KEYS = [
  'artifact_bytes', 'artifact_sha256', 'artifact_url', 'build_id', 'manifest_sha256',
  'maximum_data_schema', 'minimum_data_schema', 'minimum_launcher_protocol', 'platform', 'version',
];
const MANIFEST_KEYS = [
  'application_protocol', 'build_id', 'config_schema', 'data_schema', 'entries', 'launcher_protocol',
  'platform', 'schema', 'service_template_sha256', 'version',
];
const MANIFEST_ENTRY_KEYS = ['mode', 'path', 'sha256', 'size', 'target', 'type'];
const LEGACY_RELEASE_KEYS = [
  'automatic_fallback', 'build_id', 'configuration_fingerprint', 'configuration_locator_sha256',
  'configuration_path', 'external_provider_supervised', 'operations_manifest_sha256',
  'operations_schema', 'provider_mode', 'release_id', 'release_tree_sha256', 'schema_version', 'source_tree',
];

class LauncherError extends Error {
  constructor(code, message) {
    super(message);
    this.name = 'LauncherError';
    this.code = code;
  }
}

function fail(code, message) {
  throw new LauncherError(code, message);
}

function exactKeys(value, expected, code = 'contract_invalid') {
  if (!value || typeof value !== 'object' || Array.isArray(value)) fail(code, 'document must be an object');
  const observed = Object.keys(value).sort();
  const wanted = [...expected].sort();
  if (observed.length !== wanted.length || observed.some((key, index) => key !== wanted[index])) {
    fail(code, 'document fields are not closed');
  }
}

function canonicalJson(value) {
  if (value === null || typeof value === 'boolean' || typeof value === 'string') return JSON.stringify(value);
  if (typeof value === 'number') {
    if (!Number.isSafeInteger(value)) fail('contract_invalid', 'numbers must be safe integers');
    return String(value);
  }
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(',')}]`;
  if (value && typeof value === 'object') {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`).join(',')}}`;
  }
  fail('contract_invalid', 'unsupported JSON value');
}

function parseCanonicalJson(bytes, maximumBytes = 1024 * 1024) {
  const input = Buffer.isBuffer(bytes) ? bytes : Buffer.from(bytes);
  if (input.length === 0 || input.length > maximumBytes) fail('contract_invalid', 'canonical document size is invalid');
  let value;
  try {
    value = JSON.parse(input.toString('utf8'));
  } catch {
    fail('contract_invalid', 'canonical document is invalid JSON');
  }
  if (Buffer.from(canonicalJson(value), 'utf8').compare(input) !== 0) {
    fail('contract_invalid', 'document is not canonical JSON');
  }
  return value;
}

function sha256(bytes) {
  return crypto.createHash('sha256').update(bytes).digest('hex');
}

function parseTime(value, code) {
  if (typeof value !== 'string' || !/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$/.test(value)) fail(code, 'time is invalid');
  const milliseconds = Date.parse(value);
  if (!Number.isFinite(milliseconds) || new Date(milliseconds).toISOString().replace('.000Z', 'Z') !== value) {
    fail(code, 'time is invalid');
  }
  return milliseconds;
}

function validateChannel(document, options = {}) {
  exactKeys(document, CHANNEL_KEYS, 'channel_invalid');
  if (document.schema !== 'voice-agent.channel.v1' || document.channel !== 'stable') {
    fail('channel_invalid', 'only the stable v1 channel is accepted');
  }
  if (!Number.isSafeInteger(document.sequence) || document.sequence < 1) fail('channel_invalid', 'sequence is invalid');
  const trustedSequence = options.trustedSequence ?? 0;
  if (!Number.isSafeInteger(trustedSequence) || trustedSequence < 0) fail('channel_invalid', 'trusted sequence is invalid');
  if (document.sequence < trustedSequence) fail('channel_sequence_rollback', 'channel sequence rolled back');
  const generated = parseTime(document.generated_at, 'channel_invalid');
  const expires = parseTime(document.expires_at, 'channel_invalid');
  const now = options.now instanceof Date ? options.now.getTime() : Date.now();
  if (generated > now + 300000 || expires <= generated) fail('channel_invalid', 'channel validity interval is invalid');
  if (expires <= now) fail('channel_expired', 'channel metadata expired');
  if (!Array.isArray(document.releases) || document.releases.length < 1 || document.releases.length > 32) {
    fail('channel_invalid', 'channel release count is invalid');
  }
  const identities = new Set();
  for (const release of document.releases) {
    exactKeys(release, CHANNEL_RELEASE_KEYS, 'channel_invalid');
    if (!VERSION.test(release.version) || !BUILD_ID.test(release.build_id) || release.platform !== SUPPORTED_PLATFORM) {
      fail('channel_invalid', 'release identity or platform is invalid');
    }
    if (!SHA256.test(release.artifact_sha256) || !SHA256.test(release.manifest_sha256)) {
      fail('channel_invalid', 'release digest is invalid');
    }
    if (!Number.isSafeInteger(release.artifact_bytes) || release.artifact_bytes < 1) fail('channel_invalid', 'artifact size is invalid');
    if (!Number.isSafeInteger(release.minimum_launcher_protocol) || release.minimum_launcher_protocol < 1) {
      fail('channel_invalid', 'launcher protocol is invalid');
    }
    if (!Number.isSafeInteger(release.minimum_data_schema) || !Number.isSafeInteger(release.maximum_data_schema)
        || release.minimum_data_schema < 1 || release.maximum_data_schema < release.minimum_data_schema) {
      fail('channel_invalid', 'data schema range is invalid');
    }
    let url;
    try { url = new URL(release.artifact_url); } catch { fail('channel_invalid', 'artifact URL is invalid'); }
    if (url.protocol !== 'https:' || url.username || url.password || url.hash || release.artifact_url.length > 2048) {
      fail('channel_invalid', 'artifact URL authority is invalid');
    }
    const identity = `${release.version}\0${release.platform}`;
    if (identities.has(identity)) fail('channel_invalid', 'duplicate release identity');
    identities.add(identity);
  }
  return document;
}

function verifySignedChannel(channelBytes, signatureBytes, publicKeyPem, options = {}) {
  const bytes = Buffer.isBuffer(channelBytes) ? channelBytes : Buffer.from(channelBytes);
  const document = parseCanonicalJson(bytes, 256 * 1024);
  let signature;
  try {
    const text = Buffer.isBuffer(signatureBytes) ? signatureBytes.toString('ascii').trim() : String(signatureBytes).trim();
    if (!/^[A-Za-z0-9+/]{86}==$/.test(text)) fail('channel_signature_invalid', 'signature encoding is invalid');
    signature = Buffer.from(text, 'base64');
    const key = crypto.createPublicKey(publicKeyPem);
    if (key.asymmetricKeyType !== 'ed25519' || signature.length !== 64 || !crypto.verify(null, bytes, key, signature)) {
      fail('channel_signature_invalid', 'channel signature is invalid');
    }
  } catch (error) {
    if (error instanceof LauncherError) throw error;
    fail('channel_signature_invalid', 'channel signature is invalid');
  }
  return validateChannel(document, options);
}

function validateRange(value, code) {
  exactKeys(value, ['maximum', 'minimum'], code);
  if (!Number.isSafeInteger(value.minimum) || !Number.isSafeInteger(value.maximum)
      || value.minimum < 1 || value.maximum < value.minimum) fail(code, 'protocol range is invalid');
}

function validateArchivePath(name, code = 'manifest_invalid') {
  if (typeof name !== 'string' || name.length < 1 || Buffer.byteLength(name) > 1024
      || name.includes('\0') || name.includes('\\') || name.startsWith('/')) fail(code, 'archive path is unsafe');
  const parts = name.split('/');
  if (parts.some((part) => part === '' || part === '.' || part === '..')) fail(code, 'archive path is unsafe');
  return parts;
}

function resolvedLinkPath(entryPath, target, code) {
  if (typeof target !== 'string' || target.length < 1 || target.startsWith('/') || target.includes('\\') || target.includes('\0')) {
    fail(code, 'archive link target is unsafe');
  }
  const stack = entryPath.split('/').slice(0, -1);
  for (const part of target.split('/')) {
    if (part === '' || part === '.') continue;
    if (part === '..') {
      if (!stack.length) fail(code, 'archive link escapes the release');
      stack.pop();
    } else {
      stack.push(part);
    }
  }
  if (!stack.length) fail(code, 'archive link target is unsafe');
  return stack.join('/');
}

function validateArtifactManifest(document) {
  exactKeys(document, MANIFEST_KEYS, 'manifest_invalid');
  if (document.schema !== 'voice-agent.platform-artifact-manifest.v1'
      || !VERSION.test(document.version) || !BUILD_ID.test(document.build_id)
      || document.platform !== SUPPORTED_PLATFORM || !SHA256.test(document.service_template_sha256)) {
    fail('manifest_invalid', 'manifest identity is invalid');
  }
  for (const name of ['launcher_protocol', 'application_protocol', 'config_schema', 'data_schema']) validateRange(document[name], 'manifest_invalid');
  if (!Array.isArray(document.entries) || document.entries.length < 1 || document.entries.length > 20000) {
    fail('manifest_invalid', 'manifest entry count is invalid');
  }
  const entries = new Map();
  for (const entry of document.entries) {
    exactKeys(entry, MANIFEST_ENTRY_KEYS, 'manifest_invalid');
    validateArchivePath(entry.path);
    if (entries.has(entry.path) || !['directory', 'file', 'symlink', 'hardlink'].includes(entry.type)
        || typeof entry.mode !== 'string' || !/^0[0-7]{3}$/.test(entry.mode)) {
      fail('manifest_invalid', 'manifest entry is invalid or duplicated');
    }
    if (entry.type === 'file') {
      if (!Number.isSafeInteger(entry.size) || entry.size < 0 || !SHA256.test(entry.sha256) || entry.target !== null) {
        fail('manifest_invalid', 'manifest file entry is invalid');
      }
    } else if (entry.type === 'directory') {
      if (entry.size !== 0 || entry.sha256 !== null || entry.target !== null) fail('manifest_invalid', 'manifest directory is invalid');
    } else if (entry.size !== 0 || entry.sha256 !== null || typeof entry.target !== 'string') {
      fail('manifest_invalid', 'manifest link is invalid');
    }
    entries.set(entry.path, entry);
  }
  for (const entry of entries.values()) {
    const parts = entry.path.split('/');
    for (let index = 1; index < parts.length; index += 1) {
      const ancestor = entries.get(parts.slice(0, index).join('/'));
      if (ancestor && ancestor.type !== 'directory') fail('manifest_invalid', 'archive entry descends through a non-directory');
    }
    if (entry.type === 'symlink' || entry.type === 'hardlink') {
      const target = resolvedLinkPath(entry.path, entry.target, 'manifest_invalid');
      const targetEntry = entries.get(target);
      if (!targetEntry || (entry.type === 'hardlink' && targetEntry.type !== 'file')) {
        fail('manifest_invalid', 'archive link target is undeclared');
      }
    }
  }
  return document;
}

function validateArchiveEntries(archiveEntries, manifest, manifestBytes) {
  validateArtifactManifest(manifest);
  if (!Array.isArray(archiveEntries) || archiveEntries.length !== manifest.entries.length + 1) {
    fail('archive_invalid', 'archive entry count differs from manifest');
  }
  const declared = new Map(manifest.entries.map((entry) => [entry.path, entry]));
  const observed = new Set();
  for (const entry of archiveEntries) {
    exactKeys(entry, MANIFEST_ENTRY_KEYS, 'archive_invalid');
    validateArchivePath(entry.path, 'archive_invalid');
    if (observed.has(entry.path)) fail('archive_duplicate_entry', 'archive contains a duplicate path');
    observed.add(entry.path);
    if (entry.path === 'release-manifest.json') {
      if (entry.type !== 'file' || entry.target !== null || entry.size !== manifestBytes.length
          || entry.sha256 !== sha256(manifestBytes)) fail('archive_invalid', 'embedded manifest differs');
      continue;
    }
    const expected = declared.get(entry.path);
    if (!expected || canonicalJson(entry) !== canonicalJson(expected)) fail('archive_invalid', 'archive entry differs from manifest');
    if (!['directory', 'file', 'symlink', 'hardlink'].includes(entry.type)) fail('archive_unsafe_type', 'archive type is unsafe');
  }
  if (!observed.has('release-manifest.json') || [...declared.keys()].some((name) => !observed.has(name))) {
    fail('archive_invalid', 'archive output is incomplete');
  }
  return true;
}

function verifyPlatformArtifact(artifactBytes, manifestBytes, channelRelease, options = {}) {
  const bytes = Buffer.isBuffer(artifactBytes) ? artifactBytes : Buffer.from(artifactBytes);
  const manifestBuffer = Buffer.isBuffer(manifestBytes) ? manifestBytes : Buffer.from(manifestBytes);
  const manifest = validateArtifactManifest(parseCanonicalJson(manifestBuffer, 4 * 1024 * 1024));
  if (bytes.length !== channelRelease.artifact_bytes || sha256(bytes) !== channelRelease.artifact_sha256) {
    fail('artifact_identity_mismatch', 'artifact size or hash differs');
  }
  if (sha256(manifestBuffer) !== channelRelease.manifest_sha256
      || manifest.version !== channelRelease.version || manifest.build_id !== channelRelease.build_id
      || manifest.platform !== channelRelease.platform) fail('artifact_identity_mismatch', 'manifest identity differs');
  const protocol = options.launcherProtocol ?? LAUNCHER_PROTOCOL;
  const platform = options.platform ?? SUPPORTED_PLATFORM;
  if (channelRelease.minimum_launcher_protocol > protocol
      || manifest.launcher_protocol.minimum > protocol || manifest.launcher_protocol.maximum < protocol) {
    fail('launcher_protocol_incompatible', 'launcher protocol is incompatible');
  }
  if (manifest.platform !== platform) fail('platform_incompatible', 'artifact platform differs');
  return manifest;
}

function signCanonicalFixture(document, privateKeyPem) {
  const bytes = Buffer.from(canonicalJson(document), 'utf8');
  const key = crypto.createPrivateKey(privateKeyPem);
  if (key.asymmetricKeyType !== 'ed25519') fail('fixture_key_invalid', 'fixture key must be Ed25519');
  return { bytes, signature: crypto.sign(null, bytes, key).toString('base64') + '\n' };
}

function assertAbsoluteRoot(root) {
  if (typeof root !== 'string' || !path.isAbsolute(root) || path.normalize(root) !== root) fail('install_root_invalid', 'root is not canonical');
}

function lstatExists(filename) {
  try { fs.lstatSync(filename); return true; } catch (error) {
    if (error.code === 'ENOENT') return false;
    fail('file_custody_invalid', 'path custody is unavailable');
  }
}

function noSymlinkComponents(root, missingOkay = false) {
  assertAbsoluteRoot(root);
  let current = path.parse(root).root;
  for (const part of root.slice(current.length).split(path.sep).filter(Boolean)) {
    current = path.join(current, part);
    let metadata;
    try { metadata = fs.lstatSync(current); } catch (error) {
      if (missingOkay && error.code === 'ENOENT') return false;
      fail('install_root_invalid', 'root custody is unavailable');
    }
    if (metadata.isSymbolicLink()) fail('install_root_invalid', 'root contains a symlink');
  }
  return true;
}

function ownedDirectory(directory, uid, mode = null) {
  let metadata;
  try { metadata = fs.lstatSync(directory); } catch { fail('install_root_invalid', 'directory is unavailable'); }
  if (!metadata.isDirectory() || metadata.uid !== uid || (mode !== null && (metadata.mode & 0o777) !== mode)) {
    fail('install_root_invalid', 'directory custody is invalid');
  }
  return metadata;
}

function readOwnedRegular(filename, uid, modes = null, maximumBytes = 4 * 1024 * 1024) {
  let descriptor;
  try {
    descriptor = fs.openSync(filename, fs.constants.O_RDONLY | (fs.constants.O_NOFOLLOW || 0));
    const before = fs.fstatSync(descriptor);
    const lexical = fs.lstatSync(filename);
    if (!before.isFile() || !lexical.isFile() || before.uid !== uid || lexical.uid !== uid
        || before.ino !== lexical.ino || before.dev !== lexical.dev || before.nlink !== 1
        || (modes && !modes.includes(before.mode & 0o777)) || before.size > maximumBytes) {
      fail('file_custody_invalid', 'file custody is invalid');
    }
    const bytes = fs.readFileSync(descriptor);
    const after = fs.fstatSync(descriptor);
    if (after.ino !== before.ino || after.dev !== before.dev || after.size !== before.size || bytes.length !== before.size) {
      fail('file_custody_invalid', 'file changed while reading');
    }
    return bytes;
  } catch (error) {
    if (error instanceof LauncherError) throw error;
    fail('file_custody_invalid', 'file custody is unavailable');
  } finally {
    if (descriptor !== undefined) fs.closeSync(descriptor);
  }
}

function safePointer(root, name, idPattern, uid, releaseMode = 0o700) {
  const pointer = path.join(root, name);
  let metadata;
  try { metadata = fs.lstatSync(pointer); } catch (error) {
    if (error.code === 'ENOENT') return null;
    fail('pointer_invalid', 'release pointer is unavailable');
  }
  if (!metadata.isSymbolicLink() || metadata.uid !== uid) fail('pointer_invalid', 'release pointer custody is invalid');
  const target = fs.readlinkSync(pointer);
  const parts = target.split('/');
  if (parts.length !== 2 || parts[0] !== 'releases' || !idPattern.test(parts[1])) fail('pointer_invalid', 'release pointer target is invalid');
  const release = path.join(root, 'releases', parts[1]);
  ownedDirectory(release, uid, releaseMode);
  if (fs.readlinkSync(pointer) !== target) fail('pointer_invalid', 'release pointer changed while reading');
  return release;
}

function legacyTreeDigest(root, uid = process.geteuid()) {
  const records = [];
  function visit(directory, prefix = '') {
    const names = fs.readdirSync(directory).sort();
    for (const name of names) {
      const relative = prefix ? `${prefix}/${name}` : name;
      if (relative === 'release.json') continue;
      const filename = path.join(directory, name);
      const metadata = fs.lstatSync(filename);
      if (metadata.uid !== uid) fail('legacy_release_unowned', 'legacy release contains an unowned path');
      const mode = (metadata.mode & 0o777).toString(8);
      if (metadata.isSymbolicLink()) {
        const target = fs.readlinkSync(filename);
        resolvedLinkPath(relative, target, 'legacy_release_invalid');
        records.push(Buffer.from(`L\0${relative}\0${mode}\0${target}`));
      } else if (metadata.isDirectory()) {
        records.push(Buffer.from(`D\0${relative}\0${mode}`));
        visit(filename, relative);
      } else if (metadata.isFile()) {
        const bytes = readOwnedRegular(filename, uid, null, 1024 * 1024 * 1024);
        records.push(Buffer.from(`F\0${relative}\0${mode}\0${bytes.length}\0${sha256(bytes)}`));
      } else {
        fail('legacy_release_invalid', 'legacy release contains an unsupported type');
      }
    }
  }
  visit(root);
  return sha256(Buffer.concat(records.flatMap((record, index) => index ? [Buffer.from('\n'), record] : [record])));
}

function legacyReleaseId(document) {
  return sha256(Buffer.from(canonicalJson({
    commit: document.build_id,
    configuration: document.configuration_fingerprint,
    configuration_locator: document.configuration_locator_sha256,
    manifest: document.operations_manifest_sha256,
    release_tree: document.release_tree_sha256,
    tree: document.source_tree,
  }))).slice(0, 24);
}

function validateLegacyRelease(releaseRoot, legacyRoot, uid) {
  if (path.dirname(releaseRoot) !== path.join(legacyRoot, 'releases') || !LEGACY_RELEASE_ID.test(path.basename(releaseRoot))) {
    fail('legacy_release_invalid', 'legacy release is outside its canonical root');
  }
  ownedDirectory(releaseRoot, uid, 0o700);
  const document = JSON.parse(readOwnedRegular(path.join(releaseRoot, 'release.json'), uid, [0o600], 64 * 1024).toString('utf8'));
  exactKeys(document, LEGACY_RELEASE_KEYS, 'legacy_release_invalid');
  if (document.schema_version !== 'voice-agent.operational-release.v2' || document.release_id !== path.basename(releaseRoot)
      || !BUILD_ID.test(document.build_id) || !BUILD_ID.test(document.source_tree)
      || document.operations_schema !== 'voice-agent.operations.v1' || !SHA256.test(document.operations_manifest_sha256)
      || !SHA256.test(document.configuration_locator_sha256) || !SHA256.test(document.configuration_fingerprint)
      || !SHA256.test(document.release_tree_sha256) || document.provider_mode !== 'local'
      || document.external_provider_supervised !== false || document.automatic_fallback !== false
      || legacyReleaseId(document) !== document.release_id) fail('legacy_release_invalid', 'legacy release identity is invalid');
  const operations = readOwnedRegular(path.join(releaseRoot, 'config', 'operations-v1.json'), uid, null, 1024 * 1024);
  if (sha256(operations) !== document.operations_manifest_sha256 || legacyTreeDigest(releaseRoot, uid) !== document.release_tree_sha256) {
    fail('legacy_release_invalid', 'legacy immutable inventory differs');
  }
  return {
    release_id: document.release_id,
    build_id: document.build_id,
    state: 'legacy_unsupported',
    application_manifest: 'unsupported',
    immutable_inventory: 'verified',
    root: releaseRoot,
  };
}

function normalizedUnit(bytes) {
  const result = {};
  let section = null;
  for (const raw of bytes.toString('utf8').split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith('#') || line.startsWith(';')) continue;
    if (line.startsWith('[') && line.endsWith(']')) { section = line.slice(1, -1); result[section] ||= {}; continue; }
    if (!section || !line.includes('=')) fail('legacy_unit_invalid', 'service unit syntax is invalid');
    const index = line.indexOf('=');
    const name = line.slice(0, index);
    const value = line.slice(index + 1);
    result[section][name] ||= [];
    result[section][name].push(value);
  }
  return result;
}

function validateLegacyRunning(release, snapshot, options) {
  const uid = options.expectedUid;
  const unitPath = options.serviceUnitPath;
  const serviceUnitOwner = options.serviceUnitOwner;
  const expectedPythonPath = options.expectedPythonPath;
  if (!snapshot || !snapshot.service || !snapshot.process || !snapshot.runtime) fail('legacy_runtime_unavailable', 'runtime evidence is incomplete');
  const service = snapshot.service;
  const process = snapshot.process;
  const runtime = snapshot.runtime;
  if (service.name !== SERVICE_NAME || service.fragment_path !== unitPath || service.active !== 'active'
      || !Number.isSafeInteger(service.main_pid) || service.main_pid <= 0 || service.main_pid !== process.pid
      || process.uid !== uid || process.cwd !== release.root || runtime.release_id !== release.release_id
      || runtime.build_id !== release.build_id) fail('legacy_runtime_mismatch', 'runtime identity differs from release custody');
  const releaseUnit = readOwnedRegular(path.join(release.root, 'ops', 'systemd', SERVICE_NAME), uid, null, 256 * 1024);
  const installedUnit = readOwnedRegular(unitPath, serviceUnitOwner, null, 256 * 1024);
  normalizedUnit(releaseUnit);
  if (sha256(releaseUnit) !== sha256(installedUnit)) fail('legacy_unit_mismatch', 'installed unit differs from running release');
  const expectedScript = path.join(release.root, 'scripts', 'run_slice6.py');
  if (!Array.isArray(process.argv) || process.argv.length !== 3 || process.argv[0] !== expectedPythonPath
      || process.argv[1] !== '-B' || process.argv[2] !== expectedScript
      || fs.realpathSync(expectedPythonPath) !== process.executable) fail('legacy_process_mismatch', 'process argv differs from release');
  const executable = fs.lstatSync(process.executable);
  const script = fs.lstatSync(expectedScript);
  if (!executable.isFile() || !script.isFile() || ![0, uid].includes(executable.uid) || script.uid !== uid) {
    fail('legacy_process_mismatch', 'process executable custody is invalid');
  }
  const health = runtime.health;
  const requiredComponents = new Set(['livekit', 'controller', 'stt', 'selected_llm', 'tts']);
  const ready = Boolean(runtime.accepting === true && health && health.overall_readiness === 'ready'
    && Array.isArray(health.components) && health.components.length === 5
    && new Set(health.components.map((item) => item.component)).size === 5
    && health.components.every((item) => requiredComponents.has(item.component)
      && item.liveness === 'alive' && item.readiness === 'ready' && item.compatible === true));
  if (!ready) fail('legacy_runtime_unready', 'running legacy release is not exactly ready');
  return true;
}

function dockerEvidence(snapshot, uid) {
  const docker = snapshot && snapshot.docker;
  const expected = `unix:///run/user/${uid}/docker.sock`;
  if (!docker) return { state: 'unavailable', endpoint_kind: 'none', ownership_verified: false };
  if (docker.endpoint !== expected || docker.socket_uid !== uid || docker.socket_type !== 'socket' || docker.rootless !== true) {
    return { state: 'invalid', endpoint_kind: 'rootless', ownership_verified: false };
  }
  return { state: 'available', endpoint_kind: 'rootless', ownership_verified: true };
}

async function discoverLegacy(options) {
  const legacyRoot = options.legacyRoot;
  const uid = options.expectedUid ?? process.geteuid();
  const serviceUnitPath = options.serviceUnitPath ?? path.join('/etc', 'systemd', 'system', SERVICE_NAME);
  const serviceUnitOwner = options.serviceUnitOwner ?? 0;
  const expectedPythonPath = options.expectedPythonPath ?? path.join(options.home ?? os.homedir(), '.cache', 'voice-agent-v2', 'slice-6', 'runtime', 'venv', 'bin', 'python');
  const absent = {
    schema_version: 'voice-agent.legacy-discovery.v1', state: 'absent', selected: null, running: null,
    rollback: { state: 'missing', release_id: null }, service_custody: 'unavailable', runtime_custody: 'unavailable',
    docker: { state: 'unavailable', endpoint_kind: 'none', ownership_verified: false }, adoption_eligible: false,
  };
  if (!lstatExists(legacyRoot)) return absent;
  try {
    noSymlinkComponents(legacyRoot);
    ownedDirectory(legacyRoot, uid, 0o700);
    ownedDirectory(path.join(legacyRoot, 'releases'), uid, 0o700);
    const selectedRoot = safePointer(legacyRoot, 'current', LEGACY_RELEASE_ID, uid);
    const rollbackRoot = safePointer(legacyRoot, 'previous', LEGACY_RELEASE_ID, uid);
    const selected = selectedRoot ? validateLegacyRelease(selectedRoot, legacyRoot, uid) : null;
    const rollbackRelease = rollbackRoot ? validateLegacyRelease(rollbackRoot, legacyRoot, uid) : null;
    const snapshot = await options.serviceProbe.inspectLegacy({ serviceName: SERVICE_NAME, uid });
    const runtimeId = snapshot && snapshot.runtime && snapshot.runtime.release_id;
    let running = null;
    let serviceCustody = 'unavailable';
    let runtimeCustody = 'unavailable';
    if (typeof runtimeId === 'string' && LEGACY_RELEASE_ID.test(runtimeId)) {
      const runningRoot = path.join(legacyRoot, 'releases', runtimeId);
      running = validateLegacyRelease(runningRoot, legacyRoot, uid);
      validateLegacyRunning(running, snapshot, { expectedUid: uid, serviceUnitPath, serviceUnitOwner, expectedPythonPath });
      serviceCustody = 'verified';
      runtimeCustody = 'verified_ready';
    }
    const output = {
      schema_version: 'voice-agent.legacy-discovery.v1',
      state: selected || running ? 'legacy_unsupported' : 'invalid',
      selected: selected ? { state: selected.state, release_id: selected.release_id, build_id: selected.build_id } : null,
      running: running ? { state: running.state, release_id: running.release_id, build_id: running.build_id, ready: true } : null,
      rollback: rollbackRelease
        ? { state: 'verified', release_id: rollbackRelease.release_id }
        : { state: 'missing', release_id: null },
      service_custody: serviceCustody,
      runtime_custody: runtimeCustody,
      docker: dockerEvidence(snapshot, uid),
      adoption_eligible: Boolean(running && serviceCustody === 'verified' && runtimeCustody === 'verified_ready'),
    };
    return output;
  } catch (error) {
    return { ...absent, state: 'invalid', error_code: error instanceof LauncherError ? error.code : 'legacy_discovery_failed' };
  }
}

function validateReleaseRecord(record) {
  const keys = [
    'application_protocol', 'artifact_bytes', 'artifact_sha256', 'build_id', 'channel', 'channel_sequence',
    'data_schema', 'launcher_protocol', 'manifest_sha256', 'platform', 'readiness', 'release_id', 'schema',
    'service_template_sha256', 'verified_at', 'version',
  ];
  exactKeys(record, keys, 'release_record_invalid');
  if (record.schema !== 'voice-agent.release-record.v1' || !RELEASE_ID.test(record.release_id)
      || !VERSION.test(record.version) || !BUILD_ID.test(record.build_id) || record.channel !== 'stable'
      || !Number.isSafeInteger(record.channel_sequence) || record.channel_sequence < 1
      || record.platform !== SUPPORTED_PLATFORM || !SHA256.test(record.artifact_sha256)
      || !SHA256.test(record.manifest_sha256) || !SHA256.test(record.service_template_sha256)
      || !Number.isSafeInteger(record.artifact_bytes) || record.artifact_bytes < 1
      || record.launcher_protocol !== LAUNCHER_PROTOCOL) fail('release_record_invalid', 'release record identity is invalid');
  validateRange(record.application_protocol, 'release_record_invalid');
  validateRange(record.data_schema, 'release_record_invalid');
  parseTime(record.verified_at, 'release_record_invalid');
  exactKeys(record.readiness, ['checked_at', 'state'], 'release_record_invalid');
  if (!['ready', 'not_verified'].includes(record.readiness.state)) fail('release_record_invalid', 'readiness receipt is invalid');
  if (record.readiness.checked_at !== null) parseTime(record.readiness.checked_at, 'release_record_invalid');
  return record;
}

function readCanonicalRelease(root, pointer, uid) {
  const releaseRoot = safePointer(root, pointer, RELEASE_ID, uid, 0o500);
  if (!releaseRoot) return null;
  const record = validateReleaseRecord(JSON.parse(readOwnedRegular(path.join(releaseRoot, 'release-record.json'), uid, [0o400, 0o600], 256 * 1024).toString('utf8')));
  if (record.release_id !== path.basename(releaseRoot)) fail('release_record_invalid', 'release record directory differs');
  return record;
}

function canonicalState(installRoot, uid) {
  if (!lstatExists(installRoot)) return { state: 'absent', selected: null, rollback: null, transaction: { state: 'none', phase: null }, agent_environment: { state: 'unconfigured', reason_code: 'not_configured' } };
  try {
    noSymlinkComponents(installRoot);
    ownedDirectory(installRoot, uid, 0o700);
    ownedDirectory(path.join(installRoot, 'releases'), uid, 0o700);
    const selected = readCanonicalRelease(installRoot, 'current', uid);
    const rollback = readCanonicalRelease(installRoot, 'rollback', uid);
    let transaction = { state: 'none', phase: null };
    const transactionPath = path.join(installRoot, 'transactions', 'update.json');
    if (lstatExists(transactionPath)) {
      const document = JSON.parse(readOwnedRegular(transactionPath, uid, [0o600], 256 * 1024).toString('utf8'));
      if (document.schema !== 'voice-agent.update-transaction.v1' || typeof document.phase !== 'string') {
        fail('transaction_invalid', 'transaction record is invalid');
      }
      transaction = { state: 'present', phase: document.phase };
    }
    let agent_environment = { state: 'unconfigured', reason_code: 'not_configured' };
    const registry = path.join(installRoot, 'agent-environment', 'private', 'registry.json');
    if (lstatExists(registry)) {
      const document = JSON.parse(readOwnedRegular(registry, uid, [0o600], 256 * 1024).toString('utf8'));
      const allowed = new Set(['absent', 'running', 'stopped', 'stale_spec', 'unhealthy', 'unavailable']);
      agent_environment = allowed.has(document.state)
        ? { state: document.state, reason_code: typeof document.reason_code === 'string' ? document.reason_code : null }
        : { state: 'unknown', reason_code: 'registry_incompatible' };
    }
    return { state: selected ? 'canonical' : 'incomplete', selected, rollback, transaction, agent_environment };
  } catch (error) {
    return { state: 'invalid', selected: null, rollback: null, transaction: { state: 'invalid', phase: null }, agent_environment: { state: 'unknown', reason_code: 'install_state_invalid' }, error_code: error instanceof LauncherError ? error.code : 'install_state_invalid' };
  }
}

class SystemServiceProbe {
  async inspectCanonical({ uid }) {
    const result = spawnSync('systemctl', ['--user', 'show', 'voice-agent.service', '--property=LoadState,ActiveState,SubState,MainPID,ControlGroup'], {
      encoding: 'utf8', timeout: 5000, env: { PATH: '/usr/bin:/bin', LANG: 'C.UTF-8', LC_ALL: 'C.UTF-8' }, maxBuffer: 65536,
    });
    const values = {};
    if (result.status === 0) for (const line of result.stdout.split('\n')) if (line.includes('=')) values[line.slice(0, line.indexOf('='))] = line.slice(line.indexOf('=') + 1);
    const enabled = spawnSync('systemctl', ['--user', 'is-enabled', 'voice-agent.service'], { encoding: 'utf8', timeout: 3000, env: { PATH: '/usr/bin:/bin' } });
    const servicePids = new Set();
    function collectPids(directory) {
      try {
        for (const line of fs.readFileSync(path.join(directory, 'cgroup.procs'), 'utf8').split('\n')) if (/^[1-9][0-9]*$/.test(line)) servicePids.add(Number(line));
        for (const name of fs.readdirSync(directory)) { const child = path.join(directory, name); if (fs.lstatSync(child).isDirectory()) collectPids(child); }
      } catch {}
    }
    if (typeof values.ControlGroup === 'string' && values.ControlGroup.startsWith('/')) collectPids(path.join('/sys/fs/cgroup', values.ControlGroup));
    const socket = spawnSync('ss', ['-H', '-ltnp', 'sport = :8000'], { encoding: 'utf8', timeout: 3000, env: { PATH: '/usr/bin:/bin', LANG: 'C.UTF-8', LC_ALL: 'C.UTF-8' } });
    const loopback = socket.status === 0 && socket.stdout.split('\n').find((line) => /127\.0\.0\.1:8000\b/.test(line));
    const listenerPid = loopback && /pid=([1-9][0-9]*)/.exec(loopback);
    const listenerOwned = Boolean(listenerPid && servicePids.has(Number(listenerPid[1])));
    return {
      service_active: values.ActiveState === 'active', service_enabled: enabled.status === 0,
      process_uid: Number(values.MainPID || 0) > 0 ? (() => { try { return fs.lstatSync(`/proc/${values.MainPID}`).uid; } catch { return null; } })() : null,
      runtime: await this.#runtime(),
      listener: { host: loopback ? '127.0.0.1' : null, port: loopback ? 8000 : null, owner_uid: listenerOwned ? uid : null, owner: listenerOwned ? 'service' : 'unknown' },
    };
  }

  async inspectLegacy({ uid }) {
    const result = spawnSync('systemctl', ['show', SERVICE_NAME, '--property=FragmentPath,LoadState,ActiveState,SubState,MainPID'], {
      encoding: 'utf8', timeout: 5000, env: { PATH: '/usr/bin:/bin', LANG: 'C.UTF-8', LC_ALL: 'C.UTF-8' }, maxBuffer: 65536,
    });
    const values = {};
    if (result.status === 0) for (const line of result.stdout.split('\n')) if (line.includes('=')) values[line.slice(0, line.indexOf('='))] = line.slice(line.indexOf('=') + 1);
    const pid = Number(values.MainPID || 0);
    let process = null;
    if (pid > 0) {
      try {
        process = {
          pid, uid: fs.lstatSync(`/proc/${pid}`).uid, cwd: fs.readlinkSync(`/proc/${pid}/cwd`),
          executable: fs.readlinkSync(`/proc/${pid}/exe`),
          argv: fs.readFileSync(`/proc/${pid}/cmdline`).toString('utf8').split('\0').filter(Boolean),
        };
      } catch { process = null; }
    }
    const runtime = await this.#runtime();
    const socketPath = `/run/user/${uid}/docker.sock`;
    let docker = null;
    try {
      const metadata = fs.lstatSync(socketPath);
      const info = spawnSync('docker', ['--host', `unix://${socketPath}`, 'info', '--format', '{{json .SecurityOptions}}'], {
        encoding: 'utf8', timeout: 3000, env: { PATH: '/usr/bin:/bin', HOME: '/nonexistent', DOCKER_CONFIG: '/nonexistent' }, maxBuffer: 65536,
      });
      docker = { endpoint: `unix://${socketPath}`, socket_uid: metadata.uid, socket_type: metadata.isSocket() ? 'socket' : 'other', rootless: info.status === 0 && info.stdout.includes('rootless') };
    } catch { docker = null; }
    return {
      service: { name: SERVICE_NAME, fragment_path: values.FragmentPath || null, load: values.LoadState || 'unknown', active: values.ActiveState || 'unknown', substate: values.SubState || 'unknown', main_pid: pid },
      process, runtime, docker,
    };
  }

  #runtime() {
    return new Promise((resolve) => {
      const request = http.get({ host: '127.0.0.1', port: 8000, path: '/api/status', timeout: 1000, headers: { Connection: 'close' } }, (response) => {
        let size = 0;
        const chunks = [];
        response.on('data', (chunk) => { size += chunk.length; if (size <= 65536) chunks.push(chunk); else request.destroy(); });
        response.on('end', () => {
          if (response.statusCode !== 200 || size > 65536) return resolve(null);
          try { const value = JSON.parse(Buffer.concat(chunks).toString('utf8')); resolve(value && typeof value === 'object' ? value : null); } catch { resolve(null); }
        });
      });
      request.on('timeout', () => request.destroy());
      request.on('error', () => resolve(null));
    });
  }
}

function canonicalSnapshotReady(snapshot, record, uid) {
  const health = snapshot && snapshot.runtime && snapshot.runtime.health;
  const components = health && health.components;
  const required = new Set(['livekit', 'controller', 'stt', 'selected_llm', 'tts']);
  return Boolean(snapshot && snapshot.service_active === true && snapshot.service_enabled === true && snapshot.process_uid === uid
    && snapshot.runtime.release_id === record.release_id && snapshot.runtime.build_id === record.build_id
    && snapshot.runtime.accepting === true && Array.isArray(components) && components.length === 5
    && new Set(components.map((item) => item.component)).size === 5
    && components.every((item) => required.has(item.component) && item.liveness === 'alive' && item.readiness === 'ready' && item.compatible === true)
    && snapshot.listener && snapshot.listener.host === '127.0.0.1' && snapshot.listener.port === 8000
    && snapshot.listener.owner_uid === uid && snapshot.listener.owner === 'service');
}

function identity(record, source, ready = false) {
  if (!record) return { state: 'absent', release_id: null, version: null, build_id: null, source, ready: false };
  return {
    state: record.state || (record.readiness && record.readiness.state === 'ready' ? 'verified' : 'not_verified'),
    release_id: record.release_id, version: record.version || null, build_id: record.build_id, source, ready,
  };
}

async function collectStatus(options = {}) {
  const uid = options.expectedUid ?? process.geteuid();
  const home = options.home ?? os.homedir();
  const installRoot = options.installRoot ?? path.join(home, '.local', 'share', 'voice-agent');
  const legacyRoot = options.legacyRoot ?? path.join(home, '.local', 'share', 'voice-agent-v2');
  const serviceUnitPath = options.serviceUnitPath ?? path.join('/etc', 'systemd', 'system', SERVICE_NAME);
  const serviceProbe = options.serviceProbe ?? new SystemServiceProbe();
  const canonical = canonicalState(installRoot, uid);
  const legacy = await discoverLegacy({
    legacyRoot, expectedUid: uid, serviceUnitPath, serviceProbe, home,
    serviceUnitOwner: options.serviceUnitOwner, expectedPythonPath: options.expectedPythonPath,
  });
  let canonicalSnapshot = null;
  let canonicalReady = false;
  if (canonical.selected && typeof serviceProbe.inspectCanonical === 'function') {
    try {
      canonicalSnapshot = await serviceProbe.inspectCanonical({ uid, release: canonical.selected });
      canonicalReady = canonicalSnapshotReady(canonicalSnapshot, canonical.selected, uid);
    } catch { canonicalSnapshot = null; }
  }
  let selected = identity(canonical.selected, 'canonical');
  let running = canonicalReady ? identity(canonical.selected, 'canonical_service', true) : identity(null, 'service');
  let rollback = canonical.rollback ? { state: 'verified', release_id: canonical.rollback.release_id, version: canonical.rollback.version, build_id: canonical.rollback.build_id } : { state: 'missing', release_id: null, version: null, build_id: null };
  let installationState = canonical.state;
  if (!canonical.selected && legacy.selected) {
    selected = identity(legacy.selected, 'legacy');
    installationState = legacy.state;
  }
  if (!canonicalReady && legacy.running) running = identity(legacy.running, 'legacy_service', true);
  if (!canonical.rollback && legacy.rollback.state === 'verified') rollback = { state: 'verified_legacy', release_id: legacy.rollback.release_id, version: null, build_id: null };
  let alignment = 'unknown';
  if (selected.release_id && running.release_id) {
    alignment = selected.release_id === running.release_id && running.ready ? 'selected-running-ready' : 'selected-new-running-old';
  } else if (running.release_id) alignment = 'running-only';
  else if (selected.release_id) alignment = 'selected-not-running';
  else alignment = 'not-installed';
  return {
    schema_version: 'voice-agent.launcher-status.v1',
    launcher: { version: LAUNCHER_VERSION, protocol: LAUNCHER_PROTOCOL },
    installation: { state: installationState, channel: canonical.selected ? 'stable' : null },
    selected,
    running,
    alignment,
    rollback,
    transaction: canonical.transaction,
    service: {
      state: canonicalReady || legacy.running ? 'active' : canonicalSnapshot ? 'inactive' : legacy.service_custody === 'unavailable' ? 'unavailable' : 'inactive',
      enabled: canonicalSnapshot ? Boolean(canonicalSnapshot.service_enabled) : null,
      main_pid_present: Boolean(canonicalReady || legacy.running),
      custody: canonicalReady ? 'verified' : legacy.service_custody,
    },
    agent_environment: canonical.agent_environment,
    docker: legacy.docker,
    legacy: { state: legacy.state, adoption_eligible: legacy.adoption_eligible, runtime_custody: legacy.runtime_custody },
    read_only: true,
  };
}

async function collectDoctor(options = {}) {
  const status = await collectStatus(options);
  const checks = [
    { code: 'install_root', state: ['invalid'].includes(status.installation.state) ? 'fail' : status.installation.state === 'absent' ? 'not_configured' : 'ok' },
    { code: 'selected_release', state: status.selected.release_id ? status.selected.state === 'legacy_unsupported' ? 'unsupported' : 'ok' : 'not_configured' },
    { code: 'running_release', state: status.running.release_id ? status.running.ready ? 'ok' : 'fail' : 'unavailable' },
    { code: 'selected_running_alignment', state: status.alignment === 'selected-running-ready' ? 'ok' : status.alignment === 'selected-new-running-old' ? 'recovery_required' : 'not_configured' },
    { code: 'rollback_custody', state: status.rollback.state.startsWith('verified') ? 'ok' : 'missing' },
    { code: 'transaction', state: status.transaction.state === 'invalid' ? 'fail' : status.transaction.state },
    { code: 'rootless_docker', state: status.docker.state },
    { code: 'agent_environment', state: status.agent_environment.state },
  ];
  return {
    schema_version: 'voice-agent.launcher-doctor.v1', launcher: status.launcher,
    overall: checks.some((check) => check.state === 'fail') ? 'invalid' : checks.some((check) => check.state === 'recovery_required') ? 'recovery_required' : 'read_only_complete',
    checks, status, repair_performed: false, read_only: true,
  };
}

function humanStatus(status) {
  return [
    `Installation: ${status.installation.state}`,
    `Selected: ${status.selected.release_id || 'none'} (${status.selected.state})`,
    `Running: ${status.running.release_id || 'none'} (${status.running.ready ? 'ready' : status.running.state})`,
    `Alignment: ${status.alignment}`,
    `Rollback: ${status.rollback.release_id || 'none'} (${status.rollback.state})`,
    `Transaction: ${status.transaction.state}${status.transaction.phase ? `/${status.transaction.phase}` : ''}`,
    `Agent tools: ${status.agent_environment.state}`,
    `Docker: ${status.docker.state} (${status.docker.endpoint_kind})`,
    'Read only: yes',
  ].join('\n');
}

function humanDoctor(doctor) {
  return [`Doctor: ${doctor.overall}`, ...doctor.checks.map((check) => `${check.code}: ${check.state}`), 'Repair performed: no'].join('\n');
}

function parseCli(argv) {
  if (argv.length < 1 || !['install', 'status', 'doctor'].includes(argv[0])) fail('usage', 'expected install, status, or doctor');
  const result = { command: argv[0], json: false };
  for (let index = 1; index < argv.length; index += 1) {
    const item = argv[index];
    if (item === '--json' && result.command !== 'install') result.json = true;
    else fail('usage', 'unknown option; production installation roots are fixed by XDG');
  }
  return result;
}

function loadInstaller() {
  if (!require('node:sea').isSea()) return require('./install.cjs')(module.exports);
  const source = require('node:sea').getAsset('install.cjs', 'utf8');
  const embedded = { exports: {} };
  Function('require', 'module', 'exports', source)(require, embedded, embedded.exports);
  return embedded.exports(module.exports);
}

async function main(argv = process.argv.slice(2)) {
  try {
    const arguments_ = parseCli(argv);
    if (arguments_.command === 'install') {
      await loadInstaller().installVoiceAgent();
      return 0;
    }
    const document = arguments_.command === 'status' ? await collectStatus() : await collectDoctor();
    process.stdout.write(arguments_.json ? `${JSON.stringify(document, null, 2)}\n` : `${arguments_.command === 'status' ? humanStatus(document) : humanDoctor(document)}\n`);
    return 0;
  } catch (error) {
    const code = error instanceof LauncherError ? error.code : 'launcher_failed';
    const actionable = argv[0] === 'install' && ['host_unsupported', 'linger_privilege_unavailable', 'release_authority_unprovisioned'].includes(code);
    process.stderr.write(actionable ? `${error.message}\n` : `${code}: command failed safely; no healthy installation was claimed\n`);
    return 2;
  }
}

module.exports = {
  LAUNCHER_PROTOCOL, LAUNCHER_VERSION, SUPPORTED_PLATFORM, LauncherError, SystemServiceProbe,
  canonicalJson, collectDoctor, collectStatus, discoverLegacy, humanDoctor, humanStatus,
  legacyReleaseId, legacyTreeDigest, loadInstaller, main, parseCanonicalJson, parseCli, signCanonicalFixture,
  validateArchiveEntries, validateArtifactManifest, validateChannel, validateReleaseRecord,
  verifyPlatformArtifact, verifySignedChannel,
};

if (require.main === module) main().then((status) => { process.exitCode = status; });
