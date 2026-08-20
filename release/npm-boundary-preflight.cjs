'use strict';

const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

const PUBLIC_REGISTRY = 'https://registry.npmjs.org/';
const EXPECTED = Object.freeze({
  HOME: '/work/npm-home',
  NPM_CONFIG_CACHE: '/npm-cache',
  NPM_CONFIG_GLOBALCONFIG: '/work/npm-global/npmrc',
  NPM_CONFIG_PREFIX: '/work/npm-prefix',
  NPM_CONFIG_REGISTRY: PUBLIC_REGISTRY,
  NPM_CONFIG_USERCONFIG: '/work/npm-user/npmrc',
  TMPDIR: '/work/npm-temp',
  XDG_CACHE_HOME: '/work/xdg-cache',
  XDG_CONFIG_HOME: '/work/xdg-config',
});
const FACTS = Object.freeze({
  'config-identities': 'distinct-paths-and-inodes:no-links:private-boundary',
  environment: 'ambient-npm-yarn-pnpm-node-options-registry-auth-token-cert-proxy-denied',
  'global-config': 'regular:0600:single-link:empty:owner=current',
  'npm-behavior': 'npm:11.19.0:userconfig-exact:globalconfig-exact:registry-exact',
  'private-directories': 'cache-prefix-temp-home:distinct:0700:owner=current:no-links',
  registry: PUBLIC_REGISTRY,
  'user-config': 'regular:0600:single-link:empty:owner=current',
});

function forbiddenEnvironmentName(name) {
  const lower = name.toLowerCase();
  if (lower.startsWith('npm_') || lower.startsWith('yarn_') || lower.startsWith('pnpm_') || lower.startsWith('corepack_')) return true;
  if (lower === 'node_options' || lower === 'node_path' || lower === 'node_extra_ca_certs' || lower === 'node_tls_reject_unauthorized') return true;
  return lower.includes('registry') || lower.includes('auth') || lower.includes('token') || lower.includes('cert') || lower.includes('proxy')
    || lower.includes('no_proxy') || lower.includes('ca_bundle') || lower === 'ssl_cert_file' || lower === 'ssl_cert_dir' || lower === 'cafile';
}

function inspectPrivateNpmBoundary(options = {}) {
  const environment = options.environment || process.env;
  const expected = options.expected || EXPECTED;
  const expectedUid = options.expectedUid ?? process.getuid();
  const facts = new Map(Object.keys(FACTS).map((name) => [name, { evidence: FACTS[name], status: 'ok' }]));
  const mismatch = (name) => facts.set(name, { evidence: 'mismatch', status: 'mismatch' });
  const metadata = new Map();
  const inspect = (name, filename, type, mode) => {
    try {
      const item = fs.lstatSync(filename);
      const acceptedType = type === 'file' ? item.isFile() && !item.isSymbolicLink() && item.nlink === 1 && item.size === 0 : item.isDirectory() && !item.isSymbolicLink();
      if (!acceptedType || item.uid !== expectedUid || (item.mode & 0o777) !== mode || fs.realpathSync(filename) !== filename) mismatch(name);
      metadata.set(filename, item);
    } catch { mismatch(name); }
  };
  inspect('user-config', expected.NPM_CONFIG_USERCONFIG, 'file', 0o600);
  inspect('global-config', expected.NPM_CONFIG_GLOBALCONFIG, 'file', 0o600);
  for (const filename of [expected.NPM_CONFIG_CACHE, expected.NPM_CONFIG_PREFIX, expected.TMPDIR, expected.HOME]) inspect('private-directories', filename, 'directory', 0o700);

  const identityPaths = [expected.NPM_CONFIG_USERCONFIG, expected.NPM_CONFIG_GLOBALCONFIG, expected.NPM_CONFIG_CACHE, expected.NPM_CONFIG_PREFIX, expected.TMPDIR, expected.HOME];
  const identities = identityPaths.map((filename) => metadata.has(filename) ? `${metadata.get(filename).dev}:${metadata.get(filename).ino}` : 'missing');
  if (new Set(identityPaths).size !== identityPaths.length || new Set(identities).size !== identities.length || identityPaths.some((filename) => path.resolve(filename) !== filename)
    || identityPaths.some((filename) => options.privateRoot ? !filename.startsWith(`${options.privateRoot}${path.sep}`) : !(filename.startsWith('/work/') || filename === '/npm-cache'))) mismatch('config-identities');

  const allowedSensitive = new Set(['NPM_CONFIG_CACHE', 'NPM_CONFIG_GLOBALCONFIG', 'NPM_CONFIG_PREFIX', 'NPM_CONFIG_REGISTRY', 'NPM_CONFIG_USERCONFIG']);
  for (const [name, value] of Object.entries(environment)) {
    if (allowedSensitive.has(name)) {
      if (value !== expected[name]) mismatch(name === 'NPM_CONFIG_REGISTRY' ? 'registry' : 'environment');
    } else if (forbiddenEnvironmentName(name)) mismatch('environment');
  }
  for (const [name, value] of Object.entries(expected)) if (environment[name] !== value) mismatch(name === 'NPM_CONFIG_REGISTRY' ? 'registry' : 'environment');

  const runNpm = options.runNpm || ((args) => spawnSync('/build/tools/node-command', ['/build/tools/node/lib/node_modules/npm/bin/npm-cli.js', ...args], {
    encoding: 'utf8', env: environment, timeout: 30000,
  }));
  const probes = [
    [['--version'], '11.19.0'],
    [['config', 'get', 'userconfig'], expected.NPM_CONFIG_USERCONFIG],
    [['config', 'get', 'globalconfig'], expected.NPM_CONFIG_GLOBALCONFIG],
    [['config', 'get', 'registry'], PUBLIC_REGISTRY],
  ];
  for (const [args, expected] of probes) {
    let result; try { result = runNpm(args); } catch { mismatch('npm-behavior'); continue; }
    const stdout = typeof result.stdout === 'string' ? result.stdout : '';
    const stderr = typeof result.stderr === 'string' ? result.stderr : '';
    if (result.status !== 0 || Buffer.byteLength(stdout) > 1024 || Buffer.byteLength(stderr) > 1024 || stdout.trim() !== expected) mismatch('npm-behavior');
  }
  return facts;
}

function reportText(facts, provenance) {
  return Object.keys(FACTS).sort().map((name) => {
    const item = facts.get(name) || { evidence: 'absent', status: 'absent' };
    return `${provenance}\t${name}\t${item.evidence}\t${item.status}`;
  }).join('\n') + '\n';
}

function main() {
  const [authorityFile, reportFile] = process.argv.slice(2);
  if (!authorityFile || !reportFile) process.exit(64);
  let authority;
  try {
    authority = JSON.parse(fs.readFileSync(authorityFile, 'utf8'));
    if (authority.schema !== 'voice-agent.npm-boundary.v1' || typeof authority.provenance !== 'string' || authority.facts.join('\0') !== Object.keys(FACTS).sort().join('\0')) throw new Error('invalid');
  } catch { process.exit(65); }
  const facts = inspectPrivateNpmBoundary();
  fs.writeFileSync(reportFile, reportText(facts, authority.provenance), { mode: 0o600 });
  if ([...facts.values()].some((item) => item.status !== 'ok')) process.exitCode = 1;
}

if (require.main === module) main();
module.exports = { EXPECTED, FACTS, PUBLIC_REGISTRY, forbiddenEnvironmentName, inspectPrivateNpmBoundary, reportText };
