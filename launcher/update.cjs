'use strict';

module.exports = function createUpdater(core, installer) {
  const crypto = require('node:crypto');
  const fs = require('node:fs');
  const path = require('node:path');
  const { spawnSync } = require('node:child_process');

  const PHASES = new Set([
    'recovering', 'checking', 'staging', 'verified', 'migrations_prepared', 'prior_custody',
    'quiescing', 'activating', 'starting', 'ready', 'committing', 'healthy', 'cleanup',
    'rolling_back', 'restoring', 'starting_prior', 'prior_ready', 'failed_safe', 'failed_needs_repair',
  ]);
  const POST_QUIESCE = new Set([
    'quiescing', 'activating', 'starting', 'ready', 'committing', 'healthy', 'rolling_back',
    'restoring', 'starting_prior', 'prior_ready', 'failed_safe', 'failed_needs_repair',
  ]);
  const RECEIPT_KEYS = [
    'artifact_verified', 'candidate_ready', 'channel_checked', 'migration_prepared',
    'pointer_activated', 'pre_gc_complete', 'prior_custody', 'prior_ready', 'prior_restored',
    'release_staged', 'service_started', 'service_stopped', 'terminal', 'unit_reloaded',
  ];
  const JOURNAL_KEYS = [
    'candidate', 'config_snapshot', 'failure_code', 'id', 'phase', 'prior_healthy',
    'prior_running', 'prior_selected', 'receipts', 'requested_channel', 'schema', 'service',
    'started_at', 'updated_at',
  ];
  const MIGRATION_KEYS = ['destructive', 'from', 'id', 'operation', 'product_choice', 'reversible', 'scope', 'sha256', 'to'];
  const UPDATE_SCHEMA = 'voice-agent.update-transaction.v1';

  function error(code, message) { throw new core.LauncherError(code, message); }
  function digest(bytes) { return crypto.createHash('sha256').update(bytes).digest('hex'); }
  function exists(filename) { try { fs.lstatSync(filename); return true; } catch (reason) { if (reason.code === 'ENOENT') return false; throw reason; } }
  function timestamp(clock) { return clock.now().toISOString().replace('.000Z', 'Z'); }
  function exactKeys(value, keys, code) {
    if (!value || typeof value !== 'object' || Array.isArray(value)
      || Object.keys(value).sort().join('\0') !== [...keys].sort().join('\0')) error(code, 'the update record has unknown or missing fields');
  }
  function fault(dependencies, kind, name) {
    const injector = dependencies.fault;
    if (!injector) return;
    if (kind === 'write' && typeof injector.afterDurablePhase === 'function') injector.afterDurablePhase(name);
    if (kind === 'action' && typeof injector.afterAction === 'function') injector.afterAction(name);
  }

  function releaseId(release) { return `${release.version}-${release.artifact_sha256.slice(0, 12)}`; }

  function acquireExclusiveLock(layout, dependencies) {
    installer.ensurePrivateDirectory(layout.runtime, layout.identity.uid);
    if (dependencies.lock) return dependencies.lock.acquire({ path: layout.updateLock, uid: layout.identity.uid });
    let descriptor;
    try {
      descriptor = fs.openSync(layout.updateLock, fs.constants.O_RDWR | fs.constants.O_CREAT | (fs.constants.O_NOFOLLOW || 0), 0o600);
      fs.fchmodSync(descriptor, 0o600);
      const metadata = fs.fstatSync(descriptor);
      if (!metadata.isFile() || metadata.uid !== layout.identity.uid || metadata.nlink !== 1 || (metadata.mode & 0o777) !== 0o600) {
        error('update_lock_invalid', 'the canonical update lock is foreign or unsafe');
      }
      const result = spawnSync('flock', ['--nonblock', '3'], { stdio: ['ignore', 'ignore', 'ignore', descriptor], timeout: 3000, env: { PATH: '/usr/bin:/bin', LC_ALL: 'C.UTF-8' } });
      if (result.status !== 0) error('update_in_progress', 'another voice-agent update owns the installation lock');
      return { release() { if (descriptor !== undefined) { fs.closeSync(descriptor); descriptor = undefined; } } };
    } catch (reason) {
      if (descriptor !== undefined) fs.closeSync(descriptor);
      if (reason instanceof core.LauncherError) throw reason;
      error('update_lock_unavailable', 'the canonical update lock could not be acquired');
    }
  }

  function emptyReceipts() { return Object.fromEntries(RECEIPT_KEYS.map((key) => [key, false])); }

  function validateJournal(document) {
    exactKeys(document, JOURNAL_KEYS, 'update_journal_invalid');
    if (document.schema !== UPDATE_SCHEMA || !/^[0-9a-f]{32}$/.test(document.id) || document.requested_channel !== 'stable'
      || !PHASES.has(document.phase) || !/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$/.test(document.started_at)
      || !/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$/.test(document.updated_at)
      || ![document.prior_selected, document.prior_running, document.prior_healthy, document.candidate].every((value) => value === null || /^[0-9A-Za-z][0-9A-Za-z.+-]{0,95}$/.test(value))
      || document.prior_running !== document.prior_healthy
      || document.config_snapshot !== document.id
      || (document.failure_code !== null && (typeof document.failure_code !== 'string' || !/^[a-z0-9_]{1,64}$/.test(document.failure_code)))) {
      error('update_journal_invalid', 'the durable update journal is invalid');
    }
    exactKeys(document.service, ['unit_sha256', 'was_active', 'was_enabled'], 'update_journal_invalid');
    if (typeof document.service.was_active !== 'boolean' || typeof document.service.was_enabled !== 'boolean'
      || (document.service.unit_sha256 !== null && !/^[0-9a-f]{64}$/.test(document.service.unit_sha256))) error('update_journal_invalid', 'the service custody receipt is invalid');
    exactKeys(document.receipts, RECEIPT_KEYS, 'update_journal_invalid');
    if (RECEIPT_KEYS.some((key) => typeof document.receipts[key] !== 'boolean')) error('update_journal_invalid', 'an update receipt is invalid');
    return document;
  }

  function persistJournal(layout, journal, phase, dependencies, changes = {}) {
    const next = { ...journal, ...changes, phase, updated_at: timestamp(dependencies.clock) };
    if (changes.receipts) next.receipts = { ...journal.receipts, ...changes.receipts };
    validateJournal(next);
    installer.writeJson(layout.updateJournal, next, layout.identity.uid);
    fault(dependencies, 'write', phase);
    return next;
  }

  function readJournal(layout) { return validateJournal(installer.readPrivateJson(layout.updateJournal, layout.identity.uid)); }

  function pointerId(layout, filename) {
    if (!exists(filename)) return null;
    const metadata = fs.lstatSync(filename);
    if (!metadata.isSymbolicLink() || metadata.uid !== layout.identity.uid) error('pointer_invalid', 'a canonical release pointer is unsafe');
    const target = fs.readlinkSync(filename);
    const match = /^releases\/([0-9A-Za-z][0-9A-Za-z.+-]{0,95})$/.exec(target);
    if (!match) error('pointer_invalid', 'a canonical release pointer target is invalid');
    return match[1];
  }

  function readRelease(layout, id) {
    if (typeof id !== 'string' || !/^[0-9A-Za-z][0-9A-Za-z.+-]{0,95}$/.test(id)) error('release_record_invalid', 'release identity is invalid');
    const root = path.join(layout.releases, id);
    installer.inspectManagedPath(root, layout.identity.uid, 0o500);
    const legacyPath = path.join(root, 'legacy-import-record.json');
    if (exists(legacyPath)) {
      const record = core.validateLegacyImportRecord(JSON.parse(core.readOwnedRegular(legacyPath, layout.identity.uid, [0o400], 256 * 1024).toString('utf8')));
      if (record.release_id !== id || exists(path.join(root, 'payload', 'release.json'))
          || core.legacyTreeDigest(path.join(root, 'payload'), layout.identity.uid) !== record.immutable_inventory_sha256
          || digest(core.readOwnedRegular(path.join(root, 'payload', 'ops', 'systemd', 'voice-agent-v2.service'), layout.identity.uid, null, 256 * 1024)) !== record.service_unit_sha256) {
        error('legacy_import_record_invalid', 'imported legacy release differs from its custody record');
      }
      return { id, root, record: { ...record, artifact_bytes: 0 }, manifest: null, manifestBytes: null, kind: 'legacy_import' };
    }
    const recordPath = path.join(root, 'release-record.json');
    const record = core.validateReleaseRecord(JSON.parse(core.readOwnedRegular(recordPath, layout.identity.uid, [0o400], 256 * 1024).toString('utf8')));
    if (record.release_id !== id) error('release_record_invalid', 'release record identity differs from its directory');
    const manifestBytes = core.readOwnedRegular(path.join(root, 'release-manifest.json'), layout.identity.uid, [0o444], 4 * 1024 * 1024);
    if (digest(manifestBytes) !== record.manifest_sha256) error('release_record_invalid', 'the signed manifest receipt differs');
    const manifest = core.validateArtifactManifest(core.parseCanonicalJson(manifestBytes, 4 * 1024 * 1024));
    if (manifest.version !== record.version || manifest.build_id !== record.build_id || manifest.platform !== record.platform
      || manifest.service_template_sha256 !== record.service_template_sha256) error('release_record_invalid', 'release and manifest identity differ');
    installer.verifyExtractedTree(root, manifest, manifestBytes, layout.identity.uid, true);
    return { id, root, record, manifest, manifestBytes, kind: 'signed_artifact' };
  }

  function atomicPointer(layout, filename, id, transactionId) {
    readRelease(layout, id);
    const temporary = path.join(layout.data, `.${path.basename(filename)}.${transactionId}`);
    if (exists(temporary)) fs.unlinkSync(temporary);
    fs.symlinkSync(`releases/${id}`, temporary);
    fs.renameSync(temporary, filename);
    installer.syncDirectory(layout.data);
  }

  function makeWritable(root) {
    const metadata = fs.lstatSync(root);
    if (metadata.isSymbolicLink()) return;
    if (metadata.isDirectory()) {
      fs.chmodSync(root, 0o700);
      for (const name of fs.readdirSync(root)) makeWritable(path.join(root, name));
    } else if (metadata.isFile()) fs.chmodSync(root, 0o600);
  }

  function removeExactTree(root, expectedParent, uid) {
    if (path.dirname(root) !== expectedParent) error('gc_target_invalid', 'a collection target escaped its canonical root');
    const metadata = fs.lstatSync(root);
    if (!metadata.isDirectory() || metadata.isSymbolicLink() || metadata.uid !== uid) error('gc_target_invalid', 'a collection target is unsafe or unowned');
    makeWritable(root);
    fs.rmSync(root, { recursive: true });
    installer.syncDirectory(expectedParent);
  }

  function protectedReleaseIds(layout, journal = null, extra = []) {
    const values = new Set(extra.filter(Boolean));
    for (const pointer of [layout.current, layout.rollback]) { const id = pointerId(layout, pointer); if (id) values.add(id); }
    if (journal) for (const key of ['prior_selected', 'prior_running', 'prior_healthy', 'candidate']) if (journal[key]) values.add(journal[key]);
    return values;
  }

  function collectReleases(layout, journal, extra = []) {
    const protectedIds = protectedReleaseIds(layout, journal, extra);
    let removed = 0;
    let bytes = 0;
    if (!exists(layout.releases)) return { removed, bytes };
    for (const id of fs.readdirSync(layout.releases).sort()) {
      if (protectedIds.has(id)) continue;
      const root = path.join(layout.releases, id);
      try {
        const release = readRelease(layout, id);
        const currentProtected = protectedReleaseIds(layout, exists(layout.updateJournal) ? readJournal(layout) : journal, extra);
        if (currentProtected.has(id)) continue;
        bytes += release.record.artifact_bytes;
        removeExactTree(root, layout.releases, layout.identity.uid);
        removed += 1;
      } catch (reason) {
        if (reason instanceof core.LauncherError) continue;
        throw reason;
      }
    }
    return { removed, bytes };
  }

  function removeTransactionPartials(layout, journal, keepCandidate = false) {
    const targets = [
      path.join(layout.transactions, `stage-${journal.id}`),
      path.join(layout.downloads, `update-${journal.id}.partial`),
      path.join(layout.downloads, `update-${journal.id}.verified`),
    ];
    for (const target of targets) {
      if (!exists(target)) continue;
      const parent = path.dirname(target);
      const metadata = fs.lstatSync(target);
      if (metadata.uid !== layout.identity.uid || metadata.isSymbolicLink()) error('partial_target_invalid', 'a transaction partial is unsafe or unowned');
      if (metadata.isDirectory()) removeExactTree(target, parent, layout.identity.uid);
      else if (metadata.isFile() && metadata.nlink === 1) { fs.unlinkSync(target); installer.syncDirectory(parent); }
      else error('partial_target_invalid', 'a transaction partial has an unsafe type');
    }
    if (!keepCandidate && journal.candidate && ![journal.prior_selected, journal.prior_running, journal.prior_healthy].includes(journal.candidate)) {
      const candidateRoot = path.join(layout.releases, journal.candidate);
      if (exists(candidateRoot)) {
        try { readRelease(layout, journal.candidate); removeExactTree(candidateRoot, layout.releases, layout.identity.uid); } catch (reason) { if (!(reason instanceof core.LauncherError)) throw reason; }
      }
    }
  }

  function parseConfigSchema(bytes) {
    const match = /^schema_version:\s*voice-agent\.config\.v([1-9][0-9]*)\s*$/m.exec(bytes.toString('utf8'));
    if (!match) error('config_schema_invalid', 'configuration schema is missing or ambiguous');
    return Number(match[1]);
  }

  function validateMigrationDescriptor(document) {
    exactKeys(document, MIGRATION_KEYS, 'migration_descriptor_invalid');
    if (!/^[a-z0-9][a-z0-9._-]{0,63}$/.test(document.id) || !Number.isSafeInteger(document.from) || !Number.isSafeInteger(document.to)
      || document.from < 1 || document.to !== document.from + 1 || !['config', 'data'].includes(document.scope)
      || !['schema-version', 'noop'].includes(document.operation) || typeof document.reversible !== 'boolean'
      || typeof document.destructive !== 'boolean' || typeof document.product_choice !== 'boolean' || !/^[0-9a-f]{64}$/.test(document.sha256)) {
      error('migration_descriptor_invalid', 'a migration descriptor is invalid');
    }
    const unsigned = { ...document }; delete unsigned.sha256;
    if (digest(Buffer.from(core.canonicalJson(unsigned))) !== document.sha256) error('migration_descriptor_invalid', 'a migration descriptor digest differs');
    return document;
  }

  async function loadMigrationDescriptors(acquired) {
    if (!acquired || typeof acquired.readEntry !== 'function') return [];
    let bytes;
    try { bytes = await acquired.readEntry('descriptors/config-migrations.json'); } catch { return []; }
    if (!Buffer.isBuffer(bytes)) return [];
    const document = core.parseCanonicalJson(bytes, 256 * 1024);
    exactKeys(document, ['migrations', 'schema'], 'migration_descriptor_invalid');
    if (document.schema !== 'voice-agent.config-migrations.v1' || !Array.isArray(document.migrations) || document.migrations.length > 64) error('migration_descriptor_invalid', 'the migration set is invalid');
    return document.migrations.map(validateMigrationDescriptor);
  }

  function migrationPlan(current, targetRange, descriptors, scope) {
    if (current >= targetRange.minimum && current <= targetRange.maximum) return [];
    if (current > targetRange.maximum) error('migration_backward_incompatible', 'the candidate cannot read the current schema');
    const plan = [];
    let version = current;
    while (version < targetRange.minimum) {
      const matches = descriptors.filter((item) => item.scope === scope && item.from === version);
      if (matches.length !== 1) error('migration_path_unavailable', 'an ordered migration path is unavailable');
      const descriptor = matches[0];
      if (descriptor.destructive || descriptor.product_choice || !descriptor.reversible) error('migration_requires_decision', 'an irreversible, destructive, or product-choice migration was refused before quiesce');
      plan.push(descriptor); version = descriptor.to;
    }
    return plan;
  }

  function prepareSnapshots(layout, journal, prior, candidate, acquired, dependencies) {
    const uid = layout.identity.uid;
    const snapshot = path.join(layout.migrations, journal.id);
    if (exists(snapshot)) error('migration_snapshot_ambiguous', 'the update snapshot already exists without a matching receipt');
    fs.mkdirSync(snapshot, { mode: 0o700 }); fs.chmodSync(snapshot, 0o700); installer.syncDirectory(layout.migrations);
    const configPath = path.join(layout.config, 'config.yaml');
    const config = core.readOwnedRegular(configPath, uid, [0o600], 4 * 1024 * 1024);
    const descriptorsPromise = loadMigrationDescriptors(acquired);
    return descriptorsPromise.then((descriptors) => {
      const configVersion = parseConfigSchema(config);
      const configPlan = migrationPlan(configVersion, candidate.manifest.config_schema, descriptors, 'config');
      const priorDataVersion = prior.record.data_schema.maximum;
      const dataPlan = migrationPlan(priorDataVersion, candidate.manifest.data_schema, descriptors, 'data');
      if (dataPlan.some((item) => item.operation !== 'noop')) error('migration_descriptor_invalid', 'protocol 1 data migrations must be snapshotted no-ops');
      let migrated = Buffer.from(config);
      for (const descriptor of configPlan) {
        if (descriptor.operation !== 'schema-version') error('migration_descriptor_invalid', 'configuration migration operation is unsupported');
        const text = migrated.toString('utf8').replace(/^schema_version:\s*voice-agent\.config\.v[1-9][0-9]*\s*$/m, `schema_version: voice-agent.config.v${descriptor.to}`);
        migrated = Buffer.from(text);
      }
      const migratedVersion = parseConfigSchema(migrated);
      if (migratedVersion < candidate.manifest.config_schema.minimum || migratedVersion > candidate.manifest.config_schema.maximum) error('migration_validation_failed', 'the staged configuration is not candidate-readable');
      const priorReadable = migratedVersion >= prior.manifest.config_schema.minimum && migratedVersion <= prior.manifest.config_schema.maximum;
      installer.atomicWrite(path.join(snapshot, 'config.before'), config, 0o600, uid);
      installer.atomicWrite(path.join(snapshot, 'config.candidate'), migrated, 0o600, uid);
      installer.atomicWrite(path.join(snapshot, 'install.before'), core.readOwnedRegular(layout.installRecord, uid, [0o600], 256 * 1024), 0o600, uid);
      const unitBytes = core.readOwnedRegular(layout.unit, uid, [0o600], 256 * 1024);
      installer.atomicWrite(path.join(snapshot, 'unit.before'), unitBytes, 0o600, uid);
      installer.writeJson(path.join(snapshot, 'receipt.json'), {
        schema: 'voice-agent.migration-snapshot.v1', config_before_sha256: digest(config), config_candidate_sha256: digest(migrated),
        config_from: configVersion, config_to: migratedVersion, data_from: priorDataVersion,
        data_to: dataPlan.length ? dataPlan.at(-1).to : priorDataVersion, prior_backward_readable: priorReadable,
      }, uid);
      fault(dependencies, 'action', 'migration_snapshot_prepared');
      return { snapshot, config, migrated, priorReadable, unitBytes };
    });
  }

  function readSnapshot(layout, journal) {
    const root = path.join(layout.migrations, journal.config_snapshot);
    installer.inspectManagedPath(root, layout.identity.uid, 0o700);
    const read = (name) => core.readOwnedRegular(path.join(root, name), layout.identity.uid, [0o600], 4 * 1024 * 1024);
    const receipt = JSON.parse(read('receipt.json').toString('utf8'));
    exactKeys(receipt, ['config_before_sha256', 'config_candidate_sha256', 'config_from', 'config_to', 'data_from', 'data_to', 'prior_backward_readable', 'schema'], 'migration_snapshot_invalid');
    const config = read('config.before'); const candidate = read('config.candidate'); const unit = read('unit.before'); const install = read('install.before');
    if (receipt.schema !== 'voice-agent.migration-snapshot.v1' || digest(config) !== receipt.config_before_sha256 || digest(candidate) !== receipt.config_candidate_sha256
      || typeof receipt.prior_backward_readable !== 'boolean') error('migration_snapshot_invalid', 'the configuration snapshot differs from its receipt');
    return { root, receipt, config, candidate, unit, install };
  }

  function removeSnapshot(layout, journal) {
    const root = path.join(layout.migrations, journal.config_snapshot);
    if (exists(root)) removeExactTree(root, layout.migrations, layout.identity.uid);
  }

  function releaseLike(item) { return { version: item.record.version, artifact_sha256: item.record.artifact_sha256, build_id: item.record.build_id }; }

  async function observeHealthyRunning(layout, service) {
    const snapshot = await service.probe({ deadline_ms: 0 });
    const runtime = snapshot && snapshot.runtime;
    if (!runtime || typeof runtime.release_id !== 'string') error('prior_runtime_unavailable', 'the exact running release could not be established');
    const running = readRelease(layout, runtime.release_id);
    if (!installer.validateReadiness(snapshot, releaseLike(running), layout.identity.uid)) error('prior_runtime_unready', 'the running release is not exact five-component ready');
    return { snapshot, running };
  }

  function serviceState(snapshot, unitBytes) {
    return { was_active: snapshot.service_active === true, was_enabled: snapshot.service_enabled === true, unit_sha256: digest(unitBytes) };
  }

  async function stopAndProve(service, request) {
    if (typeof service.quiesce === 'function') await service.quiesce(request);
    else await service.stop({ unit: installer.SERVICE_UNIT, graceful_deadline_ms: 75000 });
    if (typeof service.probeStopped === 'function') {
      const stopped = await service.probeStopped(request);
      if (!stopped || stopped.process_generation_gone !== true || stopped.listener_gone !== true) error('service_quiesce_unproven', 'the prior process/listener generation did not quiesce');
    }
  }

  async function startOnce(service, request) {
    if (typeof service.startExactlyOnce === 'function') await service.startExactlyOnce(request);
    else await service.enableAndStart({ unit: installer.SERVICE_UNIT, start_once: true });
  }

  async function waitReady(service, release, layout, dependencies) {
    const started = dependencies.clock.monotonic();
    while (dependencies.clock.monotonic() - started <= installer.STARTUP_DEADLINE_MS) {
      const value = await service.probe({ release_id: release.id, build_id: release.record.build_id, deadline_ms: installer.STARTUP_DEADLINE_MS });
      if (installer.validateReadiness(value, releaseLike(release), layout.identity.uid)) return value;
      if (dependencies.clock.monotonic() - started === installer.STARTUP_DEADLINE_MS) break;
      await dependencies.clock.sleep(Math.min(1000, installer.STARTUP_DEADLINE_MS - (dependencies.clock.monotonic() - started)));
    }
    error('candidate_not_ready', 'the exact candidate did not become five-component ready within 300 seconds');
  }

  function updateInstallationRecord(layout, candidate, rollbackId, sequence, clock) {
    const previous = installer.readPrivateJson(layout.installRecord, layout.identity.uid);
    installer.writeJson(layout.installRecord, {
      schema: 'voice-agent.installation.v1', installation_id: previous.installation_id, channel: 'stable', channel_sequence: sequence,
      launcher_protocol: core.LAUNCHER_PROTOCOL, release_id: candidate.id, healthy_release: candidate.id, rollback_release: rollbackId,
      version: candidate.record.version, build_id: candidate.record.build_id, artifact_sha256: candidate.record.artifact_sha256,
      installed_at: timestamp(clock),
    }, layout.identity.uid);
  }

  async function rollback(layout, journal, dependencies, originalCode) {
    let next = persistJournal(layout, journal, 'rolling_back', dependencies, { failure_code: originalCode });
    try {
      const prior = readRelease(layout, next.prior_healthy);
      const snapshot = readSnapshot(layout, next);
      try { await dependencies.service.stop({ unit: installer.SERVICE_UNIT, graceful_deadline_ms: 75000 }); } catch {}
      installer.atomicWrite(path.join(layout.config, 'config.yaml'), snapshot.config, 0o600, layout.identity.uid);
      atomicPointer(layout, layout.current, prior.id, next.id);
      atomicPointer(layout, layout.rollback, prior.id, next.id);
      if (!core.readOwnedRegular(layout.unit, layout.identity.uid, [0o600], 256 * 1024).equals(snapshot.unit)) await dependencies.service.installUnit({ path: layout.unit, bytes: snapshot.unit, mode: 0o600, uid: layout.identity.uid });
      installer.atomicWrite(layout.installRecord, snapshot.install, 0o600, layout.identity.uid);
      fault(dependencies, 'action', 'prior_restored');
      next = persistJournal(layout, next, 'restoring', dependencies, { receipts: { prior_restored: true } });
      await startOnce(dependencies.service, { unit: installer.SERVICE_UNIT, release_id: prior.id, start_once: true });
      fault(dependencies, 'action', 'prior_started');
      next = persistJournal(layout, next, 'starting_prior', dependencies, { receipts: { service_started: true } });
      await waitReady(dependencies.service, prior, layout, dependencies);
      fault(dependencies, 'action', 'prior_readiness_proved');
      next = persistJournal(layout, next, 'prior_ready', dependencies, { receipts: { prior_ready: true } });
      next = persistJournal(layout, next, 'failed_safe', dependencies, { receipts: { terminal: true } });
      removeTransactionPartials(layout, next, false); removeSnapshot(layout, next);
      fs.unlinkSync(layout.updateJournal); installer.syncDirectory(layout.transactions);
      error('update_failed_safe', `candidate failed (${originalCode}); the exact prior release was restored and proved ready`);
    } catch (reason) {
      if (reason instanceof core.LauncherError && reason.code === 'update_failed_safe') throw reason;
      try { persistJournal(layout, next, 'failed_needs_repair', dependencies, { failure_code: originalCode, receipts: { terminal: true } }); } catch {}
      error('update_failed_needs_repair', `candidate failed (${originalCode}) and prior readiness could not be restored; recovery material was retained`);
    }
  }

  async function commitCandidate(layout, journal, candidate, sequence, dependencies) {
    let next = persistJournal(layout, journal, 'committing', dependencies);
    const recordPath = path.join(candidate.root, 'release-record.json');
    const record = core.validateReleaseRecord(JSON.parse(core.readOwnedRegular(recordPath, layout.identity.uid, [0o400], 256 * 1024).toString('utf8')));
    if (record.readiness.state !== 'ready') {
      record.readiness = { state: 'ready', checked_at: timestamp(dependencies.clock) };
      fs.chmodSync(candidate.root, 0o700); installer.atomicWrite(recordPath, Buffer.from(core.canonicalJson(record)), 0o400, layout.identity.uid); fs.chmodSync(candidate.root, 0o500); installer.syncDirectory(layout.releases);
    }
    atomicPointer(layout, layout.current, candidate.id, next.id);
    atomicPointer(layout, layout.rollback, next.prior_healthy, next.id);
    updateInstallationRecord(layout, candidate, next.prior_healthy, sequence, dependencies.clock);
    fault(dependencies, 'action', 'healthy_committed');
    next = persistJournal(layout, next, 'healthy', dependencies, { receipts: { terminal: true } });
    const final = await dependencies.service.probe({ release_id: candidate.id, build_id: candidate.record.build_id, deadline_ms: 0 });
    if (!installer.validateReadiness(final, releaseLike(candidate), layout.identity.uid)) return rollback(layout, next, dependencies, 'candidate_lost_readiness');
    const gc = collectReleases(layout, next, [candidate.id, next.prior_healthy]);
    fault(dependencies, 'action', 'post_gc_complete');
    removeTransactionPartials(layout, next, true); removeSnapshot(layout, next);
    fs.unlinkSync(layout.updateJournal); installer.syncDirectory(layout.transactions);
    return { state: 'updated_healthy', release_id: candidate.id, version: candidate.record.version, rollback_release: next.prior_healthy, gc };
  }

  async function priorStillExactReady(layout, journal, dependencies) {
    if (!journal.prior_healthy) return false;
    try {
      const prior = readRelease(layout, journal.prior_healthy);
      const probe = await dependencies.service.probe({ release_id: prior.id, build_id: prior.record.build_id, deadline_ms: 0 });
      return installer.validateReadiness(probe, releaseLike(prior), layout.identity.uid);
    } catch { return false; }
  }

  async function recover(layout, dependencies) {
    if (!exists(layout.updateJournal)) return null;
    let journal = readJournal(layout);
    const interruptedPhase = journal.phase;
    journal = persistJournal(layout, journal, 'recovering', dependencies);
    const serviceUnchanged = !journal.receipts.service_stopped && await priorStillExactReady(layout, journal, dependencies);
    if ((!POST_QUIESCE.has(interruptedPhase) && !journal.receipts.service_stopped) || serviceUnchanged) {
      removeTransactionPartials(layout, journal, false); removeSnapshot(layout, journal);
      fs.unlinkSync(layout.updateJournal); installer.syncDirectory(layout.transactions);
      return { state: 'pre_quiesce_recovered' };
    }
    if (!journal.candidate || !journal.prior_healthy) error('update_journal_invalid', 'post-quiesce recovery lacks exact release custody');
    const candidate = readRelease(layout, journal.candidate);
    try {
      const probe = await dependencies.service.probe({ release_id: candidate.id, build_id: candidate.record.build_id, deadline_ms: 0 });
      if (installer.validateReadiness(probe, releaseLike(candidate), layout.identity.uid)) {
        journal = persistJournal(layout, journal, 'ready', dependencies, { receipts: { candidate_ready: true } });
        const install = installer.readPrivateJson(layout.installRecord, layout.identity.uid);
        return commitCandidate(layout, journal, candidate, Math.max(install.channel_sequence, candidate.record.channel_sequence), dependencies);
      }
    } catch {}
    return rollback(layout, journal, dependencies, journal.failure_code || 'update_interrupted');
  }

  function semverCompare(left, right) {
    const parse = (value) => value.split('-', 1)[0].split('.').map(Number);
    const a = parse(left); const b = parse(right);
    for (let index = 0; index < 3; index += 1) if (a[index] !== b[index]) return a[index] - b[index];
    return left.includes('-') === right.includes('-') ? left.localeCompare(right) : left.includes('-') ? -1 : 1;
  }

  function selectCandidate(channel, current) {
    const compatible = channel.releases.filter((item) => item.platform === core.SUPPORTED_PLATFORM && item.minimum_launcher_protocol <= core.LAUNCHER_PROTOCOL)
      .sort((left, right) => semverCompare(right.version, left.version));
    if (!compatible.length) error('platform_release_unavailable', 'the signed channel has no compatible candidate');
    const selected = compatible[0];
    if (semverCompare(selected.version, current.record.version) < 0) error('channel_release_rollback', 'the signed channel attempted an ordinary release downgrade');
    return selected;
  }

  async function updateVoiceAgent(options = {}) {
    const testMode = options.testMode === true;
    if (!testMode && options.dependencies) error('test_injection_forbidden', 'update injection is test-only');
    const dependencies = options.dependencies || installer.defaultDependencies();
    const layout = installer.layoutFor(testMode ? dependencies.identity : undefined, testMode);
    const output = dependencies.output || { info() {} };
    if (!exists(layout.installRecord) || !exists(layout.current)) error('canonical_install_required', 'voice-agent update requires a completed canonical fresh installation');
    for (const directory of [layout.data, layout.releases, layout.transactions, layout.migrations, layout.config, layout.cache, layout.downloads]) {
      installer.inspectManagedPath(directory, layout.identity.uid, 0o700);
    }
    installer.inspectManagedPath(layout.runtime, layout.identity.uid, 0o700);
    installer.inspectManagedPath(path.join(layout.config, 'config.yaml'), layout.identity.uid, 0o600, 'file');
    installer.inspectManagedPath(layout.unit, layout.identity.uid, 0o600, 'file');
    const lock = acquireExclusiveLock(layout, dependencies);
    let journal = null;
    try {
      const recovered = await recover(layout, dependencies);
      if (recovered && recovered.state !== 'pre_quiesce_recovered') {
        if (recovered.state === 'updated_healthy') output.info(`Recovered update: Voice Agent ${recovered.version} is exact-ready.`);
        return recovered;
      }
      const selectedId = pointerId(layout, layout.current);
      const selected = readRelease(layout, selectedId);
      const { snapshot: priorSnapshot, running: prior } = await observeHealthyRunning(layout, dependencies.service);
      const installRecord = installer.readPrivateJson(layout.installRecord, layout.identity.uid);
      let signed;
      try { signed = await dependencies.source.acquireChannel({ offline: options.offline === true }); }
      catch (reason) {
        if (['channel_unavailable', 'network_unavailable', 'release_authority_unprovisioned'].includes(reason && reason.code)) {
          output.info(`Could not check the stable channel. Current release ${prior.record.version} remains healthy; latest is unknown and no change was made.`);
          return { state: 'metadata_unavailable_current_healthy', release_id: prior.id, latest_known: false };
        }
        throw reason;
      }
      if (!signed || !signed.channelBytes || !signed.signatureBytes || !signed.publicKeyPem) error('channel_unavailable', 'signed stable channel metadata is unavailable');
      const channel = core.verifySignedChannel(signed.channelBytes, signed.signatureBytes, signed.publicKeyPem, { now: dependencies.clock.now(), trustedSequence: installRecord.channel_sequence });
      const release = selectCandidate(channel, selected);
      if (releaseId(release) === selected.id && prior.id === selected.id) {
        const gc = collectReleases(layout, null, [selected.id, pointerId(layout, layout.rollback)]);
        output.info(options.offline || signed.cached === true
          ? `Using previously verified cached stable metadata; latest is unknown. Voice Agent ${selected.record.version} is selected, running, and ready.`
          : `Voice Agent ${selected.record.version} is selected, running, and ready. No update needed.`);
        return { state: 'already_current_healthy', release_id: selected.id, latest_known: !(options.offline || signed.cached === true), gc };
      }

      journal = {
        schema: UPDATE_SCHEMA, id: dependencies.randomBytes(16).toString('hex'), requested_channel: 'stable', phase: 'checking',
        prior_selected: selected.id, prior_running: prior.id, prior_healthy: prior.id, candidate: releaseId(release), config_snapshot: null,
        service: { was_active: priorSnapshot.service_active === true, was_enabled: priorSnapshot.service_enabled === true, unit_sha256: null },
        receipts: emptyReceipts(), failure_code: null, started_at: timestamp(dependencies.clock), updated_at: timestamp(dependencies.clock),
      };
      journal.config_snapshot = journal.id;
      journal.service = serviceState(priorSnapshot, core.readOwnedRegular(layout.unit, layout.identity.uid, [0o600], 256 * 1024));
      installer.writeJson(layout.updateJournal, journal, layout.identity.uid); fault(dependencies, 'write', 'checking');
      journal = persistJournal(layout, journal, 'staging', dependencies, { receipts: { channel_checked: true } });
      const preGc = collectReleases(layout, journal, [selected.id, prior.id]);
      fault(dependencies, 'action', 'pre_gc_complete');
      journal = persistJournal(layout, journal, 'staging', dependencies, { receipts: { pre_gc_complete: true } });

      const acquired = await dependencies.source.acquireArtifact(release, { offline: options.offline === true });
      if (!acquired || !Buffer.isBuffer(acquired.artifactBytes) || !Buffer.isBuffer(acquired.manifestBytes) || !Array.isArray(acquired.archiveEntries) || typeof acquired.readEntry !== 'function') error('artifact_unavailable', 'the exact authorized candidate artifact is unavailable');
      const partial = path.join(layout.downloads, `update-${journal.id}.partial`);
      const verifiedDownload = path.join(layout.downloads, `update-${journal.id}.verified`);
      installer.atomicWrite(partial, acquired.artifactBytes, 0o600, layout.identity.uid); fault(dependencies, 'action', 'artifact_downloaded');
      const manifest = core.verifyPlatformArtifact(acquired.artifactBytes, acquired.manifestBytes, release);
      const requirements = installer.artifactPreflight(manifest, release, acquired.archiveEntries, acquired.manifestBytes);
      const payloadBytes = manifest.entries.reduce((sum, item) => sum + (item.type === 'file' ? item.size : 0), 0);
      const facts = await dependencies.host.inspectCompatibility({ release, manifest, requirements, layout });
      if (!facts || !facts.assets || facts.assets.model_descriptor_sha256 !== requirements.model_descriptor_sha256
        || facts.assets.runtime_descriptor_sha256 !== requirements.runtime_descriptor_sha256 || facts.assets.model_available !== true
        || facts.assets.runtime_available !== true || facts.assets.runtime_compatible !== true) {
        error('runtime_assets_unavailable', 'the exact signed candidate model/runtime assets are unavailable or incompatible');
      }
      if (!Number.isSafeInteger(facts.free_bytes) || facts.free_bytes < installer.FREE_SPACE_RESERVE + release.artifact_bytes + payloadBytes) error('insufficient_space', 'genuine disk shortage remains after safe owned release collection');
      fs.renameSync(partial, verifiedDownload); installer.syncDirectory(layout.downloads); fault(dependencies, 'action', 'artifact_verified');
      journal = persistJournal(layout, journal, 'verified', dependencies, { receipts: { artifact_verified: true } });

      const candidateId = releaseId(release);
      const candidateRoot = path.join(layout.releases, candidateId);
      let candidate;
      if (exists(candidateRoot)) candidate = readRelease(layout, candidateId);
      else {
        const stage = path.join(layout.transactions, `stage-${journal.id}`);
        fs.mkdirSync(stage, { mode: 0o700 }); fs.chmodSync(stage, 0o700);
        await installer.extractVerifiedArchive(stage, manifest, acquired.manifestBytes, acquired);
        installer.verifyExtractedTree(stage, manifest, acquired.manifestBytes, layout.identity.uid);
        installer.atomicWrite(path.join(stage, 'release-record.json'), Buffer.from(core.canonicalJson({
          schema: 'voice-agent.release-record.v1', release_id: candidateId, version: release.version, build_id: release.build_id,
          platform: release.platform, channel: 'stable', channel_sequence: channel.sequence, artifact_sha256: release.artifact_sha256,
          artifact_bytes: release.artifact_bytes, manifest_sha256: release.manifest_sha256, launcher_protocol: core.LAUNCHER_PROTOCOL,
          application_protocol: manifest.application_protocol, data_schema: manifest.data_schema, service_template_sha256: manifest.service_template_sha256,
          verified_at: timestamp(dependencies.clock), readiness: { state: 'not_verified', checked_at: null },
        })), 0o400, layout.identity.uid);
        fs.renameSync(stage, candidateRoot); fs.chmodSync(candidateRoot, 0o500); installer.syncDirectory(layout.releases);
        candidate = readRelease(layout, candidateId);
      }
      fault(dependencies, 'action', 'release_staged');
      journal = persistJournal(layout, journal, 'verified', dependencies, { receipts: { release_staged: true } });
      await prepareSnapshots(layout, journal, prior, candidate, acquired, dependencies);
      journal = persistJournal(layout, journal, 'migrations_prepared', dependencies, { receipts: { migration_prepared: true } });
      journal = persistJournal(layout, journal, 'prior_custody', dependencies, { receipts: { prior_custody: true } });

      journal = persistJournal(layout, journal, 'quiescing', dependencies);
      await stopAndProve(dependencies.service, { unit: installer.SERVICE_UNIT, prior_release_id: prior.id, deadline_ms: 75000 });
      fault(dependencies, 'action', 'service_stopped');
      journal = persistJournal(layout, journal, 'quiescing', dependencies, { receipts: { service_stopped: true } });
      const snapshot = readSnapshot(layout, journal);
      const currentConfig = core.readOwnedRegular(path.join(layout.config, 'config.yaml'), layout.identity.uid, [0o600], 4 * 1024 * 1024);
      if (digest(currentConfig) !== snapshot.receipt.config_before_sha256) error('config_cas_mismatch', 'configuration changed after migration preparation');
      installer.atomicWrite(path.join(layout.config, 'config.yaml'), snapshot.candidate, 0o600, layout.identity.uid);
      atomicPointer(layout, layout.rollback, prior.id, journal.id);
      atomicPointer(layout, layout.current, candidate.id, journal.id);
      fault(dependencies, 'action', 'pointer_activated');
      journal = persistJournal(layout, journal, 'activating', dependencies, { receipts: { pointer_activated: true } });
      const unit = installer.renderUnit(layout, candidate.root);
      if (!core.readOwnedRegular(layout.unit, layout.identity.uid, [0o600], 256 * 1024).equals(unit)) {
        await dependencies.service.installUnit({ path: layout.unit, bytes: unit, mode: 0o600, uid: layout.identity.uid });
        fault(dependencies, 'action', 'unit_reloaded');
        journal = persistJournal(layout, journal, 'activating', dependencies, { receipts: { unit_reloaded: true } });
      }
      journal = persistJournal(layout, journal, 'starting', dependencies);
      await startOnce(dependencies.service, { unit: installer.SERVICE_UNIT, release_id: candidate.id, start_once: true });
      fault(dependencies, 'action', 'candidate_started');
      journal = persistJournal(layout, journal, 'starting', dependencies, { receipts: { service_started: true } });
      await waitReady(dependencies.service, candidate, layout, dependencies);
      fault(dependencies, 'action', 'candidate_readiness_proved');
      journal = persistJournal(layout, journal, 'ready', dependencies, { receipts: { candidate_ready: true } });
      const result = await commitCandidate(layout, journal, candidate, channel.sequence, dependencies);
      output.info(`Voice Agent ${candidate.record.version} is healthy; rollback ${prior.record.version} retained; removed ${result.gc.removed + preGc.removed} older owned program releases.`);
      return result;
    } catch (reason) {
      if (reason && ['update_interrupted', 'update_failed_safe', 'update_failed_needs_repair'].includes(reason.code)) throw reason;
      if (journal && exists(layout.updateJournal)) {
        const currentJournal = readJournal(layout);
        const priorUnchanged = !currentJournal.receipts.service_stopped && await priorStillExactReady(layout, currentJournal, dependencies);
        if (currentJournal.receipts.service_stopped || (POST_QUIESCE.has(currentJournal.phase) && !priorUnchanged)) return rollback(layout, currentJournal, dependencies, reason.code || 'update_failed');
        try {
          removeTransactionPartials(layout, currentJournal, false); removeSnapshot(layout, currentJournal);
          const terminal = persistJournal(layout, currentJournal, 'cleanup', dependencies, { failure_code: reason.code || 'update_failed', receipts: { terminal: true } });
          if (terminal.receipts.terminal) { fs.unlinkSync(layout.updateJournal); installer.syncDirectory(layout.transactions); }
        } catch {}
      }
      throw reason;
    } finally { lock.release(); }
  }

  return {
    PHASES, POST_QUIESCE, RECEIPT_KEYS, UPDATE_SCHEMA, acquireExclusiveLock, collectReleases,
    loadMigrationDescriptors, migrationPlan, parseConfigSchema, readRelease, updateVoiceAgent, validateJournal,
  };
};
