'use strict';

module.exports = function createLifecycle(core, installer, updater) {
  const crypto = require('node:crypto');
  const fs = require('node:fs');
  const os = require('node:os');
  const path = require('node:path');
  const zlib = require('node:zlib');
  const readline = require('node:readline/promises');
  const { spawnSync } = require('node:child_process');
  const agentEnvironment = core.loadAgentEnvironment();

  const CATEGORIES = ['program', 'program_cache', 'models', 'agent_environment', 'application_data'];
  const BUNDLE_CATEGORIES = ['status', 'doctor', 'last-operation', 'service-policy', 'journal-metadata'];
  const ENTRY_NAMES = ['doctor.json', 'journal-metadata.json', 'last-operation.json', 'service-policy.json', 'status.json'];
  const RECEIPT_KEYS = [...CATEGORIES, 'agent_container'];
  const UNINSTALL_PHASES = ['planned', 'service_removed', 'program_removed', 'program_cache_removed', 'models_removed', 'agent_container_removed', 'agent_environment_removed', 'application_data_removed', 'launcher_removed', 'complete'];

  function error(code, message) { throw new core.LauncherError(code, message); }
  function exists(filename) { try { fs.lstatSync(filename); return true; } catch (reason) { if (reason.code === 'ENOENT') return false; throw reason; } }
  function digest(bytes) { return crypto.createHash('sha256').update(bytes).digest('hex'); }
  function exactKeys(value, keys, code) {
    if (!value || typeof value !== 'object' || Array.isArray(value) || Object.keys(value).sort().join('\0') !== [...keys].sort().join('\0')) error(code, 'document fields are not closed');
  }
  function fault(dependencies, phase) { if (dependencies.fault && typeof dependencies.fault.afterLifecyclePhase === 'function') dependencies.fault.afterLifecyclePhase(phase); }
  function timestamp(clock) { return clock.now().toISOString().replace('.000Z', 'Z'); }

  function recursiveInventory(root, expectedParent, uid) {
    if (path.dirname(root) !== expectedParent) error('uninstall_target_invalid', 'an exact category root escaped its allowlist');
    if (!exists(root)) return { entries: 0, bytes: 0 };
    let entries = 0; let bytes = 0;
    function walk(filename) {
      const metadata = fs.lstatSync(filename);
      if (metadata.uid !== uid || metadata.isSymbolicLink()) error('uninstall_target_invalid', 'a selected target is foreign or linked');
      entries += 1;
      if (metadata.isDirectory()) for (const name of fs.readdirSync(filename).sort()) walk(path.join(filename, name));
      else if (metadata.isFile() && metadata.nlink === 1) bytes += metadata.size;
      else error('uninstall_target_invalid', 'a selected target has an unsupported type');
    }
    walk(root);
    return { entries, bytes };
  }

  function removeTree(root, expectedParent, uid) {
    if (!exists(root)) return 'already_removed';
    recursiveInventory(root, expectedParent, uid);
    function remove(filename) {
      const metadata = fs.lstatSync(filename);
      if (metadata.uid !== uid || metadata.isSymbolicLink()) error('uninstall_target_changed', 'a selected target changed during deletion');
      if (metadata.isDirectory()) { for (const name of fs.readdirSync(filename).sort()) remove(path.join(filename, name)); fs.rmdirSync(filename); }
      else if (metadata.isFile() && metadata.nlink === 1) fs.unlinkSync(filename);
      else error('uninstall_target_changed', 'a selected target changed type during deletion');
    }
    remove(root); installer.syncDirectory(expectedParent); return 'removed';
  }

  function inspectFile(filename, uid, modes = null) {
    if (!exists(filename)) return null;
    const metadata = installer.inspectManagedPath(filename, uid, null, 'file');
    if (metadata.nlink !== 1 || (modes && !modes.includes(metadata.mode & 0o777))) error('uninstall_target_invalid', 'an exact file target has unsafe custody');
    return { entries: 1, bytes: metadata.size };
  }

  function selectedCategories(arguments_) {
    const categories = ['program'];
    if (arguments_.purgeProgramCache || arguments_.purgeAll) categories.push('program_cache');
    if (arguments_.purgeModels || arguments_.purgeAll) categories.push('models');
    if (arguments_.purgeAgentEnvironment || arguments_.purgeAll) categories.push('agent_environment');
    if (arguments_.purgeAll) categories.push('application_data');
    return categories;
  }

  function validateReleaseInventory(layout) {
    if (!exists(layout.releases)) return { entries: 0, bytes: 0 };
    installer.inspectManagedPath(layout.releases, layout.identity.uid, 0o700);
    let bytes = 0; let entries = 1;
    for (const id of fs.readdirSync(layout.releases).sort()) {
      const release = updater.readRelease(layout, id);
      const value = recursiveInventory(release.root, layout.releases, layout.identity.uid);
      entries += value.entries; bytes += value.bytes;
    }
    return { entries, bytes };
  }

  function inventory(layout, categories) {
    const uid = layout.identity.uid;
    const totals = Object.fromEntries(CATEGORIES.map((name) => [name, { selected: categories.includes(name), entries: 0, bytes: 0 }]));
    const add = (category, value) => { if (value) { totals[category].entries += value.entries; totals[category].bytes += value.bytes; } };
    add('program', validateReleaseInventory(layout));
    for (const filename of [layout.installRecord, layout.current, layout.rollback, layout.updateJournal, layout.selfUpdateJournal, layout.updateResult]) {
      if (!exists(filename)) continue;
      const metadata = fs.lstatSync(filename);
      if (metadata.isSymbolicLink()) {
        if (![layout.current, layout.rollback].includes(filename) || metadata.uid !== uid || !/^releases\/[0-9A-Za-z][0-9A-Za-z.+-]{0,95}$/.test(fs.readlinkSync(filename))) error('uninstall_target_invalid', 'a program pointer is unsafe');
        add('program', { entries: 1, bytes: 0 });
      } else add('program', inspectFile(filename, uid, [0o600]));
    }
    if (exists(layout.migrations)) {
      installer.inspectManagedPath(layout.migrations, uid, 0o700);
      for (const name of fs.readdirSync(layout.migrations).sort()) {
        if (!/^[0-9a-f]{32}$/.test(name)) error('uninstall_target_invalid', 'unknown migration evidence is preserved and blocks broad deletion');
        add('program', recursiveInventory(path.join(layout.migrations, name), layout.migrations, uid));
      }
    }
    for (const filename of [layout.launcher, `${layout.launcher}.old`]) add('program', inspectFile(filename, uid, [0o700, 0o755]));
    add('program', inspectFile(layout.unit, uid, [0o600]));
    if (exists(layout.downloads)) add('program', recursiveInventory(layout.downloads, layout.cache, uid));
    if (categories.includes('program_cache')) {
      for (const root of [layout.runtimes, layout.launchers]) if (exists(root)) add('program_cache', recursiveInventory(root, path.dirname(root), uid));
      add('program_cache', inspectFile(layout.channelReceipt, uid, [0o600]));
    }
    if (categories.includes('models') && exists(layout.models)) add('models', recursiveInventory(layout.models, path.dirname(layout.models), uid));
    if (categories.includes('agent_environment') && exists(layout.agent)) add('agent_environment', recursiveInventory(layout.agent, layout.data, uid));
    if (categories.includes('application_data')) {
      if (exists(layout.appData)) add('application_data', recursiveInventory(layout.appData, layout.data, uid));
      for (const root of [layout.config, layout.logs]) if (exists(root)) add('application_data', recursiveInventory(root, path.dirname(root), uid));
    }
    return { schema: 'voice-agent.uninstall-inventory.v1', categories: CATEGORIES.map((name) => ({ name, ...totals[name] })) };
  }

  function validateUninstallJournal(value) {
    exactKeys(value, ['categories', 'id', 'phase', 'receipts', 'schema', 'started_at', 'updated_at'], 'uninstall_journal_invalid');
    exactKeys(value.receipts, RECEIPT_KEYS, 'uninstall_journal_invalid');
    if (value.schema !== 'voice-agent.uninstall-transaction.v1' || !/^[0-9a-f]{32}$/.test(value.id) || !UNINSTALL_PHASES.includes(value.phase)
      || !Array.isArray(value.categories) || value.categories.length < 1 || value.categories.some((name) => !CATEGORIES.includes(name))
      || new Set(value.categories).size !== value.categories.length || RECEIPT_KEYS.some((name) => typeof value.receipts[name] !== 'boolean')) error('uninstall_journal_invalid', 'uninstall recovery record is invalid');
    return value;
  }

  function writeUninstallJournal(layout, value, phase, dependencies, receipt = null) {
    const next = { ...value, phase, updated_at: timestamp(dependencies.clock), receipts: { ...value.receipts, ...(receipt ? { [receipt]: true } : {}) } };
    validateUninstallJournal(next); installer.writeJson(layout.uninstallJournal, next, layout.identity.uid); fault(dependencies, phase); return next;
  }

  async function confirmCategories(arguments_, categories, dependencies) {
    if (arguments_.yes) return;
    const confirmer = dependencies.confirm;
    if (!confirmer) error('confirmation_required', 'confirmation is required; use --yes for automation');
    const requests = [{ category: 'program', phrase: 'UNINSTALL VOICE AGENT' }];
    for (const category of categories.filter((name) => name !== 'program')) requests.push({ category, phrase: `PURGE ${category.replaceAll('_', ' ').toUpperCase()}` });
    for (const request of requests) if (await confirmer(request) !== request.phrase) error('confirmation_required', `typed confirmation for ${request.category} did not match`);
  }

  function exactUnitAccepted(layout) {
    if (!exists(layout.unit)) return;
    const bytes = core.readOwnedRegular(layout.unit, layout.identity.uid, [0o600], 256 * 1024);
    const ids = [];
    for (const pointer of [layout.current, layout.rollback]) {
      if (!exists(pointer)) continue;
      const match = /^releases\/(.+)$/.exec(fs.readlinkSync(pointer)); if (match) ids.push(match[1]);
    }
    const accepted = ids.some((id) => { try { return bytes.equals(installer.renderUnit(layout, updater.readRelease(layout, id).root)); } catch { return false; } });
    if (!accepted) error('uninstall_service_policy_changed', 'the owned user unit differs from every exact recorded release policy');
  }

  function removeExactFile(filename, uid) {
    if (!exists(filename)) return;
    inspectFile(filename, uid); fs.unlinkSync(filename); installer.syncDirectory(path.dirname(filename));
  }

  function removeProgram(layout) {
    const uid = layout.identity.uid;
    if (exists(layout.releases)) {
      for (const id of fs.readdirSync(layout.releases).sort()) { const release = updater.readRelease(layout, id); removeTree(release.root, layout.releases, uid); }
    }
    if (exists(layout.migrations)) for (const name of fs.readdirSync(layout.migrations).sort()) removeTree(path.join(layout.migrations, name), layout.migrations, uid);
    for (const filename of [layout.current, layout.rollback]) if (exists(filename)) { const meta = fs.lstatSync(filename); if (!meta.isSymbolicLink() || meta.uid !== uid) error('uninstall_target_changed', 'program pointer changed'); fs.unlinkSync(filename); installer.syncDirectory(layout.data); }
    for (const filename of [layout.installRecord, layout.updateJournal, layout.selfUpdateJournal, layout.updateResult]) removeExactFile(filename, uid);
    if (exists(layout.downloads)) removeTree(layout.downloads, layout.cache, uid);
  }

  function registryIdentity(layout) {
    const filename = path.join(layout.agent, 'private', 'registry.json');
    if (!exists(filename)) return null;
    const registry = JSON.parse(core.readOwnedRegular(filename, layout.identity.uid, [0o600], 1024 * 1024).toString('utf8'));
    if (!registry || registry.schema_version !== 'voice-agent.agent-environment-registry.v1' || !/^[a-f0-9]{64}$/.test(registry.selected_container_id || '') || typeof registry.owner_key !== 'string') error('agent_environment_registry_invalid', 'exact AgentEnvironment registry custody is unavailable');
    return registry;
  }

  async function removeAgentContainer(layout, dependencies) {
    const registry = registryIdentity(layout);
    if (registry) {
      const endpointPath = path.join(layout.private, 'docker-endpoint.json');
      if (!exists(endpointPath)) error('rootless_docker_required', 'exact rootless Docker authority is required before AgentEnvironment deletion');
      const endpoint = agentEnvironment.validateEndpointRecord(JSON.parse(core.readOwnedRegular(endpointPath, layout.identity.uid, [0o600], 256 * 1024).toString('utf8')), layout.identity.uid);
      if (!endpoint.endpoint || !dependencies.docker) error('rootless_docker_required', 'exact rootless Docker reinspection is required before AgentEnvironment deletion');
      const observed = await dependencies.docker.reinspect({ endpoint: endpoint.endpoint, container_id: registry.selected_container_id });
      if (!observed || observed.endpoint_verified !== true || observed.rootless !== true || observed.container_id !== registry.selected_container_id
        || observed.managed !== true || observed.owner_key !== registry.owner_key) error('agent_environment_delete_denied', 'Docker endpoint/container/owner identity changed before deletion');
      await dependencies.docker.removeExact({ endpoint: endpoint.endpoint, container_id: registry.selected_container_id });
    }
  }

  function removeAgentData(layout) {
    if (exists(layout.agent)) removeTree(layout.agent, layout.data, layout.identity.uid);
  }

  async function uninstallVoiceAgent(options = {}) {
    const testMode = options.testMode === true;
    if (!testMode && options.dependencies) error('test_injection_forbidden', 'uninstall injection is test-only');
    const dependencies = options.dependencies || defaultDependencies();
    const layout = installer.layoutFor(testMode ? dependencies.identity : undefined, testMode);
    const arguments_ = options.arguments || {};
    const categories = selectedCategories(arguments_);
    const output = dependencies.output || { info() {} };
    output.info('Preserved by default: configuration, secrets, models, application data/media, logs, and the complete persistent AgentEnvironment/Docker data; login linger is unchanged.');
    const plan = inventory(layout, categories);
    output.info(`Dry-run inventory: ${plan.categories.filter((item) => item.selected).map((item) => `${item.name}=${item.entries} entries/${item.bytes} bytes`).join(', ')}.`);
    if (arguments_.dryRun) return { state: 'dry_run', inventory: plan };
    await confirmCategories(arguments_, categories, dependencies);
    installer.ensurePrivateDirectory(layout.runtime, layout.identity.uid);
    const lock = updater.acquireExclusiveLock(layout, dependencies);
    try {
      let journal;
      if (exists(layout.uninstallJournal)) {
        journal = validateUninstallJournal(installer.readPrivateJson(layout.uninstallJournal, layout.identity.uid));
        if (journal.categories.join('\0') !== categories.join('\0')) error('uninstall_selection_mismatch', 'resume requires exactly the originally selected purge categories');
      } else {
        installer.ensurePrivateDirectory(layout.data, layout.identity.uid); installer.ensurePrivateDirectory(layout.transactions, layout.identity.uid);
        journal = { schema: 'voice-agent.uninstall-transaction.v1', id: dependencies.randomBytes(16).toString('hex'), categories, phase: 'planned', receipts: Object.fromEntries(RECEIPT_KEYS.map((name) => [name, false])), started_at: timestamp(dependencies.clock), updated_at: timestamp(dependencies.clock) };
        installer.writeJson(layout.uninstallJournal, journal, layout.identity.uid); fault(dependencies, 'planned');
      }
      if (journal.phase === 'planned') {
        exactUnitAccepted(layout);
        await dependencies.service.stopExact({ unit: installer.SERVICE_UNIT });
        await dependencies.service.disableExact({ unit: installer.SERVICE_UNIT });
        if (await dependencies.service.isEnabled({ unit: installer.SERVICE_UNIT })) error('uninstall_service_still_enabled', 'service remains enabled; program bytes were not removed');
        if (exists(layout.unit)) { exactUnitAccepted(layout); await dependencies.service.removeUnit({ path: layout.unit }); }
        journal = writeUninstallJournal(layout, journal, 'service_removed', dependencies);
      }
      if (!journal.receipts.program) { removeProgram(layout); journal = writeUninstallJournal(layout, journal, 'program_removed', dependencies, 'program'); }
      if (categories.includes('program_cache') && !journal.receipts.program_cache) {
        for (const root of [layout.runtimes, layout.launchers]) if (exists(root)) removeTree(root, path.dirname(root), layout.identity.uid);
        removeExactFile(layout.channelReceipt, layout.identity.uid);
        journal = writeUninstallJournal(layout, journal, 'program_cache_removed', dependencies, 'program_cache');
      }
      if (categories.includes('models') && !journal.receipts.models) { if (exists(layout.models)) removeTree(layout.models, path.dirname(layout.models), layout.identity.uid); journal = writeUninstallJournal(layout, journal, 'models_removed', dependencies, 'models'); }
      if (categories.includes('agent_environment') && !journal.receipts.agent_container) { await removeAgentContainer(layout, dependencies); journal = writeUninstallJournal(layout, journal, 'agent_container_removed', dependencies, 'agent_container'); }
      if (categories.includes('agent_environment') && !journal.receipts.agent_environment) { removeAgentData(layout); journal = writeUninstallJournal(layout, journal, 'agent_environment_removed', dependencies, 'agent_environment'); }
      if (categories.includes('application_data') && !journal.receipts.application_data) {
        if (exists(layout.appData)) removeTree(layout.appData, layout.data, layout.identity.uid);
        for (const root of [layout.config, layout.logs]) if (exists(root)) removeTree(root, path.dirname(root), layout.identity.uid);
        journal = writeUninstallJournal(layout, journal, 'application_data_removed', dependencies, 'application_data');
      }
      if (!['launcher_removed', 'complete'].includes(journal.phase)) {
        for (const filename of [layout.launcher, `${layout.launcher}.old`]) removeExactFile(filename, layout.identity.uid);
        journal = writeUninstallJournal(layout, journal, 'launcher_removed', dependencies);
      }
      journal = writeUninstallJournal(layout, journal, 'complete', dependencies);
      installer.ensurePrivateDirectory(layout.state, layout.identity.uid);
      installer.writeJson(layout.lifecycleResult, { schema: 'voice-agent.lifecycle-result.v1', operation: 'uninstall', state: 'uninstalled', error_code: null, active_release: null, rollback_release: null, recovery_required: false }, layout.identity.uid);
      fs.unlinkSync(layout.uninstallJournal); installer.syncDirectory(layout.transactions);
      output.info(`Uninstall complete for selected categories: ${categories.join(', ')}. Preserved categories were not broadened; login linger remains unchanged.`);
      return { state: 'uninstalled', categories, inventory: plan };
    } finally { lock.release(); }
  }

  function safeDiagnosticDocument(value, kind) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) error('support_schema_invalid', `${kind} document is invalid`);
    const schema = kind === 'status' ? 'voice-agent.launcher-status.v1' : 'voice-agent.launcher-doctor.v1';
    if (value.schema_version !== schema) error('support_schema_invalid', `${kind} schema differs`);
    return value;
  }

  function lastOperation(layout) {
    if (exists(layout.lifecycleResult)) {
      const value = JSON.parse(core.readOwnedRegular(layout.lifecycleResult, layout.identity.uid, [0o600], 256 * 1024).toString('utf8'));
      exactKeys(value, ['active_release', 'error_code', 'operation', 'recovery_required', 'rollback_release', 'schema', 'state'], 'support_schema_invalid');
      if (value.schema !== 'voice-agent.lifecycle-result.v1' || !['rollback', 'uninstall'].includes(value.operation)
        || !['rolled_back_healthy', 'failed_safe', 'failed_needs_repair', 'uninstalled'].includes(value.state)
        || (value.error_code !== null && !/^[a-z0-9_]{1,64}$/.test(value.error_code))
        || ![value.active_release, value.rollback_release].every((item) => item === null || /^[0-9A-Za-z][0-9A-Za-z.+-]{0,95}$/.test(item))
        || typeof value.recovery_required !== 'boolean') error('support_schema_invalid', 'last lifecycle result is invalid');
      return value;
    }
    if (exists(layout.updateResult)) {
      const value = JSON.parse(core.readOwnedRegular(layout.updateResult, layout.identity.uid, [0o600], 256 * 1024).toString('utf8'));
      exactKeys(value, ['assets', 'error_code', 'latest_known', 'offline', 'release_id', 'rollback_release', 'schema', 'self_update', 'space', 'state'], 'support_schema_invalid');
      if (value.schema !== 'voice-agent.update-result.v1' || !['updated_healthy', 'failed_safe', 'failed_needs_repair'].includes(value.state)
        || (value.error_code !== null && !/^[a-z0-9_]{1,64}$/.test(value.error_code))
        || typeof value.release_id !== 'string' || typeof value.rollback_release !== 'string') error('support_schema_invalid', 'last update result is invalid');
      return { schema: 'voice-agent.support-last-operation.v1', operation: 'update', state: value.state, error_code: value.error_code, active_release: value.release_id, rollback_release: value.rollback_release, recovery_required: value.state === 'failed_needs_repair' };
    }
    return { schema: 'voice-agent.support-last-operation.v1', operation: 'none', state: 'not_recorded', error_code: null, active_release: null, rollback_release: null, recovery_required: false };
  }

  function journalMetadata(layout, dependencies) {
    const transactions = [];
    if (exists(layout.updateJournal)) {
      const value = updater.validateJournal(installer.readPrivateJson(layout.updateJournal, layout.identity.uid));
      transactions.push({ operation: value.operation, phase: value.phase, error_code: value.failure_code, completed_receipts: Object.keys(value.receipts).filter((name) => value.receipts[name]).sort() });
    }
    if (exists(layout.uninstallJournal)) {
      const value = validateUninstallJournal(installer.readPrivateJson(layout.uninstallJournal, layout.identity.uid));
      transactions.push({ operation: 'uninstall', phase: value.phase, error_code: null, completed_receipts: Object.keys(value.receipts).filter((name) => value.receipts[name]).sort() });
    }
    const excerpts = (dependencies.journalMetadata || []).slice(0, 32).map((item) => {
      exactKeys(item, ['code', 'priority', 'timestamp'], 'support_schema_invalid');
      if (!/^[a-z0-9_]{1,64}$/.test(item.code) || !Number.isSafeInteger(item.priority) || item.priority < 0 || item.priority > 7 || typeof item.timestamp !== 'string') error('support_schema_invalid', 'journal metadata is invalid');
      return item;
    });
    return { schema: 'voice-agent.support-journal-metadata.v1', transactions, excerpts };
  }

  function scanValue(value, dependencies, key = '') {
    if (/(secret|password|passwd|credential|authorization|private[_-]?key|command[_-]?line|process[_-]?output|url|workspace|filename|media|conversation|transcript|prompt|response|model[_-]?bytes|docker[_-]?inspect|raw[_-]?log)/i.test(key)
      || /^(?:config|environment|env|service_env)$/i.test(key)) error('support_secret_suspected', 'a forbidden secret/content field name was detected');
    if (typeof value === 'string') {
      if (/-----BEGIN [A-Z ]*PRIVATE KEY-----/.test(value) || /(?:bearer\s+|gh[pousr]_|sk-[A-Za-z0-9]|AKIA[0-9A-Z]{12})/i.test(value)
        || /https?:\/\/[^\s?#]+[?#][^\s]*=/.test(value)) error('support_secret_suspected', 'a token, private key, or credential-bearing URL was detected');
      const compact = value.replace(/[^A-Za-z0-9+/=_-]/g, '');
      if (compact.length >= 32 && compact.length / Math.max(1, value.length) > 0.9
        && !/^(?:[0-9a-f]{32}|[0-9a-f]{40}|[0-9a-f]{64})$/.test(value)
        && !/^voice-agent\.[a-z0-9.-]+\.v[0-9]+$/.test(value)) error('support_secret_suspected', 'a high-entropy value was detected');
      for (const secret of dependencies.secretValues || []) if (typeof secret === 'string' && secret.length >= 4 && value.includes(secret)) error('support_secret_suspected', 'a known secret value was detected');
    } else if (Array.isArray(value)) for (const item of value) scanValue(item, dependencies, key);
    else if (value && typeof value === 'object') for (const [name, item] of Object.entries(value)) scanValue(item, dependencies, name);
  }

  function tarHeader(name, size) {
    if (!/^[a-z0-9-]+\.json$/.test(name) || Buffer.byteLength(name) > 100) error('support_category_invalid', 'archive entry name is outside the closed allowlist');
    const header = Buffer.alloc(512); header.write(name, 0, 'utf8'); header.write('0000600\0', 100, 'ascii'); header.write('0000000\0', 108, 'ascii'); header.write('0000000\0', 116, 'ascii'); header.write(`${size.toString(8).padStart(11, '0')}\0`, 124, 'ascii'); header.write('00000000000\0', 136, 'ascii'); header.fill(0x20, 148, 156); header[156] = '0'.charCodeAt(0); header.write('ustar\0', 257, 'ascii'); header.write('00', 263, 'ascii');
    let sum = 0; for (const byte of header) sum += byte; header.write(`${sum.toString(8).padStart(6, '0')}\0 `, 148, 'ascii'); return header;
  }

  function makeArchive(documents) {
    const names = Object.keys(documents).sort();
    if (names.join('\0') !== ENTRY_NAMES.join('\0')) error('support_category_invalid', 'support archive categories differ from the closed allowlist');
    const parts = [];
    for (const name of names) { const bytes = Buffer.from(core.canonicalJson(documents[name])); parts.push(tarHeader(name, bytes.length), bytes, Buffer.alloc((512 - (bytes.length % 512)) % 512)); }
    parts.push(Buffer.alloc(1024)); return zlib.gzipSync(Buffer.concat(parts), { level: 9, mtime: 0 });
  }

  function safeOutput(layout, output) {
    const filename = output ? path.resolve(output) : path.join(layout.diagnostics, 'support-bundle.tar.gz');
    if (path.normalize(filename) !== filename || filename.includes('\0')) error('support_output_unsafe', 'support output path is unsafe');
    const parent = path.dirname(filename);
    if (!exists(parent)) { if (output) error('support_output_unsafe', 'explicit output parent must already exist'); installer.ensurePrivateDirectory(parent, layout.identity.uid); }
    core.noSymlinkComponents(parent);
    const metadata = fs.lstatSync(parent);
    if (!metadata.isDirectory() || metadata.uid !== layout.identity.uid) error('support_output_unsafe', 'support output parent is foreign');
    if (exists(filename)) error('support_output_exists', 'support output already exists and was not overwritten');
    return filename;
  }

  async function supportBundle(options = {}) {
    const testMode = options.testMode === true;
    if (!testMode && options.dependencies) error('test_injection_forbidden', 'support injection is test-only');
    const dependencies = options.dependencies || defaultDependencies();
    const layout = installer.layoutFor(testMode ? dependencies.identity : undefined, testMode);
    const output = dependencies.output || { info() {} };
    output.info(`Support bundle categories: ${BUNDLE_CATEGORIES.join(', ')}. Local archive only; no upload is performed.`);
    const status = safeDiagnosticDocument(await core.collectStatus(options.statusOptions || {}), 'status');
    const doctor = safeDiagnosticDocument(await core.collectDoctor(options.statusOptions || {}), 'doctor');
    const documents = {
      'status.json': status, 'doctor.json': doctor, 'last-operation.json': lastOperation(layout),
      'service-policy.json': { schema: 'voice-agent.support-service-policy.v1', unit: installer.SERVICE_UNIT, loopback_only: true, no_new_privileges: true, private_tmp: true, protect_system: 'strict', protect_home: 'read_only', restart: 'on_failure', unit_contract_sha256: installer.UNIT_CONTRACT_SHA256 },
      'journal-metadata.json': journalMetadata(layout, dependencies),
    };
    for (const [name, document] of Object.entries(documents)) { if (!ENTRY_NAMES.includes(name)) error('support_category_invalid', 'unexpected support file'); scanValue(document, dependencies); }
    const archive = makeArchive(documents); const filename = safeOutput(layout, options.output);
    const descriptor = fs.openSync(filename, fs.constants.O_WRONLY | fs.constants.O_CREAT | fs.constants.O_EXCL | (fs.constants.O_NOFOLLOW || 0), 0o600);
    try { fs.writeFileSync(descriptor, archive); fs.fsyncSync(descriptor); fs.fchmodSync(descriptor, 0o600); } catch (reason) { try { fs.unlinkSync(filename); } catch {} throw reason; } finally { fs.closeSync(descriptor); }
    installer.syncDirectory(path.dirname(filename));
    const result = { state: 'created', checksum_sha256: digest(archive), size: archive.length, categories: [...BUNDLE_CATEGORIES] };
    output.info(`Support bundle created locally: sha256=${result.checksum_sha256} size=${result.size} categories=${result.categories.join(',')}. Upload: not performed.`);
    return { ...result, path: filename };
  }

  class SystemClock { now() { return new Date(); } }
  class SystemService {
    async stopExact({ unit }) {
      const result = spawnSync('systemctl', ['--user', 'stop', unit], { timeout: 85000, env: { PATH: '/usr/bin:/bin' } });
      if (result.status !== 0) error('uninstall_service_stop_failed', 'the exact owned user service could not be stopped');
    }
    async disableExact({ unit }) {
      const result = spawnSync('systemctl', ['--user', 'disable', unit], { timeout: 10000, env: { PATH: '/usr/bin:/bin' } });
      if (result.status !== 0) error('uninstall_service_disable_failed', 'the exact owned user service could not be disabled');
    }
    async isEnabled({ unit }) { return spawnSync('systemctl', ['--user', 'is-enabled', unit], { timeout: 5000, env: { PATH: '/usr/bin:/bin' } }).status === 0; }
    async removeUnit({ path: filename }) { fs.unlinkSync(filename); installer.syncDirectory(path.dirname(filename)); const result = spawnSync('systemctl', ['--user', 'daemon-reload'], { timeout: 10000, env: { PATH: '/usr/bin:/bin' } }); if (result.status !== 0) error('uninstall_service_reload_failed', 'the user manager could not forget the removed exact unit'); }
  }
  class SystemDocker {
    constructor(layout) { this.layout = layout; }
    async reinspect({ endpoint, container_id }) {
      const uid = this.layout.identity.uid; const socketPath = `/run/user/${uid}/docker.sock`;
      let socket; try { socket = fs.lstatSync(socketPath); } catch { error('rootless_docker_required', 'the recorded rootless Docker socket is unavailable'); }
      if (endpoint !== `unix://${socketPath}` || !socket.isSocket() || socket.uid !== uid || (socket.mode & 0o777) !== 0o600) error('rootless_docker_required', 'rootless Docker socket custody changed');
      const endpointRecord = agentEnvironment.validateEndpointRecord(JSON.parse(core.readOwnedRegular(path.join(this.layout.private, 'docker-endpoint.json'), uid, [0o600], 256 * 1024).toString('utf8')), uid);
      if (endpointRecord.socket_identity !== `${socket.dev}:${socket.ino}`) error('rootless_docker_required', 'rootless Docker socket identity changed');
      const environment = { PATH: '/usr/bin:/bin', HOME: '/nonexistent', DOCKER_CONFIG: '/nonexistent', LANG: 'C.UTF-8', LC_ALL: 'C.UTF-8' };
      const info = spawnSync('docker', ['--host', endpoint, 'info', '--format', '{{json .SecurityOptions}}\n{{.ID}}\n{{.DockerRootDir}}'], { encoding: 'utf8', timeout: 5000, maxBuffer: 65536, env: environment });
      if (info.status !== 0) error('rootless_docker_required', 'the exact rootless Docker daemon is unavailable');
      const lines = info.stdout.trim().split('\n'); let security; try { security = JSON.parse(lines[0]); } catch { error('rootless_docker_required', 'rootless Docker authority response is invalid'); }
      const root = lines[2]; core.noSymlinkComponents(root); const rootMeta = fs.lstatSync(root);
      if (!Array.isArray(security) || !security.some((item) => String(item).includes('rootless')) || digest(Buffer.from(lines[1] || '')) !== endpointRecord.daemon_identity || !rootMeta.isDirectory() || rootMeta.uid !== uid) error('rootless_docker_required', 'Docker daemon identity is not the recorded rootless user authority');
      const format = '{{.Id}}\n{{index .Config.Labels "io.priney.voice-agent-v2.managed"}}\n{{index .Config.Labels "io.priney.voice-agent-v2.owner"}}';
      const inspect = spawnSync('docker', ['--host', endpoint, 'container', 'inspect', container_id, '--format', format], { encoding: 'utf8', timeout: 5000, maxBuffer: 65536, env: environment });
      if (inspect.status !== 0) error('agent_environment_delete_denied', 'the exact recorded AgentEnvironment is unavailable');
      const values = inspect.stdout.trim().split('\n');
      return { endpoint_verified: true, rootless: true, container_id: values[0], managed: values[1] === '1', owner_key: values[2] };
    }
    async removeExact({ endpoint, container_id }) {
      const result = spawnSync('docker', ['--host', endpoint, 'container', 'rm', '--force', container_id], { encoding: 'utf8', timeout: 30000, maxBuffer: 65536, env: { PATH: '/usr/bin:/bin', HOME: '/nonexistent', DOCKER_CONFIG: '/nonexistent', LANG: 'C.UTF-8', LC_ALL: 'C.UTF-8' } });
      if (result.status !== 0 || result.stdout.trim() !== container_id) error('agent_environment_delete_failed', 'the exact owned AgentEnvironment container was not removed');
    }
  }
  async function ttyConfirm(request) { const terminal = readline.createInterface({ input: process.stdin, output: process.stderr }); try { return await terminal.question(`Type ${request.phrase} to continue: `); } finally { terminal.close(); } }
  function defaultDependencies() { const layout = installer.layoutFor(); return { identity: undefined, service: new SystemService(), docker: new SystemDocker(layout), clock: new SystemClock(), randomBytes: crypto.randomBytes, confirm: ttyConfirm, output: { info(line) { process.stdout.write(`${line}\n`); } } }; }

  return { BUNDLE_CATEGORIES, CATEGORIES, inventory, makeArchive, scanValue, supportBundle, uninstallVoiceAgent, validateUninstallJournal };
};
