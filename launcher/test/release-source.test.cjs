'use strict';

const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');

const core = require('../voice-agent.cjs');
const sourceModule = require('../release-source.cjs')(core);
const archive = require('../../release/archive.cjs');
const KEY = fs.readFileSync(path.join(__dirname, '..', 'keys', 'release-fixture-ed25519-public.pem'), 'utf8');
const API = 'https://api.github.com/repos/prineycom/voice-agent-v2';
const ASSET = `${API}/releases/assets/123456`;
const STORE = 'https://release-assets.githubusercontent.com/private/object';

function configuration(overrides = {}) {
  return {
    github_api_origin: 'https://api.github.com/',
    github_asset_origins: ['https://objects.githubusercontent.com/', 'https://release-assets.githubusercontent.com/'],
    github_channel_path: 'stable.json', github_ref: 'release-channel', github_repository: 'prineycom/voice-agent-v2',
    launcher_protocol: 1, launcher_version: '0.8.0', public_key_pem: KEY, source_commit: 'a'.repeat(40),
    source_timestamp: '2026-08-20T00:00:00Z', ...overrides,
  };
}
function response(url, bytes, overrides = {}) {
  return { status: 200, headers: { 'content-length': String(bytes.length) }, bytes, url, redirected: false, authorized: true, encrypted: true, ...overrides };
}
function githubContent(url, pathname, bytes) {
  const body = Buffer.from(JSON.stringify({ type: 'file', name: path.basename(pathname), path: pathname, encoding: 'base64', size: bytes.length, content: bytes.toString('base64'), url }));
  return response(url, body);
}
function redirect(url = ASSET) { return response(url, Buffer.alloc(0), { status: 302, headers: { 'content-length': '0', location: STORE } }); }
function expectCode(code, action) { return assert.rejects(action, (reason) => reason && reason.code === code); }
function source(transport, overrides = {}) { return new sourceModule.HttpsReleaseSource(configuration(overrides), { transport, tokenReader: () => 'github_pat_fixture_read_only' }); }
function entry(pathname, type, mode, bytes = null, target = null) {
  return { path: pathname, type, mode, size: bytes ? bytes.length : 0, sha256: bytes ? archive.sha256(bytes) : null, target };
}
function artifactFixture(options = {}) {
  const files = new Map([
    ['bin/voice-agent-runtime', Buffer.from('#!/bin/sh\nexit 0\n')], ['descriptors/local-models.json', Buffer.from('{}')], ['descriptors/runtime.json', Buffer.from('{}')],
  ]);
  const entries = [entry('bin', 'directory', '0555'), entry('bin/voice-agent-runtime', 'file', '0555', files.get('bin/voice-agent-runtime')),
    entry('descriptors', 'directory', '0555'), entry('descriptors/local-models.json', 'file', '0444', files.get('descriptors/local-models.json')),
    entry('descriptors/runtime.json', 'file', '0444', files.get('descriptors/runtime.json'))];
  const manifest = { application_protocol: { maximum: 1, minimum: 1 }, build_id: 'b'.repeat(40), config_schema: { maximum: 2, minimum: 2 },
    data_schema: { maximum: 2, minimum: 2 }, entries, launcher_protocol: { maximum: 1, minimum: 1 }, platform: core.SUPPORTED_PLATFORM,
    schema: 'voice-agent.platform-artifact-manifest.v1', service_template_sha256: 'c'.repeat(64), version: '1.0.0' };
  const manifestBytes = Buffer.from(core.canonicalJson(manifest));
  const tarEntries = [{ path: 'release-manifest.json', type: 'file', mode: '0444', bytes: manifestBytes, target: null },
    ...entries.map((value) => ({ ...value, bytes: value.type === 'file' ? files.get(value.path) : Buffer.alloc(0) }))];
  if (options.mutateTar) options.mutateTar(tarEntries);
  const artifactBytes = options.raw ? archive.createTar(tarEntries, 1787184000) : archive.createTarZstd(tarEntries, 1787184000);
  return { artifactBytes, manifestBytes, release: { artifact_bytes: artifactBytes.length, artifact_sha256: archive.sha256(artifactBytes), artifact_url: ASSET } };
}

test('production source is inert without one valid compile-time root and fixed private GitHub identity', async () => {
  for (const value of [null, configuration({ public_key_pem: '' }), configuration({ public_key_pem: `${KEY}${KEY}` }),
    configuration({ github_repository: 'unsafe' }), configuration({ github_ref: '../main' }), configuration({ github_api_origin: 'https://example.invalid/' })]) {
    const observed = sourceModule.createProductionSource(value);
    await expectCode('release_authority_unprovisioned', () => observed.acquireChannel());
  }
});

test('private GitHub channel uses only the pinned key and exact API repository/ref/path', async () => {
  const other = crypto.generateKeyPairSync('ed25519').publicKey.export({ type: 'spki', format: 'pem' }).toString();
  const channel = Buffer.from('{"channel":"fixture"}'); const signature = Buffer.from(`${'A'.repeat(88)}\n`); const requests = [];
  const transport = async (request) => {
    requests.push(request);
    const pathname = request.url.includes('stable.json.sig') ? 'stable.json.sig' : 'stable.json';
    return githubContent(request.url, pathname, pathname.endsWith('.sig') ? signature : channel);
  };
  const observedSource = source(transport); const observed = await observedSource.acquireChannel({ publicKeyPem: other, cachedKey: other });
  assert.equal(observed.publicKeyPem, KEY); assert.notEqual(observed.publicKeyPem, other);
  assert.deepEqual(requests.map((item) => item.url), [`${API}/contents/stable.json?ref=release-channel`, `${API}/contents/stable.json.sig?ref=release-channel`]);
  assert.equal(requests.every((item) => item.headers.Authorization === 'Bearer github_pat_fixture_read_only'), true);
  assert.equal(core.releaseAuthorityKey(observedSource, { publicKeyPem: other }, false), KEY);
  assert.equal(core.releaseAuthorityKey(observedSource, { publicKeyPem: other }, true), other);
});

test('token file custody requires current-user mode 0600 and never changes request identity', async (context) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'voice-agent-token-test-')); context.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const tokenPath = path.join(root, 'token'); fs.writeFileSync(tokenPath, 'github_pat_fixture_read_only\n', { mode: 0o600 }); fs.chmodSync(tokenPath, 0o600);
  const requests = []; const transport = async (request) => { requests.push(request); const pathname = request.url.includes('.sig') ? 'stable.json.sig' : 'stable.json'; return githubContent(request.url, pathname, Buffer.from(pathname)); };
  const observed = new sourceModule.HttpsReleaseSource(configuration(), { transport, tokenPath }); await observed.acquireChannel();
  assert.equal(requests.every((item) => item.headers.Authorization === 'Bearer github_pat_fixture_read_only'), true);
  fs.chmodSync(tokenPath, 0o644); await expectCode('github_token_unavailable', () => observed.acquireChannel());
});

test('missing token is actionable and invalid API/TLS/length responses remain content-free', async () => {
  const unavailable = new sourceModule.HttpsReleaseSource(configuration(), { transport: async () => { throw new Error('must not run'); }, tokenReader: () => { throw new core.LauncherError('github_token_unavailable', 'actionable'); } });
  await expectCode('github_token_unavailable', () => unavailable.acquireChannel());
  const url = `${API}/contents/stable.json?ref=release-channel`; const bytes = Buffer.from('{}');
  for (const [code, transport] of [
    ['release_response_invalid', async () => response(url, bytes, { authorized: false })],
    ['release_size_invalid', async () => response(url, bytes, { headers: { 'content-length': '99' } })],
    ['release_network_unavailable', async () => { throw new Error('secret server body'); }],
  ]) await expectCode(code, () => source(transport).fetch(url, { maximumBytes: 10, headers: { Accept: 'x' }, allowSearch: true }));
});

test('private GitHub artifact admits exactly one API-identified redirect and strips authorization', async () => {
  const fixture = artifactFixture(); const requests = [];
  const transport = async (request) => {
    requests.push(request);
    return request.url === ASSET ? redirect() : response(STORE, fixture.artifactBytes);
  };
  const acquired = await source(transport).acquireArtifact(fixture.release);
  assert.equal(acquired.manifestBytes.equals(fixture.manifestBytes), true);
  assert.equal(requests[0].headers.Authorization.startsWith('Bearer '), true);
  assert.equal(Object.hasOwn(requests[1].headers, 'Authorization'), false);
  await expectCode('release_redirect_refused', () => source(async (request) => request.url === ASSET ? response(ASSET, Buffer.alloc(0), { status: 302, headers: { 'content-length': '0', location: 'https://evil.example.invalid/object' } }) : response(request.url, fixture.artifactBytes)).acquireArtifact(fixture.release));
});

test('archive index refuses traversal, duplicate, PAX/device types, bad checksum, and unsafe metadata', () => {
  const base = artifactFixture({ raw: true }).artifactBytes;
  const mutate = (offset, value) => { const bytes = Buffer.from(base); bytes[offset] = value; return bytes; };
  assert.throws(() => sourceModule.indexArchive(mutate(156, 'x'.charCodeAt(0))));
  assert.throws(() => sourceModule.indexArchive(mutate(156, '3'.charCodeAt(0))));
  const checksum = Buffer.from(base); checksum[149] = checksum[149] === 48 ? 49 : 48; assert.throws(() => sourceModule.indexArchive(checksum), (reason) => reason.code === 'archive_checksum_invalid');
  const owner = Buffer.from(base); owner[108] = '1'.charCodeAt(0); assert.throws(() => sourceModule.indexArchive(owner));
  const duplicate = artifactFixture({ raw: true, mutateTar(entries) { entries.push({ ...entries[1] }); } }).artifactBytes;
  assert.throws(() => sourceModule.indexArchive(duplicate), (reason) => ['archive_duplicate_entry', 'archive_invalid'].includes(reason.code));
  const traversal = artifactFixture({ raw: true, mutateTar(entries) { entries[1] = { ...entries[1], path: '../escape' }; } }).artifactBytes;
  assert.throws(() => sourceModule.indexArchive(traversal), (reason) => reason.code === 'archive_path_invalid');
});

test('private GitHub ranges preserve exact validators after stripped-auth redirect', async () => {
  const bytes = Buffer.from('abcdef'); const descriptor = { size: bytes.length, sha256: archive.sha256(bytes), url: ASSET }; const requests = [];
  const transport = async (request) => {
    requests.push(request);
    return request.url === ASSET ? redirect() : response(STORE, bytes.subarray(2), { status: 206, headers: { 'content-length': '4', etag: '"immutable"', 'accept-ranges': 'bytes', 'content-range': 'bytes 2-5/6' } });
  };
  const observed = await source(transport).downloadAsset({ descriptor, url: descriptor.url, offset: 2, validator: '"immutable"' });
  assert.equal(observed.content_range, 'bytes 2-5/6'); assert.equal(requests[1].headers.Range, 'bytes=2-5'); assert.equal(Object.hasOwn(requests[1].headers, 'Authorization'), false);
});
