'use strict';

const crypto = require('node:crypto');
const fs = require('node:fs');
const https = require('node:https');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

const SHA256 = /^[0-9a-f]{64}$/;
const GIT_COMMIT = /^[0-9a-f]{40}$/;
const BUILDER = /^docker\.io\/nvidia\/cuda@sha256:([0-9a-f]{64})$/;
const CODELOAD_HOST = 'codeload.github.com';
const LLAMA_REPOSITORY = 'ggml-org/llama.cpp';
const REDIRECT_HOSTS = new Set(['github.com', 'objects.githubusercontent.com', 'release-assets.githubusercontent.com', 'github-production-release-asset-2e65be.s3.amazonaws.com']);
const PODMAN_MAJOR = 6;
const PODMAN_ENVIRONMENT_KEYS = new Set(['DBUS_SESSION_BUS_ADDRESS', 'HOME', 'LANG', 'LC_ALL', 'LOGNAME', 'PATH', 'TMPDIR', 'USER', 'XDG_RUNTIME_DIR']);

class AssemblyError extends Error { constructor(code, message) { super(message); this.code = code; } }
function fail(code, message) { throw new AssemblyError(code, message); }
function digest(bytes) { return crypto.createHash('sha256').update(bytes).digest('hex'); }
function exactKeys(value, keys, code = 'runtime_assembly_authority_invalid') {
  if (!value || typeof value !== 'object' || Array.isArray(value) || Object.keys(value).sort().join('\0') !== [...keys].sort().join('\0')) fail(code, 'runtime assembly authority is not closed');
}
function run(command, args, options = {}) {
  const result = spawnSync(command, args, { cwd: options.cwd, env: options.env || process.env, encoding: 'utf8', timeout: options.timeout || 120000, input: options.input });
  if (result.status !== 0) fail(options.code || 'runtime_assembly_failed', options.message || 'bounded runtime assembly command failed');
  return result.stdout.trim();
}

function isolatedPodmanEnvironment(environment = process.env) {
  return Object.fromEntries(Object.entries(environment).filter(([name]) => PODMAN_ENVIRONMENT_KEYS.has(name) || name.startsWith('LC_')));
}

function inspectAcquisitionNetwork(runner = run, environment = process.env) {
  const code = 'runtime_acquisition_network_unsupported';
  const message = 'runtime acquisition requires local rootless Podman 6 with netavark and a working pasta helper';
  const env = isolatedPodmanEnvironment(environment);
  let raw;
  try { raw = runner('podman', ['--remote=false', 'info', '--format=json'], { env, code, message }); }
  catch { fail(code, message); }
  let information;
  try { information = JSON.parse(raw); } catch { fail(code, message); }
  const host = information && information.host; const version = information && information.version && information.version.Version;
  const backend = host && host.networkBackendInfo;
  if (!host || host.security?.rootless !== true || host.networkBackend !== 'netavark'
    || !backend || backend.backend !== 'netavark' || typeof version !== 'string'
    || Number.parseInt(version.split('.')[0], 10) !== PODMAN_MAJOR) fail(code, message);
  let helper;
  try { helper = runner('pasta', ['--version'], { env, code, message }); }
  catch { fail(code, message); }
  if (!/^pasta\s+\S+/m.test(helper)) fail(code, message);
  return { environment: env, mode: 'pasta', podmanVersion: version };
}

function validateAssembleOptions(values) {
  const allowed = new Set(['cache', 'compareWith', 'fetch', 'output']);
  if (!values || typeof values !== 'object' || Array.isArray(values) || Object.keys(values).some((name) => !allowed.has(name))
    || (Object.hasOwn(values, 'fetch') && typeof values.fetch !== 'boolean')) fail('runtime_acquisition_network_override_refused', 'runtime acquisition network is fixed to rootless pasta and cannot be overridden');
}
function validatePreflightOptions(values) {
  const allowed = new Set(['cache', 'fetch']);
  if (!values || typeof values !== 'object' || Array.isArray(values) || Object.keys(values).some((name) => !allowed.has(name))
    || (Object.hasOwn(values, 'fetch') && typeof values.fetch !== 'boolean')) fail('runtime_acquisition_network_override_refused', 'runtime acquisition network is fixed to rootless pasta and cannot be overridden');
}

function parseHttpsUrl(value, code = 'runtime_assembly_authority_invalid') {
  let url; try { url = new URL(value); } catch { fail(code, 'immutable runtime input URL is invalid'); }
  if (url.protocol !== 'https:' || url.username || url.password || url.port || url.search || url.hash) fail(code, 'immutable runtime input URL is not canonical HTTPS authority');
  return url;
}

function validateInputLocator(item) {
  const url = parseHttpsUrl(item.url);
  if (item.name === 'llama.cpp source' || url.hostname === CODELOAD_HOST) {
    if (item.name !== 'llama.cpp source' || !GIT_COMMIT.test(item.commit || '')) fail('runtime_assembly_authority_invalid', 'llama.cpp source commit authority is invalid');
    const expected = `https://${CODELOAD_HOST}/${LLAMA_REPOSITORY}/tar.gz/${item.commit}`;
    if (item.url !== expected || item.filename !== `${item.commit}.tar.gz` || url.hostname !== CODELOAD_HOST || url.pathname !== `/${LLAMA_REPOSITORY}/tar.gz/${item.commit}`) fail('runtime_assembly_authority_invalid', 'llama.cpp codeload authority is invalid');
  }
  return url;
}

function validateAuthority(root) {
  const sources = JSON.parse(fs.readFileSync(path.join(root, 'release', 'inputs', 'runtime-sources.v1.json')));
  const wheels = JSON.parse(fs.readFileSync(path.join(root, 'release', 'inputs', 'python-wheelhouse.v1.json')));
  const tools = JSON.parse(fs.readFileSync(path.join(root, 'release', 'inputs', 'builder-tools.v1.json')));
  exactKeys(sources, ['ambient_cache_allowed', 'builder', 'host_boundary', 'host_node_allowed', 'host_python_allowed', 'inputs', 'node_in_application_runtime', 'platform', 'schema', 'tool_closure', 'wheelhouse']);
  exactKeys(sources.builder, ['ambient_mounts', 'cuda_toolchain', 'glibc_floor', 'image', 'manifest_digest', 'network_during_build', 'platform', 'upstream_tag']);
  const match = BUILDER.exec(sources.builder.image);
  if (sources.schema !== 'voice-agent.runtime-sources.v1' || sources.platform !== 'linux-x86_64-nvidia' || !match
    || sources.builder.manifest_digest !== `sha256:${match[1]}` || sources.builder.platform !== 'linux/amd64'
    || sources.builder.glibc_floor !== '2.28' || sources.builder.cuda_toolchain !== '12.9.1'
    || sources.builder.network_during_build !== false || sources.builder.ambient_mounts !== false
    || sources.node_in_application_runtime !== false || sources.ambient_cache_allowed !== false
    || sources.host_python_allowed !== false || sources.host_node_allowed !== false || sources.tool_closure !== 'builder-tools.v1.json') fail('runtime_assembly_authority_invalid', 'builder/runtime authority is invalid');
  exactKeys(tools, ['builder_image', 'builder_tools', 'content_addressed_tools', 'npm_lock_tools', 'restricted_path', 'schema']);
  if (tools.schema !== 'voice-agent.builder-tools.v1' || tools.builder_image !== sources.builder.image || tools.restricted_path !== '/build/tool-bin'
    || !Array.isArray(tools.builder_tools) || !Array.isArray(tools.content_addressed_tools) || !Array.isArray(tools.npm_lock_tools)
    || tools.builder_tools.length < 30 || tools.content_addressed_tools.length < 6 || tools.npm_lock_tools.length < 5) fail('runtime_assembly_authority_invalid', 'builder tool closure authority is invalid');
  for (const item of tools.builder_tools) {
    exactKeys(item, ['name', 'path', 'version_contains']);
    if (!/^[A-Za-z0-9+._-]+$/.test(item.name) || !/^\/(?:[A-Za-z0-9+._-]+\/)*[A-Za-z0-9+._-]+$/.test(item.path) || typeof item.version_contains !== 'string' || !item.version_contains) fail('runtime_assembly_authority_invalid', 'builder tool closure authority is invalid');
  }
  for (const item of tools.content_addressed_tools) {
    exactKeys(item, ['input', 'name', 'path', 'version_contains']);
    if (!/^[A-Za-z0-9+._-]+$/.test(item.name) || typeof item.input !== 'string' || !item.input || !item.path.startsWith('/build/tools/') || typeof item.version_contains !== 'string' || !item.version_contains) fail('runtime_assembly_authority_invalid', 'content-addressed tool closure authority is invalid');
  }
  for (const item of tools.npm_lock_tools) {
    exactKeys(item, ['name', 'package', 'version']);
    if (!/^[A-Za-z0-9+._-]+$/.test(item.name) || !/^(?:@[A-Za-z0-9._-]+\/)?[A-Za-z0-9._-]+$/.test(item.package) || !/^\d+\.\d+\.\d+$/.test(item.version)) fail('runtime_assembly_authority_invalid', 'npm tool closure authority is invalid');
  }
  const toolNames = [...tools.builder_tools, ...tools.content_addressed_tools, ...tools.npm_lock_tools].map((item) => item.name);
  if (new Set(toolNames).size !== toolNames.length) fail('runtime_assembly_authority_invalid', 'runtime tool name is duplicated');
  const npmLock = JSON.parse(fs.readFileSync(path.join(root, 'web', 'package-lock.json')));
  if (!npmLock || npmLock.lockfileVersion !== 3 || !npmLock.packages || typeof npmLock.packages !== 'object') fail('runtime_assembly_authority_invalid', 'npm tool lock authority is invalid');
  for (const item of Object.values(npmLock.packages)) {
    if (!item || !item.resolved) continue;
    const url = parseHttpsUrl(item.resolved);
    if (url.hostname !== 'registry.npmjs.org' || !/^sha512-[A-Za-z0-9+/]+={0,2}$/.test(item.integrity || '')) fail('runtime_assembly_authority_invalid', 'npm tool lock authority is invalid');
  }
  for (const item of tools.npm_lock_tools) if (npmLock.packages[`node_modules/${item.package}`]?.version !== item.version) fail('runtime_assembly_authority_invalid', 'npm tool version differs from exact lock');
  if (wheels.schema !== 'voice-agent.python-wheelhouse.v1' || wheels.python !== '3.12.13' || !Array.isArray(wheels.wheels) || wheels.wheels.length < 10) fail('runtime_assembly_authority_invalid', 'wheelhouse authority is invalid');
  const inputs = [];
  for (const item of [...sources.inputs, ...wheels.wheels]) {
    const url = item.url; const size = item.size; const sha256 = item.sha256;
    if (typeof url !== 'string') fail('runtime_assembly_authority_invalid', 'immutable runtime input is invalid');
    const locator = validateInputLocator(item); const filename = item.filename || path.basename(locator.pathname);
    if (!Number.isSafeInteger(size) || size < 1 || !SHA256.test(sha256)
      || typeof filename !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9._+%-]{0,255}$/.test(filename)) fail('runtime_assembly_authority_invalid', 'immutable runtime input is invalid');
    inputs.push({ commit: item.commit, filename, name: item.name, purpose: item.purpose || 'python-wheel', sha256, size, url });
  }
  const digests = new Set();
  for (const item of inputs) { if (digests.has(item.sha256)) fail('runtime_assembly_authority_invalid', 'runtime input digest is duplicated'); digests.add(item.sha256); }
  for (const tool of tools.content_addressed_tools) if (!inputs.some((item) => item.name === tool.input)) fail('runtime_assembly_authority_invalid', 'content-addressed tool input is absent');
  const node = sources.inputs.find((item) => item.name === 'Node.js');
  if (!node || node.version !== '26.7.0' || node.url !== 'https://nodejs.org/download/release/v26.7.0/node-v26.7.0-linux-x64.tar.gz'
    || node.size !== 62014253 || node.sha256 !== 'bd6b6c31e377bad9ad579bed72e5bc11f4c879ac9452ad51d30e646ea3d828df'
    || node.license !== 'MIT' || node.license_url !== 'https://github.com/nodejs/node/blob/v26.7.0/LICENSE') fail('runtime_assembly_authority_invalid', 'Node build input authority is invalid');
  exactKeys(node.signed_checksum, ['sha256', 'signature_sha256', 'signature_size', 'signature_url', 'signer_fingerprint', 'size', 'url']);
  if (node.signed_checksum.url !== 'https://nodejs.org/download/release/v26.7.0/SHASUMS256.txt' || node.signed_checksum.size !== 2943
    || node.signed_checksum.sha256 !== '4533f0a43b9ba7f78a48230a0511b9dd5c931f20c3b3cac281ff9b7a2080fb2e'
    || node.signed_checksum.signature_url !== 'https://nodejs.org/download/release/v26.7.0/SHASUMS256.txt.sig' || node.signed_checksum.signature_size !== 119
    || node.signed_checksum.signature_sha256 !== '7bb1dfdce6e58b8659b3e7f3e148c8165ad715358fd4876be49aa656fc8b8224'
    || node.signed_checksum.signer_fingerprint !== '5BE8A3F6C8A5C01D106C0AD820B1A390B168D356') fail('runtime_assembly_authority_invalid', 'Node signed checksum authority is invalid');
  return { sources, wheels, tools, inputs };
}

function inspectInput(filename, item) {
  let metadata; try { metadata = fs.lstatSync(filename); } catch (reason) { if (reason.code === 'ENOENT') return false; throw reason; }
  if (!metadata.isFile() || metadata.isSymbolicLink() || metadata.nlink !== 1 || metadata.size !== item.size) fail('runtime_input_custody_invalid', 'cached runtime input custody differs');
  if (digest(fs.readFileSync(filename)) !== item.sha256) fail('runtime_input_hash_mismatch', 'cached runtime input digest differs');
  return true;
}

function fetchInput(item, target, options = {}) {
  const initialUrl = validateInputLocator(item); const requestGet = options.requestGet || https.get;
  const requestUrl = (url, redirects, originalHost) => new Promise((resolve, reject) => {
    if (url.protocol !== 'https:' || redirects > 4 || (redirects > 0 && url.hostname !== originalHost && !REDIRECT_HOSTS.has(url.hostname))) return reject(new AssemblyError('runtime_input_redirect_refused', 'runtime input redirect escaped closed HTTPS authority'));
    const request = requestGet(url, { headers: { Accept: 'application/octet-stream', 'Accept-Encoding': 'identity', 'User-Agent': 'voice-agent-runtime-assembler/1' }, timeout: 30000 }, (response) => {
      if ([301, 302, 303, 307, 308].includes(response.statusCode)) {
        response.resume();
        if (!response.headers.location || url.hostname === CODELOAD_HOST) return reject(new AssemblyError('runtime_input_redirect_refused', 'runtime input redirect is not admitted'));
        let next; try { next = new URL(response.headers.location, url); } catch { return reject(new AssemblyError('runtime_input_redirect_refused', 'runtime input redirect is invalid')); }
        if (next.protocol !== 'https:' || next.username || next.password || next.hostname === CODELOAD_HOST) return reject(new AssemblyError('runtime_input_redirect_refused', 'runtime input redirect escaped closed HTTPS authority'));
        return requestUrl(next, redirects + 1, originalHost).then(resolve, reject);
      }
      if (response.statusCode !== 200) { response.resume(); return reject(new AssemblyError('runtime_input_unavailable', 'runtime input server refused exact bytes')); }
      const temporary = `${target}.partial`; let seen = 0; const hash = crypto.createHash('sha256'); const output = fs.createWriteStream(temporary, { flags: 'wx', mode: 0o600 });
      response.on('data', (chunk) => { seen += chunk.length; if (seen > item.size) request.destroy(new AssemblyError('runtime_input_oversize', 'runtime input exceeded signed size')); else hash.update(chunk); });
      response.pipe(output);
      const abort = (reason) => { output.destroy(); try { fs.unlinkSync(temporary); } catch {} reject(reason instanceof AssemblyError ? reason : new AssemblyError('runtime_input_unavailable', 'runtime input acquisition failed')); };
      request.on('error', abort); response.on('error', abort); output.on('error', abort);
      output.on('close', () => {
        try {
          if (seen !== item.size || hash.digest('hex') !== item.sha256) fail('runtime_input_hash_mismatch', 'downloaded runtime input differs from signed receipt');
          fs.chmodSync(temporary, 0o400); fs.renameSync(temporary, target); resolve();
        } catch (reason) { abort(reason); }
      });
    });
    request.on('timeout', () => request.destroy(new AssemblyError('runtime_input_timeout', 'runtime input acquisition exceeded deadline')));
    request.on('error', reject);
  });
  return requestUrl(initialUrl, 0, initialUrl.hostname);
}

async function prepareInputs(cacheRoot, authority, fetch, fetcher = fetchInput, selectedInputs = authority.inputs) {
  fs.mkdirSync(cacheRoot, { recursive: true, mode: 0o700 }); fs.chmodSync(cacheRoot, 0o700);
  const shaRoot = path.join(cacheRoot, 'sha256'); fs.mkdirSync(shaRoot, { recursive: true, mode: 0o700 }); fs.chmodSync(shaRoot, 0o700);
  for (const name of fs.readdirSync(shaRoot)) if (!SHA256.test(name) || !authority.inputs.some((item) => item.sha256 === name)) fail('ambient_cache_refused', 'assembly cache contains bytes outside release input authority');
  for (const item of selectedInputs) {
    const filename = path.join(shaRoot, item.sha256);
    if (inspectInput(filename, item)) continue;
    if (!fetch) fail('runtime_input_missing', 'an accepted immutable runtime input is absent; rerun with --fetch');
    await fetcher(item, filename);
    inspectInput(filename, item);
  }
  return shaRoot;
}

function toolAuthorityRows(authority, root) {
  const rows = [];
  for (const item of authority.tools.builder_tools) rows.push({ expected: item.version_contains, name: item.name, path: item.path, phase: 'builder', provenance: `builder:${authority.sources.builder.manifest_digest}` });
  for (const item of authority.tools.content_addressed_tools) {
    const input = authority.inputs.find((candidate) => candidate.name === item.input);
    rows.push({ detail: input.filename, expected: item.version_contains, name: item.name, path: item.path, phase: 'content', provenance: `sha256:${input.sha256}` });
  }
  const lockDigest = digest(fs.readFileSync(path.join(root, 'web', 'package-lock.json')));
  for (const item of authority.tools.npm_lock_tools) rows.push({ expected: item.version, name: item.name, path: item.package, phase: 'web', provenance: `npm-lock:${lockDigest}` });
  return rows;
}

function toolAuthorityTsv(authority, root) {
  return toolAuthorityRows(authority, root).map((item) => [item.phase, item.provenance, item.name, item.path, item.expected, item.detail || '-'].join('\t')).join('\n') + '\n';
}

function fixtureToolReport(authority, root, phases = new Set(['builder', 'content', 'web'])) {
  return toolAuthorityRows(authority, root).filter((item) => phases.has(item.phase)).map((item) => [item.provenance, item.name, item.path, item.expected === 'builder-image' ? 'builder-image' : item.expected, 'ok'].join('\t')).join('\n') + '\n';
}

function inspectToolReport(filename, authority, root, phases = new Set(['builder', 'content', 'web'])) {
  const expectedRows = toolAuthorityRows(authority, root).filter((item) => phases.has(item.phase));
  const expected = new Map(expectedRows.map((item) => [item.name, item]));
  const observed = new Map(); const extra = [];
  let text; try { text = fs.readFileSync(filename, 'utf8'); } catch { text = ''; }
  for (const line of text.split('\n').filter(Boolean)) {
    const fields = line.split('\t');
    if (fields.length !== 5 || observed.has(fields[1]) || !expected.has(fields[1])) { extra.push(fields[1] || 'malformed'); continue; }
    observed.set(fields[1], { provenance: fields[0], name: fields[1], path: fields[2], version: fields[3], status: fields[4] });
  }
  const absent = []; const mismatch = []; const blocked = [];
  for (const row of expectedRows) {
    const item = observed.get(row.name);
    if (!item) { absent.push(row.name); continue; }
    if (item.status === 'blocked') { blocked.push(row.name); continue; }
    if (item.status === 'absent') { absent.push(row.name); continue; }
    if (item.status !== 'ok' || item.provenance !== row.provenance || item.path !== row.path
      || (row.expected === 'builder-image' ? item.version !== 'builder-image' : !item.version.includes(row.expected))) mismatch.push(row.name);
  }
  if (absent.length || mismatch.length || blocked.length || extra.length) {
    const list = (values) => `[${[...new Set(values)].sort().join(',')}]`;
    fail('runtime_tool_closure_invalid', `complete runtime tool preflight failed: absent=${list(absent)} mismatch=${list(mismatch)} blocked=${list(blocked)} extra=${list(extra)}`);
  }
  return expectedRows.map((row) => {
    const item = observed.get(row.name);
    return { name: row.name, path: row.path, provenance: row.provenance, version: item.version };
  });
}

function copyTrackedSource(root, target, runner = run) {
  fs.mkdirSync(target, { mode: 0o700 });
  const excluded = new Set(['src/voice_agent_v2/operations.py', 'src/voice_agent_v2/operations_cli.py', 'src/voice_agent_v2/local_tts.py', 'src/voice_agent_v2/cloud_llm.py']);
  const allowedConfig = new Set(['config/agent-capabilities-v1.json', 'config/silero-kseniya-tts-v1.json']);
  const names = runner('git', ['ls-files', '-z', 'src/voice_agent_v2', 'scripts/run_slice6.py', 'scripts/silero_kseniya_worker.py', 'config', 'web', 'requirements-release', 'release/inputs', 'LICENSE', 'THIRD_PARTY_NOTICES.md'], { cwd: root }).split('\0').filter((name) => name && !excluded.has(name) && (!name.startsWith('config/') || allowedConfig.has(name)));
  if (!names.includes('src/voice_agent_v2/faster_whisper_runner.py') || !names.includes('web/package-lock.json')) fail('runtime_source_incomplete', 'production source closure lacks STT runner or web lock');
  for (const relative of names) {
    if (relative.includes('node_modules') || relative.startsWith('web/dist/')) continue;
    const source = path.join(root, relative); const destination = path.join(target, relative); const metadata = fs.lstatSync(source);
    if (!metadata.isFile() || metadata.isSymbolicLink() || metadata.nlink !== 1) fail('runtime_source_custody_invalid', 'tracked assembly source is not a regular file');
    const bytes = fs.readFileSync(source); const text = bytes.toString('latin1');
    if (text.includes(root) || text.includes(os.homedir()) || text.includes('/home/priney/') || /github_pat_|ghp_|-----BEGIN (?:ENCRYPTED )?PRIVATE KEY-----/.test(text)) fail('runtime_source_leak', 'assembly source contains a host path or secret marker');
    fs.mkdirSync(path.dirname(destination), { recursive: true, mode: 0o700 }); fs.writeFileSync(destination, bytes, { mode: 0o600 });
  }
}

function inspectBuilder(image, fetch, runner = run, options = {}) {
  const prefix = ['--remote=false']; const env = options.environment || isolatedPodmanEnvironment();
  if (fetch) runner('podman', [...prefix, 'pull', '--quiet', '--authfile', options.authFile, image], { env, timeout: 1800000, code: 'runtime_builder_unavailable', message: 'exact builder OCI image could not be acquired' });
  const observed = runner('podman', [...prefix, 'image', 'inspect', '--format', '{{.Digest}}', image], { env, code: 'runtime_builder_unavailable', message: 'exact builder OCI image is absent; rerun with --fetch' });
  const expected = `sha256:${BUILDER.exec(image)[1]}`;
  if (observed !== expected) fail('runtime_builder_identity_mismatch', 'local builder image differs from pinned manifest digest');
}

async function prepareToolClosure(values, context, state) {
  const { authority, authFile, build, cache, network, root, runner, shaRoot, source } = state;
  const npmCache = path.join(cache, 'npm'); fs.mkdirSync(npmCache, { mode: 0o700 }); fs.chmodSync(npmCache, 0o700);
  copyTrackedSource(root, source, runner); fs.mkdirSync(build, { mode: 0o700 });
  fs.writeFileSync(path.join(build, 'input-map.tsv'), authority.inputs.map((item) => `${item.sha256}\t${item.filename}`).sort().join('\n') + '\n', { mode: 0o600 });
  fs.writeFileSync(path.join(build, 'tool-authority.tsv'), toolAuthorityTsv(authority, root), { mode: 0o600 });
  const sourceEpoch = runner('git', ['show', '-s', '--format=%ct', 'HEAD'], { cwd: root }); const sourceCommit = runner('git', ['rev-parse', 'HEAD'], { cwd: root });
  const base = ['--remote=false', 'run', '--rm', '--userns=keep-id', '--env-host=false', '--http-proxy=false', '--cap-drop=all', '--security-opt=no-new-privileges', '--pids-limit=2048', '--memory=24g', '--cpus=12',
    '--env', 'HOME=/work/home', '--env', 'XDG_CONFIG_HOME=/work/config', '--env', 'XDG_CACHE_HOME=/work/xdg', '--env', 'NPM_CONFIG_USERCONFIG=/dev/null', '--env', 'NPM_CONFIG_GLOBALCONFIG=/dev/null', '--env', 'GIT_CONFIG_NOSYSTEM=1', '--env', 'GIT_CONFIG_GLOBAL=/dev/null',
    '--env', `SOURCE_DATE_EPOCH=${sourceEpoch}`, '--env', `VOICE_AGENT_BUILD_ID=${sourceCommit}`,
    '--volume', `${shaRoot}:/inputs:ro`, '--volume', `${source}:/source:ro`, '--volume', `${npmCache}:/npm-cache:rw`, '--volume', `${path.join(root, 'release', 'assemble-runtime.sh')}:/assembler:ro`];
  const sandbox = ['--read-only', '--tmpfs', '/tmp:rw,noexec,nosuid,size=4g', '--tmpfs', '/work:rw,nosuid,size=12g'];
  const image = authority.sources.builder.image; const environment = network.environment;
  runner('podman', [...base, '--volume', `${build}:/build:rw`, '--network=none', ...sandbox, image, '/bin/bash', '/assembler', 'tool-preflight'], { env: environment, timeout: 600000, code: 'runtime_tool_preflight_failed', message: 'network-disabled builder tool preflight could not complete' });
  try { inspectToolReport(path.join(build, 'tool-report.tsv'), authority, root, new Set(['builder', 'content'])); }
  catch (reason) { if (reason.code !== 'runtime_tool_closure_invalid') throw reason; inspectToolReport(path.join(build, 'tool-report.tsv'), authority, root); }
  if (values.fetch === true) runner('podman', [...base, '--volume', `${build}:/build:ro`, '--network=pasta', ...sandbox, image, '/bin/bash', '/assembler', 'web-acquire'], { env: environment, timeout: 1800000, code: 'web_dependency_unavailable', message: 'exact npm lock bytes could not be acquired' });
  runner('podman', [...base, '--volume', `${build}:/build:rw`, '--network=none', ...sandbox, image, '/bin/bash', '/assembler', 'web-prepare'], { env: environment, timeout: 1800000, code: 'web_dependency_unavailable', message: 'network-disabled exact npm tool installation could not complete' });
  const tools = inspectToolReport(path.join(build, 'tool-report.tsv'), authority, root);
  return { base, sandbox, sourceCommit, tools };
}

async function runPreflight(values, context, assembleOutput) {
  const root = context.root; const authority = validateAuthority(root); const cache = path.resolve(values.cache);
  const runner = context.runCommand || run; const inputPreparer = context.prepareInputs || prepareInputs; const builderInspector = context.inspectBuilder || inspectBuilder;
  const networkInspector = context.inspectAcquisitionNetwork || inspectAcquisitionNetwork;
  const network = networkInspector(runner, context.environment || process.env);
  fs.mkdirSync(cache, { recursive: true, mode: 0o700 }); fs.chmodSync(cache, 0o700);
  const policy = fs.mkdtempSync(path.join(cache, '.podman-policy-')); const authFile = path.join(policy, 'auth.json');
  fs.writeFileSync(authFile, '{}\n', { mode: 0o600 });
  try {
    builderInspector(authority.sources.builder.image, values.fetch === true, runner, { authFile, environment: network.environment });
    const selectedNames = new Set(authority.tools.content_addressed_tools.map((item) => item.input));
    const selected = authority.inputs.filter((item) => selectedNames.has(item.name));
    const shaRoot = await inputPreparer(cache, authority, values.fetch === true, fetchInput, selected);
    const temporary = fs.mkdtempSync(path.join(cache, '.preflight-')); const source = path.join(temporary, 'source'); const build = path.join(temporary, 'build');
    try {
      const prepared = await prepareToolClosure(values, context, { authority, authFile, build, cache, network, root, runner, shaRoot, source });
      if (!assembleOutput) return { authority, builder_image: authority.sources.builder.image, tool_authority_sha256: digest(Buffer.from(toolAuthorityTsv(authority, root))), tools: prepared.tools };
      await inputPreparer(cache, authority, values.fetch === true);
      fs.mkdirSync(assembleOutput, { mode: 0o700 });
      const outputBase = [...prepared.base, '--volume', `${build}:/build:ro`, '--volume', `${assembleOutput}:/output:rw`];
      runner('podman', [...outputBase, '--network=none', ...prepared.sandbox, authority.sources.builder.image, '/bin/bash', '/assembler', 'assemble'], { env: network.environment, timeout: 7200000 });
      if (!fs.existsSync(path.join(assembleOutput, 'runtime')) || !fs.existsSync(path.join(assembleOutput, 'web', 'index.html'))) fail('runtime_assembly_incomplete', 'builder did not emit runtime and static web closure');
      return { authority, output: assembleOutput, shaRoot, tool_authority_sha256: digest(Buffer.from(toolAuthorityTsv(authority, root))), tools: prepared.tools };
    } finally { fs.rmSync(temporary, { recursive: true, force: true }); }
  } finally { fs.rmSync(policy, { recursive: true, force: true }); }
}

async function preflight(values, context) {
  validatePreflightOptions(values);
  return runPreflight(values, context, null);
}

async function assemble(values, context) {
  validateAssembleOptions(values);
  const root = context.root; const output = path.resolve(values.output);
  if (output === root || output.startsWith(`${root}${path.sep}`)) fail('runtime_output_invalid', 'runtime assembly output must be outside the source checkout');
  if (fs.existsSync(output)) fail('runtime_output_exists', 'runtime assembly output already exists');
  return runPreflight(values, context, output);
}

module.exports = { AssemblyError, assemble, copyTrackedSource, digest, fetchInput, fixtureToolReport, inspectAcquisitionNetwork, inspectBuilder, inspectInput, inspectToolReport, isolatedPodmanEnvironment, preflight, prepareInputs, toolAuthorityTsv, validateAssembleOptions, validateAuthority, validateInputLocator, validatePreflightOptions };
