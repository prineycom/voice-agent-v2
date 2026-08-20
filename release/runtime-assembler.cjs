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
  exactKeys(sources, ['ambient_cache_allowed', 'builder', 'host_boundary', 'host_node_allowed', 'host_python_allowed', 'inputs', 'node_in_application_runtime', 'platform', 'schema', 'wheelhouse']);
  exactKeys(sources.builder, ['ambient_mounts', 'cuda_toolchain', 'glibc_floor', 'image', 'manifest_digest', 'network_during_build', 'platform', 'upstream_tag']);
  const match = BUILDER.exec(sources.builder.image);
  if (sources.schema !== 'voice-agent.runtime-sources.v1' || sources.platform !== 'linux-x86_64-nvidia' || !match
    || sources.builder.manifest_digest !== `sha256:${match[1]}` || sources.builder.platform !== 'linux/amd64'
    || sources.builder.glibc_floor !== '2.28' || sources.builder.cuda_toolchain !== '12.9.1'
    || sources.builder.network_during_build !== false || sources.builder.ambient_mounts !== false
    || sources.node_in_application_runtime !== false || sources.ambient_cache_allowed !== false
    || sources.host_python_allowed !== false || sources.host_node_allowed !== false) fail('runtime_assembly_authority_invalid', 'builder/runtime authority is invalid');
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
  return { sources, wheels, inputs };
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

async function prepareInputs(cacheRoot, authority, fetch, fetcher = fetchInput) {
  fs.mkdirSync(cacheRoot, { recursive: true, mode: 0o700 }); fs.chmodSync(cacheRoot, 0o700);
  const shaRoot = path.join(cacheRoot, 'sha256'); fs.mkdirSync(shaRoot, { recursive: true, mode: 0o700 }); fs.chmodSync(shaRoot, 0o700);
  for (const name of fs.readdirSync(shaRoot)) if (!SHA256.test(name) || !authority.inputs.some((item) => item.sha256 === name)) fail('ambient_cache_refused', 'assembly cache contains bytes outside release input authority');
  for (const item of authority.inputs) {
    const filename = path.join(shaRoot, item.sha256);
    if (inspectInput(filename, item)) continue;
    if (!fetch) fail('runtime_input_missing', 'an accepted immutable runtime input is absent; rerun with --fetch');
    await fetcher(item, filename);
    inspectInput(filename, item);
  }
  return shaRoot;
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

function inspectBuilder(image, fetch, runner = run) {
  if (fetch) runner('podman', ['pull', '--quiet', image], { timeout: 1800000, code: 'runtime_builder_unavailable', message: 'exact builder OCI image could not be acquired' });
  const observed = runner('podman', ['image', 'inspect', '--format', '{{.Digest}}', image], { code: 'runtime_builder_unavailable', message: 'exact builder OCI image is absent; rerun with --fetch' });
  const expected = `sha256:${BUILDER.exec(image)[1]}`;
  if (observed !== expected) fail('runtime_builder_identity_mismatch', 'local builder image differs from pinned manifest digest');
}

async function assemble(values, context) {
  const root = context.root; const authority = validateAuthority(root); const cache = path.resolve(values.cache); const output = path.resolve(values.output);
  const runner = context.runCommand || run; const inputPreparer = context.prepareInputs || prepareInputs; const builderInspector = context.inspectBuilder || inspectBuilder;
  if (output === root || output.startsWith(`${root}${path.sep}`)) fail('runtime_output_invalid', 'runtime assembly output must be outside the source checkout');
  if (fs.existsSync(output)) fail('runtime_output_exists', 'runtime assembly output already exists');
  const shaRoot = await inputPreparer(cache, authority, values.fetch === true);
  builderInspector(authority.sources.builder.image, values.fetch === true, runner);
  const temporary = fs.mkdtempSync(path.join(cache, '.assembly-')); const source = path.join(temporary, 'source'); const build = path.join(temporary, 'build');
  try {
    copyTrackedSource(root, source, runner); fs.mkdirSync(build, { mode: 0o700 }); fs.mkdirSync(output, { mode: 0o700 });
    const npmCache = path.join(cache, 'npm'); fs.mkdirSync(npmCache, { mode: 0o700 });
    fs.writeFileSync(path.join(build, 'input-map.tsv'), authority.inputs.map((item) => `${item.sha256}\t${item.filename}`).sort().join('\n') + '\n', { mode: 0o600 });
    const sourceEpoch = runner('git', ['show', '-s', '--format=%ct', 'HEAD'], { cwd: root }); const sourceCommit = runner('git', ['rev-parse', 'HEAD'], { cwd: root });
    const base = ['run', '--rm', '--userns=keep-id', '--cap-drop=all', '--security-opt=no-new-privileges', '--pids-limit=2048', '--memory=24g', '--cpus=12', '--env', `SOURCE_DATE_EPOCH=${sourceEpoch}`, '--env', `VOICE_AGENT_BUILD_ID=${sourceCommit}`,
      '--volume', `${shaRoot}:/inputs:ro`, '--volume', `${source}:/source:ro`, '--volume', `${build}:/build:rw`, '--volume', `${npmCache}:/npm-cache:rw`, '--volume', `${output}:/output:rw`, '--volume', `${path.join(root, 'release', 'assemble-runtime.sh')}:/assembler:ro`];
    if (values.fetch === true) runner('podman', [...base, '--network=slirp4netns', authority.sources.builder.image, '/bin/bash', '/assembler', 'web-acquire'], { timeout: 1800000, code: 'web_dependency_unavailable', message: 'exact Node/npm web inputs could not be acquired' });
    runner('podman', [...base, '--network=none', '--read-only', '--tmpfs', '/tmp:rw,noexec,nosuid,size=4g', '--tmpfs', '/work:rw,nosuid,size=12g', authority.sources.builder.image, '/bin/bash', '/assembler', 'assemble'], { timeout: 7200000 });
    if (!fs.existsSync(path.join(output, 'runtime')) || !fs.existsSync(path.join(output, 'web', 'index.html'))) fail('runtime_assembly_incomplete', 'builder did not emit runtime and static web closure');
    return { authority, output, shaRoot };
  } finally { fs.rmSync(temporary, { recursive: true, force: true }); }
}

module.exports = { AssemblyError, assemble, copyTrackedSource, digest, fetchInput, inspectBuilder, inspectInput, prepareInputs, validateAuthority, validateInputLocator };
