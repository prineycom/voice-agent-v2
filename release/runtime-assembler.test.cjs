'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const { gzipSync } = require('node:zlib');
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
  assert.equal(authority.tools.builder_image, authority.sources.builder.image);
  assert.equal(authority.tools.restricted_path, '/build/tool-bin');
  assert.equal(authority.tools.builder_tools.some((item) => item.name === 'gzip' && item.version_contains === '1.9'), true);
  assert.equal(authority.tools.builder_tools.some((item) => item.name === 'cicc'), true);
  assert.deepEqual(authority.tools.content_addressed_tools.map((item) => item.name), ['node', 'npm', 'python', 'pip', 'cmake', 'patchelf']);
  assert.deepEqual(authority.tools.npm_lock_tools.map((item) => item.name), ['vite', 'rolldown', 'rolldown-linux-x64-gnu', 'lightningcss', 'lightningcss-linux-x64-gnu']);
  assert.equal(new Set(authority.inputs.map((item) => item.sha256)).size, authority.inputs.length);
  const node = authority.sources.inputs.find((item) => item.name === 'Node.js');
  assert.equal(node.url, 'https://nodejs.org/download/release/v26.7.0/node-v26.7.0-linux-x64.tar.gz');
  assert.equal(node.size, 62014253); assert.equal(node.sha256, 'bd6b6c31e377bad9ad579bed72e5bc11f4c879ac9452ad51d30e646ea3d828df');
  assert.deepEqual(node.signed_checksum, {
    url: 'https://nodejs.org/download/release/v26.7.0/SHASUMS256.txt', sha256: '4533f0a43b9ba7f78a48230a0511b9dd5c931f20c3b3cac281ff9b7a2080fb2e', size: 2943,
    signature_url: 'https://nodejs.org/download/release/v26.7.0/SHASUMS256.txt.sig', signature_sha256: '7bb1dfdce6e58b8659b3e7f3e148c8165ad715358fd4876be49aa656fc8b8224', signature_size: 119,
    signer_fingerprint: '5BE8A3F6C8A5C01D106C0AD820B1A390B168D356',
  });
  const llama = authority.inputs.find((item) => item.name === 'llama.cpp source');
  const receipt = authority.sources.inputs.find((item) => item.name === 'llama.cpp source');
  assert.equal(llama.url, LLAMA_URL); assert.equal(llama.commit, LLAMA_COMMIT);
  assert.equal(llama.filename, `${LLAMA_COMMIT}.tar.gz`);
  assert.equal(llama.size, 36775744); assert.equal(llama.sha256, '0ce0978a3310651d615159689200dd751a22fb2484ab3eb4eccc25671f6db118');
  assert.equal(receipt.version, 'b10357'); assert.equal(receipt.license, 'MIT');
  assert.equal(receipt.license_url, `https://github.com/ggml-org/llama.cpp/blob/${LLAMA_COMMIT}/LICENSE`);
});

test('the accepted exact Node gzip archive extracts without xz and retains exact file bytes', (context) => {
  const root = temporary(context); const archive = path.join(root, 'node-fixture.tar.gz'); const output = path.join(root, 'out'); const bin = path.join(root, 'bin');
  fs.mkdirSync(output); fs.mkdirSync(bin);
  const payload = Buffer.from('v26.7.0\n'); const header = Buffer.alloc(512);
  header.write('node-v26.7.0-linux-x64/bin/node');
  const octal = (offset, length, value) => header.write(`${value.toString(8).padStart(length - 1, '0')}\0`, offset, length, 'ascii');
  octal(100, 8, 0o755); octal(108, 8, 0); octal(116, 8, 0); octal(124, 12, payload.length); octal(136, 12, 0);
  header.fill(0x20, 148, 156); header[156] = '0'.charCodeAt(0); header.write('ustar\0', 257); header.write('00', 263);
  octal(148, 8, [...header].reduce((sum, value) => sum + value, 0));
  const tar = Buffer.concat([header, payload, Buffer.alloc((512 - payload.length % 512) % 512), Buffer.alloc(1024)]);
  fs.writeFileSync(archive, gzipSync(tar, { level: 9, mtime: 0 }));
  for (const name of ['tar', 'gzip']) fs.symlinkSync(spawnSync('which', [name], { encoding: 'utf8' }).stdout.trim(), path.join(bin, name));
  const result = spawnSync(path.join(bin, 'tar'), ['-xzf', archive, '-C', output, '--strip-components=1'], { env: { PATH: bin }, encoding: 'utf8' });
  assert.equal(result.status, 0, result.stderr); assert.equal(fs.readFileSync(path.join(output, 'bin', 'node')).equals(payload), true);
  assert.equal(fs.existsSync(path.join(bin, 'xz')), false);
});

test('tool closure reports absent, tampered, and extra tools together without host fallback data', (context) => {
  const root = temporary(context); const authority = assembler.validateAuthority(ROOT); const report = path.join(root, 'tool-report.tsv');
  const lines = assembler.fixtureToolReport(authority, ROOT).trimEnd().split('\n');
  const altered = lines.filter((line) => !line.includes('\tbash\t')).map((line) => line.includes('\tgcc\t') ? line.replace('\t8.5.0\tok', '\twrong-version\tmismatch') : line)
    .map((line) => line.includes('\tpatchelf\t') ? line.replace(/^sha256:[^\t]+/, 'sha256:' + 'f'.repeat(64)) : line);
  altered.push(`builder:${authority.sources.builder.manifest_digest}\tambient-extra\t/host/bin/tool\t1\tok`);
  fs.writeFileSync(report, `${altered.join('\n')}\n`);
  assert.throws(() => assembler.inspectToolReport(report, authority, ROOT), (reason) => reason.code === 'runtime_tool_closure_invalid'
    && reason.message.includes('absent=[bash]') && reason.message.includes('mismatch=[gcc,patchelf]') && reason.message.includes('extra=[ambient-extra]')
    && !reason.message.includes('/host/bin/tool'));
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

test('production assembler closes extraction, compiler, ELF, PATH, and credential tool authority', () => {
  const shell = fs.readFileSync(path.join(ROOT, 'release', 'assemble-runtime.sh'), 'utf8');
  assert.match(shell, /input node-v26\.7\.0-linux-x64\.tar\.gz/); assert.doesNotMatch(shell, /tar -xJf|node-v26\.7\.0-linux-x64\.tar\.xz/);
  assert.match(shell, /export PATH=\/build\/tool-bin/); assert.match(shell, /builder_tool_preflight/);
  assert.match(shell, /readelf -dW "\$file"[^\n]+grep -q 'Dynamic section'/);
  assert.match(shell, /'\/runtime\/llama\/lib\/' in selected\.as_posix\(\): shutil\.move\(selected,target\)/);
  assert.doesNotMatch(shell, /cp .*\/output\/runtime\/llama\/lib\/.*\/output\/runtime\/lib/);
  assert.match(shell, /pacote\.tarball\.stream/); assert.match(shell, /npm-cli\.js ci --ignore-scripts --offline/);
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
  fs.unlinkSync(path.join(shaRoot, 'ambient')); fs.chmodSync(path.join(shaRoot, item.sha256), 0o600); fs.appendFileSync(path.join(shaRoot, item.sha256), 'x'); fs.chmodSync(path.join(shaRoot, item.sha256), 0o400);
  await assert.rejects(() => assembler.prepareInputs(cache, authority, false), (reason) => reason.code === 'runtime_input_custody_invalid');
  fs.chmodSync(path.join(shaRoot, item.sha256), 0o600); fs.writeFileSync(path.join(shaRoot, item.sha256), Buffer.alloc(bytes.length, 0x78)); fs.chmodSync(path.join(shaRoot, item.sha256), 0o400);
  await assert.rejects(() => assembler.prepareInputs(cache, authority, false), (reason) => reason.code === 'runtime_input_hash_mismatch');
});

test('fixture assembly preflights the full closure, cache-installs tools offline, and builds only with network none', async (context) => {
  const root = temporary(context); const cache = path.join(root, 'cache'); const output = path.join(root, 'output'); const calls = []; const podmanCommands = []; const prepared = [];
  const authority = assembler.validateAuthority(ROOT);
  const buildRoot = (args) => {
    const mount = args.find((value) => typeof value === 'string' && value.endsWith(':/build:rw'));
    return mount && mount.slice(0, -':/build:rw'.length);
  };
  const runCommand = (command, args, options = {}) => {
    if (command === 'podman') {
      podmanCommands.push({ args, env: options.env });
      if (args[1] === 'pull') {
        const authIndex = args.indexOf('--authfile'); const authFile = args[authIndex + 1];
        assert.equal(authIndex > 1, true); assert.equal(fs.readFileSync(authFile, 'utf8'), '{}\n'); assert.equal(fs.lstatSync(authFile).mode & 0o777, 0o600);
        return '';
      }
      if (args[1] === 'image') return authority.sources.builder.manifest_digest;
      calls.push({ args, env: options.env });
      if (args.at(-1) === 'tool-preflight') fs.writeFileSync(path.join(buildRoot(args), 'tool-report.tsv'), assembler.fixtureToolReport(authority, ROOT, new Set(['builder', 'content'])));
      if (args.at(-1) === 'web-prepare') fs.appendFileSync(path.join(buildRoot(args), 'tool-report.tsv'), assembler.fixtureToolReport(authority, ROOT, new Set(['web'])));
      if (args.at(-1) === 'assemble') {
        fs.mkdirSync(path.join(output, 'runtime'), { recursive: true, mode: 0o700 }); fs.mkdirSync(path.join(output, 'web'), { recursive: true, mode: 0o700 });
        fs.writeFileSync(path.join(output, 'runtime', 'fixture'), 'runtime'); fs.writeFileSync(path.join(output, 'web', 'index.html'), '<!doctype html>');
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
    prepareInputs: async (_cache, value, _fetch, _fetcher, selected = value.inputs) => {
      prepared.push(selected.map((item) => item.name));
      const shaRoot = path.join(cache, 'sha256'); fs.mkdirSync(shaRoot, { recursive: true, mode: 0o700 }); return shaRoot;
    },
  });
  assert.equal(prepared.length, 2); assert.equal(prepared[0].includes('Node.js'), true); assert.equal(prepared[0].includes('llama.cpp source'), false); assert.equal(prepared[1].includes('llama.cpp source'), true);
  assert.equal(result.output, output); assert.equal(result.tools.length, authority.tools.builder_tools.length + authority.tools.content_addressed_tools.length + authority.tools.npm_lock_tools.length);
  assert.equal(calls.length, 4); assert.equal(podmanCommands.length, 6);
  assert.deepEqual(calls.map((call) => call.args.at(-1)), ['tool-preflight', 'web-acquire', 'web-prepare', 'assemble']);
  const [toolPreflight, acquire, webPrepare, build] = calls;
  for (const call of [toolPreflight, webPrepare, build]) { assert.equal(call.args.includes('--network=none'), true); assert.equal(call.args.includes('--network=pasta'), false); }
  assert.equal(acquire.args.includes('--network=pasta'), true); assert.equal(acquire.args.includes('--network=none'), false);
  assert.equal(toolPreflight.args.some((value) => value.endsWith(':/build:rw')), true); assert.equal(acquire.args.some((value) => value.endsWith(':/build:ro')), true);
  assert.equal(webPrepare.args.some((value) => value.endsWith(':/build:rw')), true); assert.equal(build.args.some((value) => value.endsWith(':/build:ro')), true);
  for (const call of calls) {
    assert.deepEqual(call.args.slice(0, 3), ['--remote=false', 'run', '--rm']);
    for (const flag of ['--read-only', '--env-host=false', '--http-proxy=false', '--cap-drop=all']) assert.equal(call.args.includes(flag), true);
    for (const value of ['HOME=/work/home', 'XDG_CONFIG_HOME=/work/config', 'NPM_CONFIG_USERCONFIG=/dev/null', 'GIT_CONFIG_GLOBAL=/dev/null']) assert.equal(call.args.includes(value), true);
    assert.equal(Object.hasOwn(call.env, 'HTTPS_PROXY'), false); assert.equal(Object.hasOwn(call.env, 'GITHUB_TOKEN'), false);
    assert.equal(call.args.some((value) => /(?:docker|podman)\.sock/.test(value)), false);
    assert.equal(call.args.some((value) => value.includes('/usr/bin/xz') || value.includes('/bin/xz')), false);
  }
  const implementation = fs.readFileSync(path.join(ROOT, 'release', 'runtime-assembler.cjs'), 'utf8');
  assert.doesNotMatch(implementation, /slirp4netns|--network=host/); assert.equal(fs.existsSync(path.join(output, 'web', 'index.html')), true);
});

test('one complete tool preflight error stops before remaining fetch, web acquisition, or runtime output', async (context) => {
  const root = temporary(context); const cache = path.join(root, 'cache'); const output = path.join(root, 'output'); const authority = assembler.validateAuthority(ROOT); const phases = []; let inputPasses = 0;
  const runCommand = (command, args, options = {}) => {
    if (command === 'podman') {
      phases.push(args.at(-1));
      const mount = args.find((value) => typeof value === 'string' && value.endsWith(':/build:rw')); const build = mount.slice(0, -':/build:rw'.length);
      const lines = assembler.fixtureToolReport(authority, ROOT, new Set(['builder', 'content'])).trimEnd().split('\n')
        .filter((line) => !line.includes('\tbash\t')).map((line) => line.includes('\tgcc\t') ? line.replace('\t8.5.0\tok', '\ttampered\tmismatch') : line);
      lines.push(`builder:${authority.sources.builder.manifest_digest}\textra-tool\t/ambient/tool\t1\tok`); fs.writeFileSync(path.join(build, 'tool-report.tsv'), `${lines.join('\n')}\n`); return '';
    }
    const result = spawnSync(command, args, { cwd: options.cwd, encoding: 'utf8' }); if (result.status !== 0) throw new Error('fixture command failed'); return result.stdout.trim();
  };
  await assert.rejects(() => assembler.assemble({ cache, output, fetch: true }, {
    root: ROOT, runCommand, inspectBuilder: () => {}, inspectAcquisitionNetwork: (_runner, environment) => ({ environment: assembler.isolatedPodmanEnvironment(environment) }),
    prepareInputs: async () => { inputPasses += 1; const shaRoot = path.join(cache, 'sha256'); fs.mkdirSync(shaRoot, { recursive: true }); return shaRoot; },
  }), (reason) => reason.code === 'runtime_tool_closure_invalid' && reason.message.includes('absent=[') && reason.message.includes('bash') && reason.message.includes('mismatch=[gcc]') && reason.message.includes('extra=[extra-tool]'));
  assert.deepEqual(phases, ['tool-preflight']); assert.equal(inputPasses, 1); assert.equal(fs.existsSync(output), false);
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
