'use strict';

module.exports = function createInstaller(core) {
  let api;
  const crypto = require('node:crypto');
  const fs = require('node:fs');
  const os = require('node:os');
  const path = require('node:path');
  const { spawnSync } = require('node:child_process');
  const agentEnvironment = require('./agent-environment.cjs')(core);

  const SERVICE_UNIT = 'voice-agent.service';
  const REQUIRED_COMPONENTS = ['livekit', 'controller', 'stt', 'selected_llm', 'tts'];
  const REQUIRED_ARCHIVE_FILES = ['bin/voice-agent-runtime', 'descriptors/local-models.json', 'descriptors/runtime.json'];
  const FREE_SPACE_RESERVE = 8 * 1024 * 1024 * 1024;
  const MINIMUM_VRAM = 10 * 1024 * 1024 * 1024;
  const MINIMUM_NVIDIA_DRIVER = [550, 54, 0];
  const STARTUP_DEADLINE_MS = 300000;
  const UNIT_CONTRACT = `[Unit]\nDescription=Voice Agent\nAfter=network.target\nStartLimitIntervalSec=infinity\nStartLimitBurst=2\n\n[Service]\nType=notify\nWorkingDirectory=@RELEASE@\nEnvironmentFile=@PRIVATE@/service.env\nExecStart=@RELEASE@/bin/voice-agent-runtime --config @CONFIG@/config.yaml\nRestart=on-failure\nRestartSec=5s\nTimeoutStartSec=300s\nTimeoutStopSec=75s\nUMask=0077\nNoNewPrivileges=yes\nPrivateTmp=yes\nProtectSystem=strict\nProtectHome=read-only\nReadWritePaths=@DATA@ @CACHE@ @STATE@ @RUNTIME@\nRestrictAddressFamilies=AF_UNIX AF_INET AF_INET6\nLockPersonality=yes\nRestrictSUIDSGID=yes\n\n[Install]\nWantedBy=default.target\n`;
  const UNIT_CONTRACT_SHA256 = digest(Buffer.from(UNIT_CONTRACT));

  const AGENT_IMAGE = 'ghcr.io/prineycom/voice-agent-environment@sha256:8a5a972b25f7c203c71b8e28af17f756d9daf39bc74ebbc5d86eaf6c9f3da421';
  const AGENT_SPEC_DIGEST = `sha256:${digest(Buffer.from('voice-agent.agent-environment-spec.v1\\0' + AGENT_IMAGE))}`;

  function renderAgentConfig(layout = null, installationId = null, endpoint = null, enabled = false) {
    const quote = (value) => value === null ? 'null' : JSON.stringify(value);
    const registry = layout ? path.join(layout.agent, 'private', 'registry.json') : null;
    const rootfs = layout ? path.join(layout.agent, 'rootfs-storage') : null;
    const workspace = layout ? path.join(layout.agent, 'workspace') : null;
    const cache = layout ? path.join(layout.agent, 'cache') : null;
    return `schema_version: voice-agent.config.v2
agent:
  enabled: ${enabled}
  max_decisions: 24
  active_deadline_seconds: 600
  tools:
    - shell.exec
    - file.read
    - file.search
    - file.write
    - file.edit
    - file.patch
    - execute_code
    - process
    - receipt
    - web.search
    - web.fetch
    - web.extract
    - report.artifact
    - report.deliver
agent_environment:
  enabled: ${enabled}
  docker:
    endpoint: ${quote(endpoint)}
    authority: explicit_verified_rootless
  image:
    reference: ${AGENT_IMAGE}
    spec_digest: ${AGENT_SPEC_DIGEST}
    pull_at_runtime: false
  identity:
    installation_id: ${quote(installationId)}
    registry_path: ${quote(registry)}
    rootfs_storage_path: ${quote(rootfs)}
    workspace_path: ${quote(workspace)}
    cache_path: ${quote(cache)}
  lifecycle:
    lazy_create: true
    persistent: true
    restart_policy: "no"
    idle_action: none
    command_timeout_seconds: 120
    command_timeout_grace_seconds: 5
  additional_mounts: []
  network:
    enabled: false
    mode: rootless_private
    publish_ports: []
  credentials:
    store_reference: private/credentials.json
    creation_environment_names: []
    creation_file_names: []
    exec_environment_names: []
    exec_file_names: []
  resources:
    cpus: 2
    memory_mib: 4096
    pids: 128
    shm_mib: 1024
    rootfs_target_mib: 4096
    workspace_target_mib: 5120
    cache_target_mib: 2048
    host_free_reserve_mib: 8192
    watchdog_interval_seconds: 2
    maximum_output_bytes: 262144
    maximum_stream_bytes: 262144
    stream_timeout_seconds: 120
`;
  }
  const AGENT_CONFIG = renderAgentConfig();

  function error(code, message) { throw new core.LauncherError(code, message); }
  function digest(bytes) { return crypto.createHash('sha256').update(bytes).digest('hex'); }
  function exists(filename) { try { fs.lstatSync(filename); return true; } catch (reason) { if (reason.code === 'ENOENT') return false; throw reason; } }
  function syncDirectory(directory) { const descriptor = fs.openSync(directory, fs.constants.O_RDONLY | fs.constants.O_DIRECTORY); try { fs.fsyncSync(descriptor); } finally { fs.closeSync(descriptor); } }
  function timestamp(clock) { return clock.now().toISOString().replace('.000Z', 'Z'); }
  function lexicalVersion(left, right) {
    const parse = (value) => value.split('-', 1)[0].split('.').map(Number);
    const a = parse(left); const b = parse(right);
    for (let index = 0; index < 3; index += 1) if (a[index] !== b[index]) return a[index] - b[index];
    return left.includes('-') === right.includes('-') ? left.localeCompare(right) : left.includes('-') ? -1 : 1;
  }

  function defaultIdentity() {
    const user = os.userInfo();
    return {
      uid: process.geteuid(), username: user.username, home: user.homedir,
      dataHome: process.env.XDG_DATA_HOME || path.join(user.homedir, '.local', 'share'),
      configHome: process.env.XDG_CONFIG_HOME || path.join(user.homedir, '.config'),
      cacheHome: process.env.XDG_CACHE_HOME || path.join(user.homedir, '.cache'),
      stateHome: process.env.XDG_STATE_HOME || path.join(user.homedir, '.local', 'state'),
      runtimeHome: process.env.XDG_RUNTIME_DIR || `/run/user/${process.geteuid()}`,
    };
  }

  function cleanAbsolute(value, code = 'install_root_invalid') {
    if (typeof value !== 'string' || !path.isAbsolute(value) || path.normalize(value) !== value || value.includes('\0')) error(code, 'path is not a clean absolute path');
    return value;
  }

  function layoutFor(identity, testMode) {
    if (!testMode && identity !== undefined) error('test_injection_forbidden', 'production installation roots are fixed by the invoking user');
    const value = identity || defaultIdentity();
    for (const key of ['home', 'dataHome', 'configHome', 'cacheHome', 'stateHome', 'runtimeHome']) cleanAbsolute(value[key]);
    if (!Number.isSafeInteger(value.uid) || value.uid < 1 || typeof value.username !== 'string' || !/^[A-Za-z_][A-Za-z0-9_.-]{0,63}$/.test(value.username)) {
      error('host_user_invalid', 'the invoking service identity is unsupported');
    }
    const data = path.join(value.dataHome, 'voice-agent');
    const config = path.join(value.configHome, 'voice-agent');
    const cache = path.join(value.cacheHome, 'voice-agent');
    const state = path.join(value.stateHome, 'voice-agent');
    const runtime = path.join(value.runtimeHome, 'voice-agent');
    return {
      identity: value, data, config, cache, state, runtime,
      releases: path.join(data, 'releases'), transactions: path.join(data, 'transactions'), migrations: path.join(data, 'migrations'),
      appData: path.join(data, 'data'), agent: path.join(data, 'agent-environment'), agentRootfs: path.join(data, 'agent-environment', 'rootfs-storage'),
      private: path.join(config, 'private'), downloads: path.join(cache, 'downloads'), models: path.join(cache, 'models', 'sha256'), runtimes: path.join(cache, 'runtimes', 'sha256'),
      launchers: path.join(cache, 'launchers', 'sha256'), logs: path.join(state, 'logs'), diagnostics: path.join(state, 'diagnostics'), serviceRuntime: path.join(runtime, 'service'),
      unit: path.join(value.configHome, 'systemd', 'user', SERVICE_UNIT), journal: path.join(data, 'transactions', 'install.json'),
      updateJournal: path.join(data, 'transactions', 'update.json'), uninstallJournal: path.join(data, 'transactions', 'uninstall.json'), selfUpdateJournal: path.join(data, 'transactions', 'launcher-update.json'), updateResult: path.join(state, 'last-update.json'), lifecycleResult: path.join(state, 'last-lifecycle.json'), updateLock: path.join(runtime, 'update.lock'),
      launcher: path.join(value.home, '.local', 'bin', 'voice-agent'), channelReceipt: path.join(cache, 'channel-stable.json'), installRecord: path.join(data, 'install.json'), current: path.join(data, 'current'), rollback: path.join(data, 'rollback'),
    };
  }

  function rejectSymlinkAncestors(filename) {
    cleanAbsolute(filename);
    const parsed = path.parse(filename);
    let current = parsed.root;
    for (const part of filename.slice(parsed.root.length).split(path.sep).filter(Boolean)) {
      current = path.join(current, part);
      let metadata;
      try { metadata = fs.lstatSync(current); } catch (reason) { if (reason.code === 'ENOENT') return; throw reason; }
      if (metadata.isSymbolicLink()) error('path_custody_invalid', 'managed path has a linked component');
    }
  }

  function inspectManagedPath(filename, uid, expectedMode = null, kind = 'directory') {
    rejectSymlinkAncestors(filename);
    let metadata;
    try { metadata = fs.lstatSync(filename); } catch (reason) { if (reason.code === 'ENOENT') return null; error('path_custody_invalid', 'managed path custody is unavailable'); }
    if (metadata.isSymbolicLink() || metadata.uid !== uid || (kind === 'directory' && !metadata.isDirectory()) || (kind === 'file' && !metadata.isFile())
        || (expectedMode !== null && (metadata.mode & 0o777) !== expectedMode)) error('path_custody_invalid', 'managed path is foreign, linked, or has an unsafe mode');
    return metadata;
  }

  function ensurePrivateDirectory(directory, uid) {
    cleanAbsolute(directory);
    rejectSymlinkAncestors(directory);
    const missing = [];
    let current = directory;
    while (!exists(current)) { missing.push(current); const parent = path.dirname(current); if (parent === current) break; current = parent; }
    for (const item of missing.reverse()) {
      fs.mkdirSync(item, { mode: 0o700 });
      fs.chmodSync(item, 0o700);
      syncDirectory(path.dirname(item));
    }
    inspectManagedPath(directory, uid, 0o700);
  }

  function atomicWrite(filename, bytes, mode, uid) {
    const parent = path.dirname(filename);
    inspectManagedPath(parent, uid, 0o700);
    if (exists(filename)) inspectManagedPath(filename, uid, null, 'file');
    const temporary = path.join(parent, `.${path.basename(filename)}.${crypto.randomBytes(8).toString('hex')}.tmp`);
    let descriptor;
    try {
      descriptor = fs.openSync(temporary, fs.constants.O_WRONLY | fs.constants.O_CREAT | fs.constants.O_EXCL | (fs.constants.O_NOFOLLOW || 0), mode);
      fs.writeFileSync(descriptor, bytes); fs.fsyncSync(descriptor); fs.fchmodSync(descriptor, mode); fs.closeSync(descriptor); descriptor = undefined;
      fs.renameSync(temporary, filename); syncDirectory(parent);
    } finally {
      if (descriptor !== undefined) fs.closeSync(descriptor);
      try { fs.unlinkSync(temporary); } catch (reason) { if (reason.code !== 'ENOENT') throw reason; }
    }
    inspectManagedPath(filename, uid, mode, 'file');
  }

  function readPrivateJson(filename, uid) {
    const metadata = inspectManagedPath(filename, uid, 0o600, 'file');
    if (!metadata || metadata.nlink !== 1 || metadata.size > 262144) error('install_journal_invalid', 'private installation record is invalid');
    let descriptor;
    try {
      descriptor = fs.openSync(filename, fs.constants.O_RDONLY | (fs.constants.O_NOFOLLOW || 0));
      return JSON.parse(fs.readFileSync(descriptor, 'utf8'));
    } catch { error('install_journal_invalid', 'private installation record is invalid'); }
    finally { if (descriptor !== undefined) fs.closeSync(descriptor); }
  }

  function writeJson(filename, document, uid, mode = 0o600) { atomicWrite(filename, Buffer.from(`${core.canonicalJson(document)}\n`.trimEnd()), mode, uid); }

  function hostPreflight(facts) {
    if (!facts || facts.kernel !== 'linux' || typeof facts.kernel_release !== 'string' || facts.kernel_release.length < 1 || facts.architecture !== 'x86_64' || facts.systemd !== true) {
      error('host_unsupported', 'Cannot install Voice Agent runtime: Linux x86_64 with systemd is required. Nothing was installed; run voice-agent doctor.');
    }
    if (!facts.user || facts.user.uid < 1 || facts.user.name.length < 1 || typeof facts.user.home !== 'string' || !path.isAbsolute(facts.user.home)) error('host_user_invalid', 'a non-root service user is required');
    if (!facts.nvidia || facts.nvidia.available !== true || typeof facts.nvidia.gpu_name !== 'string' || facts.nvidia.gpu_name.length < 1 || facts.nvidia.runtime_compatible !== true || facts.nvidia.vram_bytes < MINIMUM_VRAM
        || !Array.isArray(facts.nvidia.devices) || !['gpu', 'control', 'uvm'].every((kind) => facts.nvidia.devices.includes(kind))) {
      error('nvidia_prerequisite_unavailable', 'a compatible NVIDIA GPU, driver, VRAM reserve, and GPU/control/UVM devices are required; no driver was changed');
    }
    const version = String(facts.nvidia.driver_version || '').split('.').map(Number);
    for (let index = 0; index < MINIMUM_NVIDIA_DRIVER.length; index += 1) {
      if ((version[index] || 0) > MINIMUM_NVIDIA_DRIVER[index]) break;
      if ((version[index] || 0) < MINIMUM_NVIDIA_DRIVER[index]) error('nvidia_driver_incompatible', 'the NVIDIA driver is older than the supported release minimum; no driver was changed');
    }
    return facts;
  }

  function artifactPreflight(manifest, release, archiveEntries, manifestBytes) {
    core.validateArchiveEntries(archiveEntries, manifest, manifestBytes);
    const entries = new Map(manifest.entries.map((entry) => [entry.path, entry]));
    for (const name of REQUIRED_ARCHIVE_FILES) {
      const entry = entries.get(name);
      if (!entry || entry.type !== 'file') error('release_incomplete', 'the verified release is missing an installation runtime or descriptor');
    }
    if (!/^05[0-7]{2}$/.test(entries.get('bin/voice-agent-runtime').mode)) error('release_incomplete', 'the runtime entry is not executable');
    if (manifest.service_template_sha256 !== UNIT_CONTRACT_SHA256) error('service_contract_mismatch', 'the release does not accept this launcher service contract');
    return {
      model_descriptor_sha256: entries.get('descriptors/local-models.json').sha256,
      runtime_descriptor_sha256: entries.get('descriptors/runtime.json').sha256,
    };
  }

  function compatibilityPreflight(facts, requirements, requiredBytes) {
    if (!facts.assets || facts.assets.model_descriptor_sha256 !== requirements.model_descriptor_sha256
        || facts.assets.runtime_descriptor_sha256 !== requirements.runtime_descriptor_sha256
        || facts.assets.model_available !== true || facts.assets.runtime_available !== true || facts.assets.runtime_compatible !== true) {
      error('runtime_assets_unavailable', 'the exact signed local model/runtime descriptors are unavailable or incompatible');
    }
    if (!Number.isSafeInteger(facts.free_bytes) || facts.free_bytes < requiredBytes) {
      error('insufficient_space', `installation needs ${requiredBytes} free bytes including reserve; ${facts.free_bytes || 0} are available`);
    }
  }

  function selectRelease(channel) {
    const compatible = channel.releases.filter((release) => release.platform === core.SUPPORTED_PLATFORM && release.minimum_launcher_protocol <= core.LAUNCHER_PROTOCOL);
    if (!compatible.length) error('platform_release_unavailable', 'the signed stable channel has no compatible Linux x86_64 NVIDIA release');
    return compatible.sort((left, right) => lexicalVersion(right.version, left.version))[0];
  }

  async function extractVerifiedArchive(root, manifest, manifestBytes, acquired) {
    const entries = manifest.entries;
    const manifestPath = path.join(root, 'release-manifest.json');
    fs.writeFileSync(manifestPath, manifestBytes, { mode: 0o444, flag: 'wx' }); fs.chmodSync(manifestPath, 0o444);
    for (const entry of entries.filter((item) => item.type === 'directory').sort((left, right) => left.path.split('/').length - right.path.split('/').length)) {
      const filename = path.join(root, ...entry.path.split('/'));
      fs.mkdirSync(filename, { mode: 0o700 }); fs.chmodSync(filename, 0o700);
    }
    for (const entry of entries.filter((item) => item.type === 'file')) {
      const bytes = await acquired.readEntry(entry.path);
      if (!Buffer.isBuffer(bytes) || bytes.length !== entry.size || digest(bytes) !== entry.sha256) error('archive_invalid', 'archive file bytes differ from the signed manifest');
      const filename = path.join(root, ...entry.path.split('/'));
      fs.writeFileSync(filename, bytes, { mode: Number.parseInt(entry.mode, 8), flag: 'wx' }); fs.chmodSync(filename, Number.parseInt(entry.mode, 8));
    }
    for (const entry of entries.filter((item) => item.type === 'hardlink')) {
      const filename = path.join(root, ...entry.path.split('/'));
      fs.linkSync(path.resolve(path.dirname(filename), entry.target), filename); fs.chmodSync(filename, Number.parseInt(entry.mode, 8));
    }
    for (const entry of entries.filter((item) => item.type === 'symlink')) fs.symlinkSync(entry.target, path.join(root, ...entry.path.split('/')));
    for (const entry of entries.filter((item) => item.type === 'directory').sort((left, right) => right.path.split('/').length - left.path.split('/').length)) {
      fs.chmodSync(path.join(root, ...entry.path.split('/')), Number.parseInt(entry.mode, 8));
    }
    syncDirectory(root);
  }

  function verifyExtractedTree(root, manifest, manifestBytes, uid, allowReleaseRecord = false) {
    const expected = new Map(manifest.entries.map((entry) => [entry.path, entry]));
    expected.set('release-manifest.json', { path: 'release-manifest.json', type: 'file', mode: '0444', size: manifestBytes.length, sha256: digest(manifestBytes), target: null });
    const observed = new Set();
    function walk(directory, prefix = '') {
      for (const name of fs.readdirSync(directory).sort()) {
        const relative = prefix ? `${prefix}/${name}` : name;
        const filename = path.join(directory, name);
        const metadata = fs.lstatSync(filename);
        const entry = expected.get(relative);
        if (allowReleaseRecord && relative === 'release-record.json') {
          if (!metadata.isFile() || metadata.uid !== uid || metadata.nlink !== 1 || (metadata.mode & 0o777) !== 0o400) error('extracted_release_invalid', 'release metadata custody differs');
          continue;
        }
        if (!entry || metadata.uid !== uid || metadata.isSocket() || metadata.isFIFO() || metadata.isCharacterDevice() || metadata.isBlockDevice()) error('extracted_release_invalid', 'extraction produced undeclared or unsafe output');
        observed.add(relative);
        if ((metadata.mode & 0o777) !== Number.parseInt(entry.mode, 8)) error('extracted_release_invalid', 'extracted mode differs from the manifest');
        if (entry.type === 'directory') { if (!metadata.isDirectory()) error('extracted_release_invalid', 'extracted type differs'); walk(filename, relative); }
        else if (entry.type === 'file') { if (!metadata.isFile() || metadata.nlink !== 1 || metadata.size !== entry.size || digest(fs.readFileSync(filename)) !== entry.sha256) error('extracted_release_invalid', 'extracted file differs'); }
        else if (entry.type === 'symlink') { if (!metadata.isSymbolicLink() || fs.readlinkSync(filename) !== entry.target) error('extracted_release_invalid', 'extracted link differs'); }
        else if (entry.type === 'hardlink') {
          const target = path.resolve(path.dirname(filename), entry.target);
          const targetMeta = fs.lstatSync(target);
          if (!metadata.isFile() || metadata.ino !== targetMeta.ino || metadata.dev !== targetMeta.dev) error('extracted_release_invalid', 'extracted hardlink differs');
        }
      }
    }
    walk(root);
    if (observed.size !== expected.size || [...expected.keys()].some((name) => !observed.has(name))) error('extracted_release_invalid', 'extracted release is incomplete');
  }

  function safeDefaults(randomBytes, layout = null, installationId = null, endpoint = null) {
    const secret = (bytes) => randomBytes(bytes).toString('base64url');
    return {
      config: Buffer.from(renderAgentConfig(layout, installationId, endpoint, false)),
      service: Buffer.from([
        `LIVEKIT_API_KEY=${secret(24)}`, `LIVEKIT_API_SECRET=${secret(48)}`, `VOICE_AGENT_API_SECRET=${secret(48)}`,
        'LIVEKIT_INTERNAL_URL=ws://127.0.0.1:7880', 'LIVEKIT_PUBLIC_URL=ws://127.0.0.1:7880',
        'SLICE6_APP_PUBLIC_URL=http://127.0.0.1:8000', 'VOICE_AGENT_LISTEN_HOST=127.0.0.1',
        'VOICE_AGENT_PROVIDER=local', 'VOICE_AGENT_AUTOMATIC_FALLBACK=false', 'VOICE_AGENT_DIAGNOSTIC_CONTENT_CAPTURE=false',
        'VOICE_AGENT_AGENT_RUN_ENABLED=false', 'VOICE_AGENT_TELEGRAM_ENABLED=false', 'VOICE_AGENT_DOCKER_HOST=',
      ].join('\n') + '\n'),
      credentials: Buffer.from(core.canonicalJson({ environment: {}, files: {}, schema_version: 'voice-agent.credentials.v1' })),
    };
  }

  function installDirectories(layout) {
    const uid = layout.identity.uid;
    for (const directory of [layout.data, layout.releases, layout.transactions, layout.migrations, layout.appData,
      layout.agent, path.join(layout.agent, 'private'), path.join(layout.agent, 'workspace'), path.join(layout.agent, 'cache'), layout.agentRootfs,
      layout.config, layout.private, layout.cache, layout.downloads, path.dirname(layout.models), layout.models, path.dirname(layout.runtimes), layout.runtimes,
      path.dirname(layout.launchers), layout.launchers, layout.state, layout.logs, layout.diagnostics, layout.runtime, layout.serviceRuntime, path.dirname(layout.unit)]) ensurePrivateDirectory(directory, uid);
  }

  function renderUnit(layout, releaseRoot) {
    const replacements = { '@RELEASE@': releaseRoot, '@PRIVATE@': layout.private, '@CONFIG@': layout.config, '@DATA@': layout.data, '@CACHE@': layout.cache, '@STATE@': layout.state, '@RUNTIME@': layout.runtime };
    let output = UNIT_CONTRACT;
    for (const [token, value] of Object.entries(replacements)) {
      if (/\s/.test(value)) error('systemd_path_unsupported', 'XDG paths containing whitespace are unsupported by protocol 1');
      output = output.split(token).join(value);
    }
    return Buffer.from(output);
  }

  function transactionDocument(base, phase, clock, reason_code = null) {
    return {
      schema: 'voice-agent.install-transaction.v1', id: base.id, phase, release_id: base.release_id,
      version: base.version, build_id: base.build_id, channel_sequence: base.channel_sequence,
      artifact_sha256: base.artifact_sha256, started_at: base.started_at, updated_at: timestamp(clock), reason_code,
    };
  }

  function validateJournal(document, release) {
    const keys = ['artifact_sha256', 'build_id', 'channel_sequence', 'id', 'phase', 'reason_code', 'release_id', 'schema', 'started_at', 'updated_at', 'version'];
    const phases = new Set(['layout_created', 'defaults_written', 'release_staged', 'release_promoted', 'linger_enabled', 'unit_installed', 'service_started', 'ready_verified', 'healthy', 'failed']);
    const timePattern = /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$/;
    if (!document || Object.keys(document).sort().join('\0') !== keys.sort().join('\0') || document.schema !== 'voice-agent.install-transaction.v1'
        || document.release_id !== `${release.version}-${release.artifact_sha256.slice(0, 12)}` || document.version !== release.version
        || document.build_id !== release.build_id || document.artifact_sha256 !== release.artifact_sha256 || document.channel_sequence < 1
        || !phases.has(document.phase) || !timePattern.test(document.started_at) || !timePattern.test(document.updated_at)
        || (document.reason_code !== null && typeof document.reason_code !== 'string') || !/^[0-9a-f]{32}$/.test(document.id)) {
      error('install_journal_invalid', 'the incomplete install does not match the signed release; run voice-agent doctor');
    }
    if (document.phase === 'failed') error('install_recovery_required', 'a prior install failed; run voice-agent doctor before retrying');
    return document;
  }

  function writeJournal(layout, base, phase, dependencies, reason = null) {
    const document = transactionDocument(base, phase, dependencies.clock, reason);
    writeJson(layout.journal, document, layout.identity.uid);
    if (dependencies.fault) dependencies.fault.afterDurablePhase(phase);
    return document;
  }

  function validateReadiness(value, release, uid) {
    const health = value && value.runtime && value.runtime.health;
    const components = health && health.components;
    const names = Array.isArray(components) ? components.map((item) => item.component) : [];
    const listener = value && value.listener;
    return Boolean(value.service_active === true && value.service_enabled === true && value.process_uid === uid
      && value.runtime.release_id === `${release.version}-${release.artifact_sha256.slice(0, 12)}` && value.runtime.build_id === release.build_id
      && value.runtime.accepting === true && value.runtime.provider === 'local' && value.runtime.automatic_fallback === false
      && health.overall_readiness === 'ready' && components.length === 5 && new Set(names).size === 5
      && components.every((item) => REQUIRED_COMPONENTS.includes(item.component) && item.liveness === 'alive' && item.readiness === 'ready' && item.compatible === true)
      && listener && listener.host === '127.0.0.1' && listener.port === 8000 && listener.owner_uid === uid && listener.owner === 'service');
  }

  async function waitForReadiness(serviceOwner, release, uid, clock) {
    const started = clock.monotonic();
    let last = null;
    while (clock.monotonic() - started <= STARTUP_DEADLINE_MS) {
      last = await serviceOwner.probe({ release_id: `${release.version}-${release.artifact_sha256.slice(0, 12)}`, build_id: release.build_id, deadline_ms: STARTUP_DEADLINE_MS });
      if (validateReadiness(last, release, uid)) return last;
      if (clock.monotonic() - started === STARTUP_DEADLINE_MS) break;
      await clock.sleep(Math.min(1000, STARTUP_DEADLINE_MS - (clock.monotonic() - started)));
    }
    error('candidate_not_ready', 'the exact release did not become five-component ready within 300 seconds');
  }

  function anyManagedState(layout) { return [layout.data, layout.config, layout.cache, layout.state, layout.runtime, layout.unit].some(exists); }

  function validateExistingInstall(layout, release, manifest, manifestBytes) {
    const uid = layout.identity.uid;
    if (!exists(layout.installRecord) || !exists(layout.current)) return null;
    const record = readPrivateJson(layout.installRecord, uid);
    const releaseId = `${release.version}-${release.artifact_sha256.slice(0, 12)}`;
    const oldKeys = ['artifact_sha256', 'build_id', 'channel', 'channel_sequence', 'healthy_release', 'installation_id', 'installed_at', 'launcher_protocol', 'release_id', 'rollback_release', 'schema', 'version'];
    if (Object.keys(record).sort().join('\0') === oldKeys.sort().join('\0')) record.channel_authority_sha256 = null;
    const keys = [...oldKeys, 'channel_authority_sha256'];
    if (Object.keys(record).sort().join('\0') !== keys.sort().join('\0') || record.schema !== 'voice-agent.installation.v1'
        || !/^[0-9a-f]{32}$/.test(record.installation_id) || !Number.isSafeInteger(record.channel_sequence) || record.channel_sequence < 1
        || record.launcher_protocol !== core.LAUNCHER_PROTOCOL || record.release_id !== releaseId || record.healthy_release !== releaseId
        || (record.rollback_release !== null && !/^[0-9A-Za-z][0-9A-Za-z.+-]{0,95}$/.test(record.rollback_release))
        || (record.channel_authority_sha256 !== null && !/^[0-9a-f]{64}$/.test(record.channel_authority_sha256))
        || record.version !== release.version || record.build_id !== release.build_id
        || record.artifact_sha256 !== release.artifact_sha256 || record.channel !== 'stable' || !/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$/.test(record.installed_at)) {
      error('existing_install_requires_doctor', 'an existing installation differs or is partial; run voice-agent doctor');
    }
    const pointer = fs.lstatSync(layout.current);
    if (!pointer.isSymbolicLink() || fs.readlinkSync(layout.current) !== `releases/${releaseId}`) error('existing_install_requires_doctor', 'the selected release is ambiguous; run voice-agent doctor');
    if (record.rollback_release !== null) {
      const rollback = fs.lstatSync(layout.rollback);
      if (!rollback.isSymbolicLink() || fs.readlinkSync(layout.rollback) !== `releases/${record.rollback_release}`) error('existing_install_requires_doctor', 'the recorded rollback release is ambiguous; run voice-agent doctor');
      inspectManagedPath(path.join(layout.releases, record.rollback_release), uid, 0o500);
    }
    const releaseRoot = path.join(layout.releases, releaseId);
    inspectManagedPath(releaseRoot, uid, 0o500);
    verifyExtractedTree(releaseRoot, manifest, manifestBytes, uid, true);
    const releaseRecord = core.validateReleaseRecord(JSON.parse(fs.readFileSync(path.join(releaseRoot, 'release-record.json'), 'utf8')));
    if (releaseRecord.release_id !== releaseId || releaseRecord.readiness.state !== 'ready') error('existing_install_requires_doctor', 'the installed release receipt is not healthy; run voice-agent doctor');
    return record;
  }

  async function installVoiceAgent(options = {}) {
    const testMode = options.testMode === true;
    if (!testMode && options.dependencies) error('test_injection_forbidden', 'host and service injection is test-only');
    const dependencies = options.dependencies || defaultDependencies();
    const layout = layoutFor(testMode ? dependencies.identity : undefined, testMode);
    const output = dependencies.output || { info() {} };
    let journalBase = null;
    let serviceMutated = false;
    let releaseRoot = null;
    try {
      const baseFacts = hostPreflight(await dependencies.host.inspectBase());
      if (baseFacts.user.uid !== layout.identity.uid || baseFacts.user.name !== layout.identity.username || baseFacts.user.home !== layout.identity.home) error('host_user_invalid', 'host and invoking service identities differ');
      for (const root of [layout.data, layout.config, layout.cache, layout.state, layout.runtime]) if (exists(root)) inspectManagedPath(root, layout.identity.uid, 0o700);
      if (exists(layout.unit)) inspectManagedPath(layout.unit, layout.identity.uid, 0o600, 'file');
      const signed = await dependencies.source.acquireChannel();
      if (!signed || !signed.channelBytes || !signed.signatureBytes || !signed.publicKeyPem) error('channel_unavailable', 'signed stable channel metadata is unavailable; nothing was installed');
      let trustedSequence = 0;
      if (exists(layout.installRecord)) { try { trustedSequence = readPrivateJson(layout.installRecord, layout.identity.uid).channel_sequence || 0; } catch { error('existing_install_requires_doctor', 'existing installation metadata is invalid; run voice-agent doctor'); } }
      else if (exists(layout.journal)) { trustedSequence = readPrivateJson(layout.journal, layout.identity.uid).channel_sequence || 0; }
      const channel = core.verifySignedChannel(signed.channelBytes, signed.signatureBytes, signed.publicKeyPem, { now: dependencies.clock.now(), trustedSequence });
      const channelAuthority = digest(Buffer.from(signed.publicKeyPem));
      const release = selectRelease(channel);
      const acquired = await dependencies.source.acquireArtifact(release);
      if (!acquired || !acquired.artifactBytes || !acquired.manifestBytes || !acquired.archiveEntries || typeof acquired.readEntry !== 'function') error('artifact_unavailable', 'the authorized platform artifact is unavailable');
      if (acquired.redirected === true || (acquired.url && acquired.url !== release.artifact_url)) error('artifact_redirect_refused', 'program artifact redirect or changed authority was refused');
      const manifest = core.verifyPlatformArtifact(acquired.artifactBytes, acquired.manifestBytes, release);
      const requirements = artifactPreflight(manifest, release, acquired.archiveEntries, acquired.manifestBytes);
      const payloadBytes = manifest.entries.reduce((total, entry) => total + (entry.type === 'file' ? entry.size : 0), 0);
      const facts = await dependencies.host.inspectCompatibility({ release, manifest, requirements, layout });
      const assetManager = core.loadAssetCache(api);
      const descriptors = assetManager.validateDescriptors(release.assets);
      const requiredAssetBytes = descriptors.filter((item) => item.reachability === 'required' && item.kind !== 'agent_environment_image').reduce((sum, item) => sum + item.size, 0);
      const descriptorReserve = Math.max(0, ...descriptors.map((item) => item.required_free_space_reserve), release.launcher ? release.launcher.required_free_space_reserve : 0);
      compatibilityPreflight(facts, requirements, FREE_SPACE_RESERVE + descriptorReserve + (release.artifact_bytes * 2) + payloadBytes + requiredAssetBytes + (release.launcher ? release.launcher.size * 2 : 0) + 4 * 1024 * 1024);

      if (anyManagedState(layout)) {
        if (exists(layout.journal)) {
          inspectManagedPath(layout.data, layout.identity.uid, 0o700);
          journalBase = validateJournal(readPrivateJson(layout.journal, layout.identity.uid), release);
        } else {
          const existing = validateExistingInstall(layout, release, manifest, acquired.manifestBytes);
          if (!existing) error('existing_install_requires_doctor', 'existing Voice Agent paths are foreign or partial; run voice-agent doctor');
          const ready = await dependencies.service.probe({ release_id: existing.release_id, build_id: existing.build_id, deadline_ms: 0 });
          if (!validateReadiness(ready, release, layout.identity.uid)) error('existing_install_requires_doctor', 'the installed release is not exactly healthy; run voice-agent doctor');
          output.info(`Voice Agent ${release.version} is already selected, running, and five-component ready.`);
          output.info('Optional agent tools: unavailable until valid v2 configuration and a rootless Docker endpoint are configured. Telegram: disabled.');
          return { state: 'already_healthy', release_id: existing.release_id, version: release.version, optional: { agent_environment: 'disabled', telegram: 'disabled' } };
        }
      }

      const releaseId = `${release.version}-${release.artifact_sha256.slice(0, 12)}`;
      releaseRoot = path.join(layout.releases, releaseId);
      if (!journalBase) {
        journalBase = {
          id: dependencies.randomBytes(16).toString('hex'), release_id: releaseId, version: release.version, build_id: release.build_id,
          channel_sequence: channel.sequence, artifact_sha256: release.artifact_sha256, started_at: timestamp(dependencies.clock),
        };
        installDirectories(layout);
        writeJournal(layout, journalBase, 'layout_created', dependencies);
      } else installDirectories(layout);
      writeJson(layout.channelReceipt, { schema: 'voice-agent.cached-channel.v1', channel_base64: Buffer.from(signed.channelBytes).toString('base64'), signature_base64: Buffer.from(signed.signatureBytes).toString('base64'), public_key_base64: Buffer.from(signed.publicKeyPem).toString('base64'), authority_sha256: channelAuthority, sequence: channel.sequence, expires_at: channel.expires_at, verified_at: timestamp(dependencies.clock) }, layout.identity.uid);
      const assetOutcome = await assetManager.reconcile(layout, descriptors, journalBase.id, dependencies.source, {
        offline: false, applicationProtocol: manifest.application_protocol.minimum,
        imageInspector: dependencies.host.inspectAgentImage ? (descriptor) => dependencies.host.inspectAgentImage(descriptor) : null,
      });
      if (assetOutcome.outcomes.some((item) => ['optional_unavailable', 'stale_spec'].includes(item.state))) output.info('Optional AgentEnvironment image/spec is stale or unavailable; persistent environment state was not pulled, rebuilt, or stopped.');
      if (release.launcher) {
        let launcherOutcome;
        do { launcherOutcome = await assetManager.acquire(layout, release.launcher, journalBase.id, dependencies.source, { applicationProtocol: manifest.application_protocol.minimum }); } while (launcherOutcome.state === 'partial');
      }
      const programDescriptor = assetManager.programDescriptor(release);
      let programOutcome;
      do { programOutcome = await assetManager.acquire(layout, programDescriptor, journalBase.id, dependencies.source, { applicationProtocol: 1 }); } while (programOutcome.state === 'partial');
      if (digest(core.readOwnedRegular(assetManager.cachePath(layout, programDescriptor), layout.identity.uid, [0o400], release.artifact_bytes)) !== release.artifact_sha256) error('artifact_cache_invalid', 'cached program artifact digest differs');
      let phase = readPrivateJson(layout.journal, layout.identity.uid).phase;
      if (['linger_enabled', 'unit_installed', 'service_started', 'ready_verified', 'healthy'].includes(phase)) serviceMutated = true;

      if (phase === 'layout_created') {
        agentEnvironment.migrateLegacy(layout, journalBase.id, writeJson);
        const defaults = safeDefaults(dependencies.randomBytes, layout, journalBase.id, null);
        atomicWrite(path.join(layout.config, 'config.yaml'), defaults.config, 0o600, layout.identity.uid);
        atomicWrite(path.join(layout.private, 'service.env'), defaults.service, 0o600, layout.identity.uid);
        atomicWrite(path.join(layout.private, 'credentials.json'), defaults.credentials, 0o600, layout.identity.uid);
        writeJson(path.join(layout.private, 'docker-endpoint.json'), agentEnvironment.endpointRecord(layout.identity.uid), layout.identity.uid);
        const environmentBefore = await agentEnvironment.capture(layout, dependencies, { phase: 'install_before' });
        agentEnvironment.writePreservation(layout, { ...environmentBefore, checked_at: timestamp(dependencies.clock) }, writeJson);
        phase = writeJournal(layout, journalBase, 'defaults_written', dependencies).phase;
      }

      const stage = path.join(layout.transactions, `stage-${journalBase.id}`);
      if (phase === 'defaults_written') {
        if (exists(stage)) error('install_stage_ambiguous', 'the recorded extraction stage is ambiguous; run voice-agent doctor');
        fs.mkdirSync(stage, { mode: 0o700 }); fs.chmodSync(stage, 0o700);
        await extractVerifiedArchive(stage, manifest, acquired.manifestBytes, acquired);
        verifyExtractedTree(stage, manifest, acquired.manifestBytes, layout.identity.uid);
        const releaseRecord = {
          schema: 'voice-agent.release-record.v1', release_id: releaseId, version: release.version, build_id: release.build_id,
          platform: release.platform, channel: 'stable', channel_sequence: journalBase.channel_sequence, artifact_sha256: release.artifact_sha256,
          artifact_bytes: release.artifact_bytes, manifest_sha256: release.manifest_sha256, launcher_protocol: core.LAUNCHER_PROTOCOL,
          application_protocol: manifest.application_protocol, data_schema: manifest.data_schema, service_template_sha256: manifest.service_template_sha256,
          asset_digests: descriptors.map((item) => item.sha256).sort(), launcher_sha256: release.launcher ? release.launcher.sha256 : null,
          verified_at: timestamp(dependencies.clock), readiness: { state: 'not_verified', checked_at: null },
        };
        atomicWrite(path.join(stage, 'release-record.json'), Buffer.from(core.canonicalJson(releaseRecord)), 0o600, layout.identity.uid);
        phase = writeJournal(layout, journalBase, 'release_staged', dependencies).phase;
      }

      if (phase === 'release_staged') {
        if (exists(releaseRoot)) error('release_target_ambiguous', 'the immutable release target already exists; run voice-agent doctor');
        fs.renameSync(stage, releaseRoot); syncDirectory(layout.releases);
        phase = writeJournal(layout, journalBase, 'release_promoted', dependencies).phase;
      }

      if (phase === 'release_promoted') {
        output.info(`Voice Agent ${release.version} requires login linger so its user service starts at boot; enabling it only for ${layout.identity.username}.`);
        await dependencies.service.enableLinger({ username: layout.identity.username, uid: layout.identity.uid, command: ['sudo', '-n', 'loginctl', 'enable-linger', layout.identity.username] });
        serviceMutated = true;
        phase = writeJournal(layout, journalBase, 'linger_enabled', dependencies).phase;
      }

      if (phase === 'linger_enabled') {
        const unit = renderUnit(layout, releaseRoot);
        await dependencies.service.installUnit({ path: layout.unit, bytes: unit, mode: 0o600, uid: layout.identity.uid });
        serviceMutated = true;
        phase = writeJournal(layout, journalBase, 'unit_installed', dependencies).phase;
      }

      if (phase === 'unit_installed') {
        await dependencies.service.enableAndStart({ unit: SERVICE_UNIT, start_once: true });
        serviceMutated = true;
        phase = writeJournal(layout, journalBase, 'service_started', dependencies).phase;
      }

      if (phase === 'service_started') {
        await waitForReadiness(dependencies.service, release, layout.identity.uid, dependencies.clock);
        phase = writeJournal(layout, journalBase, 'ready_verified', dependencies).phase;
      }

      if (phase === 'ready_verified') {
        const environmentBefore = agentEnvironment.readPreservation(layout.data, layout.identity.uid)
          || await agentEnvironment.capture(layout, dependencies, { phase: 'install_before_recovery' });
        const environmentAfter = await agentEnvironment.capture(layout, dependencies, { phase: 'install_after' });
        agentEnvironment.assertPreserved(environmentBefore, environmentAfter);
        agentEnvironment.writePreservation(layout, { ...environmentAfter, checked_at: timestamp(dependencies.clock) }, writeJson);
        const recordPath = path.join(releaseRoot, 'release-record.json');
        const releaseRecord = core.validateReleaseRecord(JSON.parse(fs.readFileSync(recordPath, 'utf8')));
        releaseRecord.readiness = { state: 'ready', checked_at: timestamp(dependencies.clock) };
        atomicWrite(recordPath, Buffer.from(core.canonicalJson(releaseRecord)), 0o400, layout.identity.uid);
        fs.chmodSync(releaseRoot, 0o500); syncDirectory(layout.releases);
        const pointerTemp = path.join(layout.data, `.current.${journalBase.id}`);
        fs.symlinkSync(`releases/${releaseId}`, pointerTemp); fs.renameSync(pointerTemp, layout.current); syncDirectory(layout.data);
        writeJson(layout.installRecord, {
          schema: 'voice-agent.installation.v1', installation_id: journalBase.id, channel: 'stable', channel_sequence: journalBase.channel_sequence,
          launcher_protocol: core.LAUNCHER_PROTOCOL, channel_authority_sha256: channelAuthority, release_id: releaseId, healthy_release: releaseId, rollback_release: null,
          version: release.version, build_id: release.build_id, artifact_sha256: release.artifact_sha256, installed_at: timestamp(dependencies.clock),
        }, layout.identity.uid);
        phase = writeJournal(layout, journalBase, 'healthy', dependencies).phase;
      }

      if (phase === 'healthy') {
        const finalReady = await dependencies.service.probe({ release_id: releaseId, build_id: release.build_id, deadline_ms: 0 });
        if (!validateReadiness(finalReady, release, layout.identity.uid)) error('candidate_not_ready', 'the committed candidate is not exact-ready; run voice-agent doctor');
        fs.unlinkSync(layout.journal); syncDirectory(layout.transactions);
      }
      if (release.launcher) {
        const replacement = core.loadSelfUpdater().replace(layout, release.launcher, journalBase.id, releaseId, dependencies.selfUpdate || {});
        if (replacement.state === 'old_launcher_restored') error('launcher_update_failed_safe', 'application install is durably healthy but launcher replacement failed and exact old launcher was restored');
      }

      output.info(`Voice Agent ${release.version} is installed, running, accepting admission, and five-component ready on http://127.0.0.1:8000.`);
      output.info('Optional agent tools: unavailable until valid v2 configuration and a rootless Docker endpoint are configured. Telegram: disabled.');
      return { state: 'installed_healthy', release_id: releaseId, version: release.version, optional: { agent_environment: 'disabled', telegram: 'disabled' } };
    } catch (reason) {
      if (reason && reason.code === 'install_interrupted') throw reason;
      if (journalBase && exists(layout.journal)) {
        if (serviceMutated) {
          try { await dependencies.service.stop({ unit: SERVICE_UNIT }); } catch {}
          try { await dependencies.service.disable({ unit: SERVICE_UNIT }); } catch {}
        }
        try { if (exists(layout.current)) fs.unlinkSync(layout.current); } catch {}
        try { if (releaseRoot && exists(releaseRoot) && (fs.lstatSync(releaseRoot).mode & 0o777) === 0o500) fs.chmodSync(releaseRoot, 0o700); } catch {}
        try { writeJson(layout.journal, transactionDocument(journalBase, 'failed', dependencies.clock, reason.code || 'install_failed'), layout.identity.uid); } catch {}
      }
      throw reason;
    }
  }

  class SystemClock {
    now() { return new Date(); }
    monotonic() { return Number(process.hrtime.bigint() / 1000000n); }
    sleep(milliseconds) { return new Promise((resolve) => setTimeout(resolve, milliseconds)); }
  }

  class SystemHost {
    async inspectBase() {
      const user = os.userInfo();
      const systemd = exists('/run/systemd/system') && spawnSync('systemctl', ['--version'], { encoding: 'utf8', timeout: 2000, env: { PATH: '/usr/bin:/bin' } }).status === 0;
      const query = spawnSync('nvidia-smi', ['--query-gpu=name,driver_version,memory.total', '--format=csv,noheader,nounits'], { encoding: 'utf8', timeout: 3000, env: { PATH: '/usr/bin:/bin' } });
      const first = query.status === 0 ? query.stdout.trim().split('\n')[0].split(',').map((item) => item.trim()) : [];
      const deviceFacts = [['gpu', '/dev/nvidia0'], ['control', '/dev/nvidiactl'], ['uvm', '/dev/nvidia-uvm']].filter(([, filename]) => { try { return fs.lstatSync(filename).isCharacterDevice(); } catch { return false; } }).map(([kind]) => kind);
      return { kernel: os.platform(), kernel_release: os.release(), architecture: os.arch(), systemd, user: { uid: process.geteuid(), name: user.username, home: user.homedir }, nvidia: { available: query.status === 0, gpu_name: first[0], runtime_compatible: query.status === 0, driver_version: first[1], vram_bytes: Math.floor(Number(first[2] || 0) * 1024 * 1024), devices: deviceFacts } };
    }
    async inspectCompatibility({ requirements, layout }) {
      let directory = layout.data;
      while (!exists(directory)) directory = path.dirname(directory);
      const space = fs.statfsSync(directory);
      return { free_bytes: Number(space.bavail) * Number(space.bsize), assets: { ...requirements, model_available: false, runtime_available: false, runtime_compatible: false } };
    }
  }

  class UnprovisionedSource {
    async acquireChannel() { error('release_authority_unprovisioned', 'production signing-key/channel publication is not provisioned in this repository build; nothing was installed'); }
    async acquireArtifact() { error('artifact_unavailable', 'release artifact unavailable'); }
  }

  class SystemService {
    constructor() { this.probeOwner = new core.SystemServiceProbe(); }
    async enableLinger({ username }) {
      const result = spawnSync('sudo', ['-n', 'loginctl', 'enable-linger', username], { encoding: 'utf8', timeout: 10000, env: { PATH: '/usr/bin:/bin', LANG: 'C.UTF-8', LC_ALL: 'C.UTF-8' } });
      if (result.status !== 0) error('linger_privilege_unavailable', `noninteractive privilege is required only for: sudo -n loginctl enable-linger ${username}; no service was installed`);
    }
    async installUnit({ path: filename, bytes, mode, uid }) {
      ensurePrivateDirectory(path.dirname(filename), uid); atomicWrite(filename, bytes, mode, uid);
      const reload = spawnSync('systemctl', ['--user', 'daemon-reload'], { encoding: 'utf8', timeout: 10000, env: { PATH: '/usr/bin:/bin' } });
      if (reload.status !== 0) error('user_systemd_unavailable', 'the user systemd manager could not load the generated unit');
    }
    async enable({ unit }) {
      const result = spawnSync('systemctl', ['--user', 'enable', unit], { encoding: 'utf8', timeout: 10000, env: { PATH: '/usr/bin:/bin' } });
      if (result.status !== 0) error('service_enable_failed', 'the user service could not be enabled');
    }
    async isEnabled({ unit }) {
      const result = spawnSync('systemctl', ['--user', 'is-enabled', unit], { encoding: 'utf8', timeout: 5000, env: { PATH: '/usr/bin:/bin' } });
      return result.status === 0;
    }
    async enableAndStart({ unit }) {
      for (const args of [['--user', 'enable', unit], ['--user', 'reset-failed', unit], ['--user', 'start', unit]]) {
        const result = spawnSync('systemctl', args, { encoding: 'utf8', timeout: 10000, env: { PATH: '/usr/bin:/bin' } });
        if (result.status !== 0) error('service_start_failed', 'the user service could not be enabled and started');
      }
    }
    async stop({ unit }) { spawnSync('systemctl', ['--user', 'stop', unit], { timeout: 10000, env: { PATH: '/usr/bin:/bin' } }); }
    async quiesce({ unit, deadline_ms = 75000 }) {
      const result = spawnSync('systemctl', ['--user', 'stop', unit], { encoding: 'utf8', timeout: deadline_ms + 10000, env: { PATH: '/usr/bin:/bin', LANG: 'C.UTF-8', LC_ALL: 'C.UTF-8' } });
      if (result.status !== 0) error('service_stop_failed', 'the user service did not stop within its graceful deadline');
    }
    async probeStopped() {
      const snapshot = await this.probeOwner.inspectCanonical({ uid: process.geteuid() });
      return { process_generation_gone: snapshot.service_active !== true, listener_gone: !(snapshot.listener && snapshot.listener.host === '127.0.0.1' && snapshot.listener.port === 8000) };
    }
    async startExactlyOnce({ unit }) {
      const reset = spawnSync('systemctl', ['--user', 'reset-failed', unit], { encoding: 'utf8', timeout: 10000, env: { PATH: '/usr/bin:/bin' } });
      if (reset.status !== 0) error('service_reset_failed', 'the user service start limit could not be reset');
      const result = spawnSync('systemctl', ['--user', 'start', unit], { encoding: 'utf8', timeout: STARTUP_DEADLINE_MS + 15000, env: { PATH: '/usr/bin:/bin' } });
      if (result.status !== 0) error('service_start_failed', 'the exact user service candidate could not be started once');
    }
    async disable({ unit }) { spawnSync('systemctl', ['--user', 'disable', unit], { timeout: 10000, env: { PATH: '/usr/bin:/bin' } }); }
    async probe({ release_id, build_id }) {
      return this.probeOwner.inspectCanonical({ uid: process.geteuid(), release: { release_id, build_id } });
    }
  }

  function defaultDependencies() {
    return { host: new SystemHost(), source: new UnprovisionedSource(), service: new SystemService(), clock: new SystemClock(), randomBytes: crypto.randomBytes, output: { info(line) { process.stdout.write(`${line}\n`); } } };
  }

  function installContractSchemaNames() { return ['install-transaction.v1.schema.json', 'installation.v1.schema.json']; }

  api = {
    AGENT_CONFIG, AGENT_IMAGE, AGENT_SPEC_DIGEST, FREE_SPACE_RESERVE, MINIMUM_VRAM, REQUIRED_COMPONENTS, SERVICE_UNIT, STARTUP_DEADLINE_MS,
    UNIT_CONTRACT, UNIT_CONTRACT_SHA256, artifactPreflight, atomicWrite, compatibilityPreflight, defaultDependencies,
    ensurePrivateDirectory, extractVerifiedArchive, hostPreflight, installContractSchemaNames, installDirectories,
    installVoiceAgent, inspectManagedPath, layoutFor, readPrivateJson, renderAgentConfig, renderUnit, safeDefaults, syncDirectory,
    validateReadiness, verifyExtractedTree, writeJson,
  };
  return api;
};
