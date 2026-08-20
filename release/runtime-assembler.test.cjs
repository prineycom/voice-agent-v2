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
function podmanInformation(overrides = {}) {
  return JSON.stringify({
    host: { networkBackend: 'netavark', networkBackendInfo: { backend: 'netavark' }, security: { rootless: true }, ...(overrides.host || {}) },
    version: { Version: '6.0.2', ...(overrides.version || {}) },
  });
}
function networkRunner(information = podmanInformation(), helper = 'pasta 2025_08_11') {
  const calls = [];
  const runner = (command, args, options = {}) => {
    calls.push({ args, command, env: options.env });
    if (command === 'podman') return information;
    if (command === 'pasta' && helper instanceof Error) throw helper;
    if (command === 'pasta') return helper;
    throw new Error(`unexpected fixture command: ${command}`);
  };
  return { calls, runner };
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

test('rootless Podman 6 netavark and pasta facts are exact and strip ambient host authority', () => {
  const fixture = networkRunner();
  const result = assembler.inspectAcquisitionNetwork(fixture.runner, {
    HOME: '/fixture/home', PATH: '/fixture/bin', USER: 'fixture', XDG_RUNTIME_DIR: '/run/user/1234',
    HTTPS_PROXY: 'http://proxy.invalid', GITHUB_TOKEN: 'credential', CONTAINER_HOST: 'ssh://remote.invalid', REGISTRY_AUTH_FILE: '/ambient/auth.json',
  });
  assert.equal(result.mode, 'pasta'); assert.equal(result.podmanVersion, '6.0.2');
  assert.deepEqual(fixture.calls.map(({ command, args }) => [command, args]), [
    ['podman', ['--remote=false', 'info', '--format=json']], ['pasta', ['--version']],
  ]);
  for (const call of fixture.calls) {
    assert.equal(call.env.HOME, '/fixture/home'); assert.equal(call.env.PATH, '/fixture/bin');
    for (const denied of ['HTTPS_PROXY', 'GITHUB_TOKEN', 'CONTAINER_HOST', 'REGISTRY_AUTH_FILE']) assert.equal(Object.hasOwn(call.env, denied), false);
  }
});

test('acquisition network fails closed for a missing helper, rootful engine, wrong backend, unsupported engine, and arbitrary override', () => {
  const cases = [
    networkRunner(podmanInformation(), new Error('missing')),
    networkRunner(podmanInformation({ host: { security: { rootless: false } } })),
    networkRunner(podmanInformation({ host: { networkBackend: 'cni', networkBackendInfo: { backend: 'cni' } } })),
    networkRunner(podmanInformation({ version: { Version: '5.6.2' } })),
  ];
  for (const fixture of cases) assert.throws(() => assembler.inspectAcquisitionNetwork(fixture.runner, {}), (reason) => reason.code === 'runtime_acquisition_network_unsupported' && /rootless Podman 6.*netavark.*pasta/.test(reason.message));
  assert.throws(() => assembler.validateAssembleOptions({ cache: '/cache', output: '/output', network: 'host' }), (reason) => reason.code === 'runtime_acquisition_network_override_refused');
});

test('production assembler skips RUNPATH mutation for static ELF, de-duplicates providers, and clears image credentials', () => {
  const shell = fs.readFileSync(path.join(ROOT, 'release', 'assemble-runtime.sh'), 'utf8');
  assert.match(shell, /readelf -dW "\$file"[^\n]+grep -q 'Dynamic section'/);
  assert.match(shell, /'\/runtime\/llama\/lib\/' in selected\.as_posix\(\): shutil\.move\(selected,target\)/);
  assert.doesNotMatch(shell, /cp .*\/output\/runtime\/llama\/lib\/.*\/output\/runtime\/lib/);
  assert.match(shell, /unset HTTP_PROXY HTTPS_PROXY FTP_PROXY ALL_PROXY NO_PROXY/);
  assert.match(shell, /unset SSH_AUTH_SOCK GIT_ASKPASS GH_TOKEN GITHUB_TOKEN NODE_AUTH_TOKEN NPM_TOKEN/);
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

test('fixture assembly uses exact pasta only for web acquisition and network none for every build phase', async (context) => {
  const root = temporary(context); const cache = path.join(root, 'cache'); const output = path.join(root, 'output'); const calls = []; const podmanCommands = [];
  const runCommand = (command, args, options = {}) => {
    if (command === 'podman') {
      podmanCommands.push({ args, env: options.env });
      if (args[1] === 'pull') {
        const authIndex = args.indexOf('--authfile'); const authFile = args[authIndex + 1];
        assert.equal(authIndex > 1, true); assert.equal(fs.readFileSync(authFile, 'utf8'), '{}\n'); assert.equal(fs.lstatSync(authFile).mode & 0o777, 0o600);
        return '';
      }
      if (args[1] === 'image') return 'sha256:8d75fca3fc684919d806956e1fd2e197ee71a578af8106a61b4db24248fbe9be';
      calls.push({ args, env: options.env });
      if (args.at(-1) === 'assemble') {
        fs.mkdirSync(path.join(output, 'runtime'), { recursive: true, mode: 0o700 });
        fs.mkdirSync(path.join(output, 'web'), { recursive: true, mode: 0o700 });
        fs.writeFileSync(path.join(output, 'runtime', 'fixture'), 'runtime');
        fs.writeFileSync(path.join(output, 'web', 'index.html'), '<!doctype html>');
      }
      return '';
    }
    const result = spawnSync(command, args, { cwd: options.cwd, encoding: 'utf8', timeout: options.timeout || 30000 });
    if (result.status !== 0) throw new Error(`fixture command failed: ${command}`);
    return result.stdout.trim();
  };
  const result = await assembler.assemble({ cache, output, fetch: true }, {
    root: ROOT, runCommand, environment: { HOME: '/fixture/home', PATH: '/fixture/bin', HTTPS_PROXY: 'http://ambient.invalid', GITHUB_TOKEN: 'credential' },
    inspectAcquisitionNetwork: (_runner, environment) => ({ environment: assembler.isolatedPodmanEnvironment(environment), mode: 'pasta', podmanVersion: '6.0.2' }),
    prepareInputs: async (_cache, authority) => {
      const llama = authority.inputs.find((item) => item.name === 'llama.cpp source');
      assert.equal(llama.url, LLAMA_URL); assert.equal(llama.commit, LLAMA_COMMIT);
      assert.equal(llama.size, 36775744); assert.equal(llama.sha256, '0ce0978a3310651d615159689200dd751a22fb2484ab3eb4eccc25671f6db118');
      const value = path.join(cache, 'sha256'); fs.mkdirSync(value, { recursive: true, mode: 0o700 }); return value;
    },
  });
  assert.equal(result.output, output); assert.equal(calls.length, 2); assert.equal(podmanCommands.length, 4);
  const [acquire, build] = calls;
  assert.equal(acquire.args.includes('--network=pasta'), true); assert.equal(acquire.args.includes('--network=none'), false); assert.equal(acquire.args.at(-1), 'web-acquire');
  assert.equal(build.args.includes('--network=none'), true); assert.equal(build.args.includes('--network=pasta'), false); assert.equal(build.args.at(-1), 'assemble');
  for (const call of calls) {
    assert.deepEqual(call.args.slice(0, 3), ['--remote=false', 'run', '--rm']);
    for (const flag of ['--read-only', '--env-host=false', '--http-proxy=false', '--cap-drop=all']) assert.equal(call.args.includes(flag), true);
    for (const value of ['HOME=/work/home', 'XDG_CONFIG_HOME=/work/config', 'NPM_CONFIG_USERCONFIG=/dev/null', 'GIT_CONFIG_GLOBAL=/dev/null']) assert.equal(call.args.includes(value), true);
    assert.equal(Object.hasOwn(call.env, 'HTTPS_PROXY'), false); assert.equal(Object.hasOwn(call.env, 'GITHUB_TOKEN'), false);
    assert.equal(call.args.some((value) => /(?:docker|podman)\.sock/.test(value)), false);
  }
  const implementation = fs.readFileSync(path.join(ROOT, 'release', 'runtime-assembler.cjs'), 'utf8');
  assert.doesNotMatch(implementation, /slirp4netns|--network=host/);
  assert.equal(fs.existsSync(path.join(output, 'web', 'index.html')), true);
});

test('unsupported rootless facts stop before input fetch or runtime output', async (context) => {
  const root = temporary(context); const cache = path.join(root, 'cache'); const output = path.join(root, 'output'); let prepared = false;
  await assert.rejects(() => assembler.assemble({ cache, output, fetch: true }, {
    root: ROOT,
    runCommand: networkRunner(podmanInformation({ host: { security: { rootless: false } } })).runner,
    prepareInputs: async () => { prepared = true; throw new Error('must not prepare'); },
  }), (reason) => reason.code === 'runtime_acquisition_network_unsupported');
  assert.equal(prepared, false); assert.equal(fs.existsSync(output), false); assert.equal(fs.existsSync(cache), false);
});
