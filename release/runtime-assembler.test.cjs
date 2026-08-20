'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const test = require('node:test');

const assembler = require('./runtime-assembler.cjs');
const ROOT = path.resolve(__dirname, '..');

function temporary(context) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'voice-agent-runtime-assembler-test-'));
  fs.chmodSync(root, 0o700);
  context.after(() => fs.rmSync(root, { recursive: true, force: true }));
  return root;
}

test('committed runtime assembler authority pins the exact OCI/toolchain and closed immutable inputs', () => {
  const authority = assembler.validateAuthority(ROOT);
  assert.match(authority.sources.builder.image, /^docker\.io\/nvidia\/cuda@sha256:[0-9a-f]{64}$/);
  assert.equal(authority.sources.builder.network_during_build, false);
  assert.equal(authority.sources.builder.ambient_mounts, false);
  assert.equal(authority.sources.node_in_application_runtime, false);
  assert.equal(new Set(authority.inputs.map((item) => item.sha256)).size, authority.inputs.length);
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
    prepareInputs: async () => { const value = path.join(cache, 'sha256'); fs.mkdirSync(value, { recursive: true, mode: 0o700 }); return value; },
    inspectBuilder() {},
  });
  assert.equal(result.output, output); assert.equal(calls.length, 1);
  assert.equal(calls[0].includes('--read-only'), true); assert.equal(calls[0].includes('--cap-drop=all'), true);
  assert.equal(fs.existsSync(path.join(output, 'web', 'index.html')), true);
});
