'use strict';

const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');

const core = require('../launcher/voice-agent.cjs');
const release = require('./release.cjs');
const archive = require('./archive.cjs');

function descriptor(id, kind, bytes) {
  const sha256 = archive.sha256(bytes);
  return { authority: { origin: 'https://assets.example.invalid', path_prefix: '/immutable/' }, compatibility: { maximum_application_protocol: 1, maximum_launcher_protocol: 1, minimum_application_protocol: 1, minimum_launcher_protocol: 1 },
    digest: `sha256:${sha256}`, id, kind, license: { acceptance: 'accepted', id: 'Fixture-Test-Only' }, platform: core.SUPPORTED_PLATFORM, reachability: 'required', required_free_space_reserve: 0,
    sha256, size: bytes.length, url: `https://assets.example.invalid/immutable/${sha256}` };
}
function tree() {
  return new Map([
    ['bin/voice-agent-runtime', { path: 'bin/voice-agent-runtime', type: 'file', mode: '0555', bytes: Buffer.from('#!/bin/sh\nexit 0\n') }],
    ['descriptors/local-models.json', { path: 'descriptors/local-models.json', type: 'file', mode: '0444', bytes: Buffer.from('{}') }],
    ['descriptors/runtime.json', { path: 'descriptors/runtime.json', type: 'file', mode: '0444', bytes: Buffer.from('{}') }],
    ['runtime/python/bin/python3', { path: 'runtime/python/bin/python3', type: 'file', mode: '0555', bytes: Buffer.from('fixture python closure') }],
    ['runtime/node/bin/node', { path: 'runtime/node/bin/node', type: 'file', mode: '0555', bytes: Buffer.from('fixture node closure') }],
    ['web/index.html', { path: 'web/index.html', type: 'file', mode: '0444', bytes: Buffer.from('<!doctype html>') }],
  ]);
}
function assemble(parent, name = 'candidate') {
  const assets = [descriptor('model-fixture', 'model', Buffer.from('model')), descriptor('runtime-fixture', 'runtime', Buffer.from('runtime'))];
  const output = path.join(parent, name);
  release.assembleFromTree({ output, tree: tree(), version: '1.0.0-rc.1', commit: 'a'.repeat(40), timestamp: '2026-08-20T00:00:00Z', expiresAt: '2026-09-20T00:00:00Z', sequence: 1, assets,
    artifactUrl: 'https://releases.example.invalid/voice-agent/voice-agent-1.0.0-rc.1-linux-x86_64-nvidia.tar.zst', sourceReceipt: { fixture: true }, platformContract: { architecture: 'x86_64', cuda: '13.0', libc: 'glibc-2.39' }, launcher: null });
  return output;
}
function temporary(context) { const root = fs.mkdtempSync(path.join(os.tmpdir(), 'voice-agent-release-test-')); context.after(() => fs.rmSync(root, { recursive: true, force: true })); return root; }

test('application candidate archive, manifest, SBOM, provenance, and unsigned channel are canonical and reproducible', (context) => {
  const parent = temporary(context); const first = assemble(parent, 'one'); const second = assemble(parent, 'two');
  const observed = release.verifyCandidate(first); assert.equal(observed.version, '1.0.0-rc.1');
  assert.equal(fs.readFileSync(path.join(first, 'candidate-receipt.json')).equals(fs.readFileSync(path.join(second, 'candidate-receipt.json'))), true);
  const artifact = JSON.parse(fs.readFileSync(path.join(first, 'candidate-receipt.json'))).artifact;
  assert.equal(archive.sha256(fs.readFileSync(path.join(first, artifact))), archive.sha256(fs.readFileSync(path.join(second, artifact))));
  const manifest = JSON.parse(fs.readFileSync(path.join(first, 'release-manifest.json')));
  for (const required of ['bin/voice-agent-runtime', 'runtime/python/bin/python3', 'runtime/node/bin/node', 'web/index.html']) assert.equal(manifest.entries.some((item) => item.path === required), true, required);
});

test('runtime receipt refuses undeclared, unhashed, wrong-platform, wrong-architecture, and license-missing closures', (context) => {
  const parent = temporary(context); const root = path.join(parent, 'runtime'); fs.mkdirSync(path.join(root, 'python/bin'), { recursive: true }); fs.mkdirSync(path.join(root, 'node/bin'), { recursive: true });
  for (const name of ['python/bin/python3', 'node/bin/node']) { fs.writeFileSync(path.join(root, name), Buffer.from('not-elf'), { mode: 0o555 }); fs.chmodSync(path.join(root, name), 0o555); }
  const files = ['node/bin/node', 'python/bin/python3'].map((name) => ({ mode: '0555', path: name, sha256: archive.sha256(fs.readFileSync(path.join(root, name))), size: 7 }));
  const hash = crypto.createHash('sha256'); for (const item of files) hash.update(Buffer.from(`${item.path}\0${item.mode}\0${item.size}\0${item.sha256}\n`));
  const receipt = { architecture: 'x86_64', cuda: { minimum_driver: '550.54.0', runtime: 'cuda-13.0' }, files, libc: { family: 'glibc', minimum: '2.39' }, node: { version: '26.0.0' }, platform: core.SUPPORTED_PLATFORM,
    python: { version: '3.14.0' }, schema: 'voice-agent.runtime-bundle.v1', source: { immutable_url: 'https://runtime.example.invalid/bundles/exact.tar.zst', license_evidence_url: 'https://runtime.example.invalid/licenses/exact.json', redistribution_authorized: true, sha256: hash.digest('hex') }, test_only: false };
  assert.equal(release.validateRuntimeReceipt(receipt, root).platform, core.SUPPORTED_PLATFORM);
  assert.throws(() => release.validateRuntimeReceipt({ ...receipt, architecture: 'arm64' }, root), (reason) => reason.code === 'runtime_receipt_invalid');
  assert.throws(() => release.validateRuntimeReceipt({ ...receipt, source: { ...receipt.source, redistribution_authorized: false } }, root), (reason) => reason.code === 'redistribution_authority_missing');
  fs.writeFileSync(path.join(root, 'undeclared'), 'x');
  assert.throws(() => release.validateRuntimeReceipt(receipt, root), (reason) => reason.code === 'runtime_undeclared_file');
});

test('production source and lock gates refuse dirty, floating, and unhashed inputs', () => {
  assert.throws(() => release.validateSourceState(path.resolve(__dirname, '..'), ' M src/file.py', 'a'.repeat(40)), (reason) => reason.code === 'dirty_source_refused');
  const locked = { packages: { 'node_modules/exact': { resolved: 'https://registry.example.invalid/exact.tgz', integrity: 'sha512-QUFBQQ==' } } };
  assert.throws(() => release.validateExactDependencies({ dependencies: { unsafe: '^1.0.0' } }, locked, ['exact==1.0 --hash=sha256:' + 'a'.repeat(64)]), (reason) => reason.code === 'floating_dependency_refused');
  assert.throws(() => release.validateExactDependencies({ dependencies: { exact: '1.0.0' } }, locked, ['exact==1.0']), (reason) => reason.code === 'dependency_hash_missing');
  assert.throws(() => release.validateLocks(), (reason) => reason.code === 'dependency_hash_missing');
});

test('offline Ed25519 signing requires owner-only key and emits no key bytes or path', (context) => {
  const parent = temporary(context); const candidate = assemble(parent); const pair = crypto.generateKeyPairSync('ed25519'); const keyPath = path.join(parent, 'offline-owner-key.pem');
  fs.writeFileSync(keyPath, pair.privateKey.export({ type: 'pkcs8', format: 'pem' }), { mode: 0o600 }); fs.chmodSync(keyPath, 0o600);
  const result = release.signChannel({ channel: path.join(candidate, 'stable.json'), privateKey: keyPath, output: candidate });
  const channelBytes = fs.readFileSync(path.join(candidate, 'stable.json')); const signature = Buffer.from(fs.readFileSync(path.join(candidate, 'stable.json.sig'), 'ascii').trim(), 'base64');
  assert.equal(crypto.verify(null, channelBytes, pair.publicKey, signature), true);
  const output = JSON.stringify(result) + fs.readFileSync(path.join(candidate, 'signing-receipt.json'), 'utf8');
  assert.equal(output.includes(keyPath), false); assert.equal(output.includes('PRIVATE KEY'), false);
  fs.chmodSync(keyPath, 0o644);
  assert.throws(() => release.signChannel({ channel: path.join(candidate, 'stable.json'), privateKey: keyPath, output: candidate }), (reason) => reason.code === 'private_key_permission_invalid');
});

test('publication dry-run requires signed exact confirmation and remote collision/readback fail closed', (context) => {
  const parent = temporary(context); const candidate = assemble(parent); const pair = crypto.generateKeyPairSync('ed25519'); const keyPath = path.join(parent, 'key.pem');
  fs.writeFileSync(keyPath, pair.privateKey.export({ type: 'pkcs8', format: 'pem' }), { mode: 0o600 }); fs.chmodSync(keyPath, 0o600);
  const publicKey = path.join(parent, 'key.pub.pem'); fs.writeFileSync(publicKey, pair.publicKey.export({ type: 'spki', format: 'pem' }));
  release.signChannel({ channel: path.join(candidate, 'stable.json'), privateKey: keyPath, output: candidate });
  const values = { candidate, repository: 'prineycom/voice-agent-v2', publicKey, confirm: '1.0.0-rc.1:1' }; const plan = release.publicationPlan(values);
  assert.equal(plan.collision_policy, 'refuse');
  assert.throws(() => release.publishWithRemote(plan, values, { exists: () => true }), (reason) => reason.code === 'publication_collision');
  assert.throws(() => release.publishWithRemote(plan, values, { exists: () => false, sequence: () => 1 }), (reason) => reason.code === 'publication_sequence_collision');
  assert.throws(() => release.publishWithRemote(plan, values, { exists: () => false, sequence: () => 0, verifyTag: () => false }), (reason) => reason.code === 'publication_tag_mismatch');
  const remote = { exists: () => false, sequence: () => 0, verifyTag: () => true, create() {}, upload() {}, readback: () => new Map(plan.assets.map((item) => [item.name, Buffer.from('mismatch')])), publishChannel() {}, readbackChannel: () => new Map() };
  assert.throws(() => release.publishWithRemote(plan, values, remote), (reason) => reason.code === 'publication_readback_mismatch');
  assert.throws(() => release.publicationPlan({ ...values, confirm: 'wrong' }), (reason) => reason.code === 'publication_confirmation_required');
});

test('release output leak scanner refuses checkout paths, private keys, and token markers', () => {
  for (const bytes of [Buffer.from(`${path.resolve(__dirname, '..')}/src`), Buffer.from('-----BEGIN PRIVATE KEY-----\nQUFBQUFBQUFBQUFBQUFBQUFB\n-----END PRIVATE KEY-----'), Buffer.from('ghp_secret')]) {
    assert.throws(() => release.pathLeakScan(bytes), (reason) => reason.code === 'release_content_leak');
  }
});
