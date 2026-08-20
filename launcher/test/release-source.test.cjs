'use strict';

const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const core = require('../voice-agent.cjs');
const sourceModule = require('../release-source.cjs')(core);
const archive = require('../../release/archive.cjs');
const KEY = fs.readFileSync(path.join(__dirname, '..', 'keys', 'release-fixture-ed25519-public.pem'), 'utf8');

function configuration(overrides = {}) {
  return {
    channel_url: 'https://releases.example.invalid/voice-agent/stable.json', launcher_protocol: 1,
    launcher_version: '0.8.0', public_key_pem: KEY, source_commit: 'a'.repeat(40),
    source_timestamp: '2026-08-20T00:00:00Z', ...overrides,
  };
}
function response(url, bytes, overrides = {}) {
  return { status: 200, headers: { 'content-length': String(bytes.length) }, bytes, url, redirected: false, authorized: true, encrypted: true, ...overrides };
}
function expectCode(code, action) { return assert.rejects(action, (reason) => reason && reason.code === code); }
function entry(pathname, type, mode, bytes = null, target = null) {
  return { path: pathname, type, mode, size: bytes ? bytes.length : 0, sha256: bytes ? archive.sha256(bytes) : null, target };
}
function artifactFixture(options = {}) {
  const files = new Map([
    ['bin/voice-agent-runtime', Buffer.from('#!/bin/sh\nexit 0\n')],
    ['descriptors/local-models.json', Buffer.from('{}')],
    ['descriptors/runtime.json', Buffer.from('{}')],
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
  return { artifactBytes, manifestBytes, release: { artifact_bytes: artifactBytes.length, artifact_sha256: archive.sha256(artifactBytes), artifact_url: 'https://releases.example.invalid/voice-agent/1.0.0.tar.zst' } };
}

test('production source is inert without one valid compile-time trust root and canonical stable URL', async () => {
  for (const value of [null, configuration({ public_key_pem: '' }), configuration({ public_key_pem: `${KEY}${KEY}` }),
    configuration({ channel_url: 'http://releases.example.invalid/voice-agent/stable.json' }),
    configuration({ channel_url: 'https://127.0.0.1/stable.json' }), configuration({ channel_url: 'https://releases.example.invalid:444/stable.json' })]) {
    const source = sourceModule.createProductionSource(value);
    await expectCode('release_authority_unprovisioned', () => source.acquireChannel());
  }
});

test('channel authority returns only the pinned key and ignores network, cached, environment, and CLI key material', async () => {
  const other = crypto.generateKeyPairSync('ed25519').publicKey.export({ type: 'spki', format: 'pem' }).toString();
  const channel = Buffer.from('{"channel":"fixture"}');
  const signature = Buffer.from(`${'A'.repeat(88)}\n`);
  const old = process.env.VOICE_AGENT_RELEASE_PUBLIC_KEY;
  process.env.VOICE_AGENT_RELEASE_PUBLIC_KEY = other;
  const transport = async ({ url }) => response(url, url.endsWith('.sig') ? signature : channel, { headers: { 'content-length': String(url.endsWith('.sig') ? signature.length : channel.length), 'x-release-public-key': other } });
  try {
    const observed = await new sourceModule.HttpsReleaseSource(configuration(), { transport }).acquireChannel({ publicKeyPem: other, cachedKey: other });
    assert.equal(observed.publicKeyPem, KEY);
    assert.notEqual(observed.publicKeyPem, other);
    const source = new sourceModule.HttpsReleaseSource(configuration(), { transport });
    assert.equal(core.releaseAuthorityKey(source, { publicKeyPem: other }, false), KEY);
    assert.equal(core.releaseAuthorityKey(source, { publicKeyPem: other }, true), other);
    assert.throws(() => core.releaseAuthorityKey({ acquireChannel() {} }, { publicKeyPem: other }, false), (reason) => reason.code === 'release_authority_unprovisioned');
  } finally {
    if (old === undefined) delete process.env.VOICE_AGENT_RELEASE_PUBLIC_KEY; else process.env.VOICE_AGENT_RELEASE_PUBLIC_KEY = old;
  }
});

test('HTTPS response validation refuses redirect, TLS, timeout, transfer encoding, and byte-limit ambiguity content-free', async () => {
  const url = configuration().channel_url;
  const bytes = Buffer.from('{}');
  const cases = [
    ['release_redirect_refused', async () => response(url, bytes, { status: 302 })],
    ['release_response_invalid', async () => response(url, bytes, { authorized: false })],
    ['release_size_invalid', async () => response(url, bytes, { headers: { 'content-length': '99' } })],
    ['release_size_invalid', async () => response(url, bytes, { headers: { 'content-length': '2', 'transfer-encoding': 'chunked' } })],
    ['release_network_unavailable', async () => { throw new Error('secret server body'); }],
  ];
  for (const [code, transport] of cases) {
    const source = new sourceModule.HttpsReleaseSource(configuration(), { transport });
    await expectCode(code, () => source.fetch(url, { maximumBytes: 10 }));
  }
});

test('artifact acquisition enforces exact zstd bytes and indexes only the complete closed manifest', async () => {
  const fixture = artifactFixture();
  const transport = async ({ url }) => response(url, fixture.artifactBytes);
  const source = new sourceModule.HttpsReleaseSource(configuration(), { transport });
  const acquired = await source.acquireArtifact(fixture.release);
  assert.equal(acquired.manifestBytes.equals(fixture.manifestBytes), true);
  assert.equal((await acquired.readEntry('bin/voice-agent-runtime')).toString(), '#!/bin/sh\nexit 0\n');
  const raw = artifactFixture({ raw: true });
  const rawSource = new sourceModule.HttpsReleaseSource(configuration(), { transport: async ({ url }) => response(url, raw.artifactBytes) });
  await expectCode('artifact_identity_mismatch', () => rawSource.acquireArtifact(raw.release));
});

test('archive index refuses traversal, duplicate, PAX/device types, bad checksum, unsafe metadata, and expansion ambiguity', () => {
  const base = artifactFixture({ raw: true }).artifactBytes;
  const mutate = (offset, value) => { const bytes = Buffer.from(base); bytes[offset] = value; return bytes; };
  assert.throws(() => sourceModule.indexArchive(mutate(156, 'x'.charCodeAt(0))), (reason) => reason.code === 'archive_metadata_invalid' || reason.code === 'archive_checksum_invalid');
  assert.throws(() => sourceModule.indexArchive(mutate(156, '3'.charCodeAt(0))));
  const checksum = Buffer.from(base); checksum[149] = checksum[149] === 48 ? 49 : 48; assert.throws(() => sourceModule.indexArchive(checksum), (reason) => reason.code === 'archive_checksum_invalid');
  const owner = Buffer.from(base); owner[108] = '1'.charCodeAt(0); assert.throws(() => sourceModule.indexArchive(owner));
  const duplicate = artifactFixture({ raw: true, mutateTar(entries) { entries.push({ ...entries[1] }); } }).artifactBytes;
  assert.throws(() => sourceModule.indexArchive(duplicate), (reason) => ['archive_duplicate_entry', 'archive_invalid'].includes(reason.code));
  const traversal = artifactFixture({ raw: true, mutateTar(entries) { entries[1] = { ...entries[1], path: '../escape' }; } }).artifactBytes;
  assert.throws(() => sourceModule.indexArchive(traversal), (reason) => reason.code === 'archive_path_invalid');
});

test('asset responses require exact strong validator, byte range, content length, and no redirect', async () => {
  const bytes = Buffer.from('abcdef'); const sha256 = archive.sha256(bytes);
  const descriptor = { size: bytes.length, sha256, url: 'https://assets.example.invalid/release/runtime.bin' };
  const make = (overrides = {}) => new sourceModule.HttpsReleaseSource(configuration(), { transport: async ({ url }) => response(url, bytes.subarray(2), {
    status: 206, headers: { 'content-length': '4', etag: '"immutable"', 'accept-ranges': 'bytes', 'content-range': 'bytes 2-5/6' }, ...overrides,
  }) });
  const observed = await make().downloadAsset({ descriptor, url: descriptor.url, offset: 2, validator: '"immutable"' });
  assert.equal(observed.content_range, 'bytes 2-5/6');
  await expectCode('asset_validator_invalid', () => make({ headers: { 'content-length': '4', etag: 'W/"weak"', 'accept-ranges': 'bytes', 'content-range': 'bytes 2-5/6' } }).downloadAsset({ descriptor, url: descriptor.url, offset: 2, validator: '"immutable"' }));
  await expectCode('asset_range_invalid', () => make({ headers: { 'content-length': '4', etag: '"immutable"', 'accept-ranges': 'bytes', 'content-range': 'bytes 1-4/6' } }).downloadAsset({ descriptor, url: descriptor.url, offset: 2, validator: '"immutable"' }));
});
