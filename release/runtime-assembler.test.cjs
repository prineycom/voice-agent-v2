'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const { EventEmitter } = require('node:events');
const { Readable } = require('node:stream');
const test = require('node:test');

const assembler = require('./runtime-assembler.cjs');
const ROOT = path.resolve(__dirname, '..');

function temporary(context) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'voice-agent-runtime-assembler-test-'));
  fs.chmodSync(root, 0o700);
  context.after(() => fs.rmSync(root, { recursive: true, force: true }));
  return root;
}

const LLAMA_COMMIT = '689e227db485c6b33d061555e74034c93a867649';
const LLAMA_URL = `https://codeload.github.com/ggml-org/llama.cpp/tar.gz/${LLAMA_COMMIT}`;
function llamaItem(bytes, overrides = {}) {
  return { commit: LLAMA_COMMIT, filename: `${LLAMA_COMMIT}.tar.gz`, name: 'llama.cpp source', purpose: 'application-runtime-build', sha256: assembler.digest(bytes), size: bytes.length, url: LLAMA_URL, ...overrides };
}
function fakeHttps(responses) {
  const requests = []; let position = 0;
  return {
    requests,
    get(url, options, callback) {
      const request = new EventEmitter(); requests.push({ headers: options.headers, url: url.href });
      request.destroy = (reason) => queueMicrotask(() => request.emit('error', reason));
      queueMicrotask(() => {
        const specification = responses[position++];
        const response = Readable.from(specification.bytes || Buffer.alloc(0));
        response.statusCode = specification.status || 200; response.headers = specification.headers || {};
        callback(response);
      });
      return request;
    },
  };
}

test('committed runtime assembler authority pins the exact OCI/toolchain and closed immutable inputs', () => {
  const authority = assembler.validateAuthority(ROOT);
  assert.equal(authority.sources.builder.image, 'docker.io/nvidia/cuda@sha256:8d75fca3fc684919d806956e1fd2e197ee71a578af8106a61b4db24248fbe9be');
  assert.equal(authority.sources.builder.network_during_build, false);
  assert.equal(authority.sources.builder.ambient_mounts, false);
  assert.equal(authority.sources.node_in_application_runtime, false);
  assert.equal(new Set(authority.inputs.map((item) => item.sha256)).size, authority.inputs.length);
  const llama = authority.inputs.find((item) => item.name === 'llama.cpp source');
  const receipt = authority.sources.inputs.find((item) => item.name === 'llama.cpp source');
  assert.equal(llama.url, LLAMA_URL); assert.equal(llama.commit, LLAMA_COMMIT);
  assert.equal(llama.filename, `${LLAMA_COMMIT}.tar.gz`);
  assert.equal(llama.size, 36775744); assert.equal(llama.sha256, '0ce0978a3310651d615159689200dd751a22fb2484ab3eb4eccc25671f6db118');
  assert.equal(receipt.version, 'b10357'); assert.equal(receipt.license, 'MIT');
  assert.equal(receipt.license_url, `https://github.com/ggml-org/llama.cpp/blob/${LLAMA_COMMIT}/LICENSE`);
});

test('llama.cpp admits only the exact direct owner/repository/full-commit codeload locator', () => {
  const bytes = Buffer.from('fixture codeload archive'); const accepted = llamaItem(bytes);
  assert.equal(assembler.validateInputLocator(accepted).href, LLAMA_URL);
  const attacks = [
    { url: LLAMA_URL.replace('/ggml-org/', '/attacker/') },
    { url: LLAMA_URL.replace('/llama.cpp/', '/other/') },
    { url: LLAMA_URL.replace(LLAMA_COMMIT, 'b10357') },
    { url: LLAMA_URL.replace('/tar.gz/', '/zip/') },
    { url: LLAMA_URL.replace('/tar.gz/', '/tar.gz/refs/heads/') },
    { url: `${LLAMA_URL}?download=1` },
    { url: `${LLAMA_URL}#archive` },
    { url: LLAMA_URL.replace('https://', 'https://user@') },
    { url: LLAMA_URL.replace('codeload.github.com', 'codeload.github.com.evil.invalid') },
    { url: LLAMA_URL.replace('codeload.github.com', 'github.com') },
    { commit: LLAMA_COMMIT.slice(0, 12), url: LLAMA_URL.replace(LLAMA_COMMIT, LLAMA_COMMIT.slice(0, 12)) },
    { commit: 'f'.repeat(40), url: LLAMA_URL },
    { filename: 'branch.tar.gz' },
  ];
  for (const attack of attacks) assert.throws(() => assembler.validateInputLocator({ ...accepted, ...attack }), (reason) => reason.code === 'runtime_assembly_authority_invalid');
});

test('direct codeload fetch is credential-free, digest-checked, and repeat cache custody is exact', async (context) => {
  const root = temporary(context); const bytes = Buffer.from('fixture codeload archive'); const item = llamaItem(bytes); const transport = fakeHttps([{ bytes }]);
  const fetcher = (value, target) => assembler.fetchInput(value, target, { requestGet: transport.get });
  const shaRoot = await assembler.prepareInputs(path.join(root, 'cache'), { inputs: [item] }, true, fetcher);
  const cached = path.join(shaRoot, item.sha256); const metadata = fs.lstatSync(cached);
  assert.equal(fs.readFileSync(cached).equals(bytes), true); assert.equal(metadata.mode & 0o777, 0o400); assert.equal(metadata.nlink, 1);
  assert.equal(transport.requests.length, 1); assert.equal(transport.requests[0].url, LLAMA_URL);
  assert.equal(Object.hasOwn(transport.requests[0].headers, 'Authorization'), false); assert.equal(Object.hasOwn(transport.requests[0].headers, 'Cookie'), false);
  assert.equal(await assembler.prepareInputs(path.join(root, 'cache'), { inputs: [item] }, false, () => { throw new Error('repeat cache inspection must not fetch'); }), shaRoot);
  assert.equal(fs.readFileSync(cached).equals(bytes), true);
});

test('codeload refuses redirects and a downloaded digest mismatch leaves no cache byte', async (context) => {
  const root = temporary(context); const bytes = Buffer.from('fixture codeload archive'); const item = llamaItem(bytes); const target = path.join(root, item.sha256);
  for (const location of [LLAMA_URL, 'https://release-assets.githubusercontent.com/objects/fixture', 'https://evil.invalid/archive']) {
    const transport = fakeHttps([{ status: 302, headers: { location } }]);
    await assert.rejects(() => assembler.fetchInput(item, target, { requestGet: transport.get }), (reason) => reason.code === 'runtime_input_redirect_refused');
  }
  const redirected = fakeHttps([{ status: 302, headers: { location: LLAMA_URL } }]);
  const githubItem = { ...item, name: 'fixture source', commit: undefined, url: `https://github.com/ggml-org/llama.cpp/archive/${LLAMA_COMMIT}.tar.gz` };
  await assert.rejects(() => assembler.fetchInput(githubItem, target, { requestGet: redirected.get }), (reason) => reason.code === 'runtime_input_redirect_refused');
  const mismatch = fakeHttps([{ bytes: Buffer.from('different accepted-size bytes!!').subarray(0, bytes.length) }]);
  await assert.rejects(() => assembler.fetchInput(item, target, { requestGet: mismatch.get }), (reason) => reason.code === 'runtime_input_hash_mismatch');
  assert.equal(fs.existsSync(target), false); assert.equal(fs.existsSync(`${target}.partial`), false);
});

test('production assembler skips RUNPATH mutation for static ELF and de-duplicates llama/CUDA providers', () => {
  const shell = fs.readFileSync(path.join(ROOT, 'release', 'assemble-runtime.sh'), 'utf8');
  assert.match(shell, /readelf -dW "\$file"[^\n]+grep -q 'Dynamic section'/);
  assert.match(shell, /'\/runtime\/llama\/lib\/' in selected\.as_posix\(\): shutil\.move\(selected,target\)/);
  assert.doesNotMatch(shell, /cp .*\/output\/runtime\/llama\/lib\/.*\/output\/runtime\/lib/);
});

test('input cache accepts only exact single-link bytes and refuses missing, tampered, or ambient entries', async (context) => {
  const root = temporary(context); const bytes = Buffer.from('fixture immutable input');
  const item = { filename: 'fixture.bin', name: 'fixture', purpose: 'test', sha256: assembler.digest(bytes), size: bytes.length, url: 'https://inputs.example.invalid/fixture.bin' };
  const authority = { inputs: [item] };
  await assert.rejects(() => assembler.prepareInputs(path.join(root, 'missing'), authority, false), (reason) => reason.code === 'runtime_input_missing');
  const cache = path.join(root, 'cache'); const shaRoot = path.join(cache, 'sha256'); fs.mkdirSync(shaRoot, { recursive: true, mode: 0o700 });
  fs.writeFileSync(path.join(shaRoot, item.sha256), bytes, { mode: 0o400 }); fs.chmodSync(path.join(shaRoot, item.sha256), 0o400);
  assert.equal(await assembler.prepareInputs(cache, authority, false), shaRoot);
  fs.writeFileSync(path.join(shaRoot, 'ambient'), 'x', { mode: 0o400 });
  await assert.rejects(() => assembler.prepareInputs(cache, authority, false), (reason) => reason.code === 'ambient_cache_refused');
  fs.unlinkSync(path.join(shaRoot, 'ambient')); fs.chmodSync(path.join(shaRoot, item.sha256), 0o600); fs.writeFileSync(path.join(shaRoot, item.sha256), Buffer.alloc(bytes.length, 0x78)); fs.chmodSync(path.join(shaRoot, item.sha256), 0o400);
  await assert.rejects(() => assembler.prepareInputs(cache, authority, false), (reason) => reason.code === 'runtime_input_hash_mismatch');
});

test('fixture assembly drives one network-disabled OCI build without host Python, Node, or real network', async (context) => {
  const root = temporary(context); const cache = path.join(root, 'cache'); const output = path.join(root, 'output'); const calls = [];
  const runCommand = (command, args, options = {}) => {
    if (command === 'podman') {
      calls.push(args);
      assert.equal(args.includes('--network=none'), true);
      fs.mkdirSync(path.join(output, 'runtime'), { recursive: true, mode: 0o700 });
      fs.mkdirSync(path.join(output, 'web'), { recursive: true, mode: 0o700 });
      fs.writeFileSync(path.join(output, 'runtime', 'fixture'), 'runtime');
      fs.writeFileSync(path.join(output, 'web', 'index.html'), '<!doctype html>');
      return '';
    }
    const result = spawnSync(command, args, { cwd: options.cwd, encoding: 'utf8', timeout: options.timeout || 30000 });
    if (result.status !== 0) throw new Error(`fixture command failed: ${command}`);
    return result.stdout.trim();
  };
  const result = await assembler.assemble({ cache, output, fetch: false }, {
    root: ROOT, runCommand,
    prepareInputs: async (_cache, authority) => {
      const llama = authority.inputs.find((item) => item.name === 'llama.cpp source');
      assert.equal(llama.url, LLAMA_URL); assert.equal(llama.commit, LLAMA_COMMIT);
      assert.equal(llama.size, 36775744); assert.equal(llama.sha256, '0ce0978a3310651d615159689200dd751a22fb2484ab3eb4eccc25671f6db118');
      const value = path.join(cache, 'sha256'); fs.mkdirSync(value, { recursive: true, mode: 0o700 }); return value;
    },
    inspectBuilder() {},
  });
  assert.equal(result.output, output); assert.equal(calls.length, 1);
  assert.equal(calls[0].includes('--read-only'), true); assert.equal(calls[0].includes('--cap-drop=all'), true);
  assert.equal(fs.existsSync(path.join(output, 'web', 'index.html')), true);
});
