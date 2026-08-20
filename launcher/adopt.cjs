'use strict';

module.exports = function createAdopter(core, installer, updater) {
  const crypto = require('node:crypto');
  const fs = require('node:fs');
  const os = require('node:os');
  const path = require('node:path');
  const { spawnSync } = require('node:child_process');
  const agentEnvironment = core.loadAgentEnvironment();

  const SERVICE_NAME = 'voice-agent-v2.service';
  const ADOPTION_SCHEMA = 'voice-agent.legacy-adoption.v1';
  const PHASES = new Set(['discovered', 'prepared', 'legacy_stopped', 'candidate_started', 'candidate_ready', 'committed', 'failed_safe', 'failed_needs_repair']);
  const RECEIPTS = ['prior_imported', 'selected_imported', 'config_committed', 'docker_recorded', 'environment_before', 'environment_after', 'candidate_staged', 'user_service_prepared', 'legacy_stopped', 'candidate_activated', 'candidate_started', 'candidate_ready', 'prior_restored', 'legacy_retired', 'terminal'];

  function error(code, message) { throw new core.LauncherError(code, message); }
  function digest(bytes) { return crypto.createHash('sha256').update(bytes).digest('hex'); }
  function exists(filename) { try { fs.lstatSync(filename); return true; } catch (reason) { if (reason.code === 'ENOENT') return false; throw reason; } }
  function timestamp(clock) { return clock.now().toISOString().replace('.000Z', 'Z'); }
  function fault(dependencies, kind, name) {
    if (!dependencies.fault) return;
    if (kind === 'write' && typeof dependencies.fault.afterDurablePhase === 'function') dependencies.fault.afterDurablePhase(name);
    if (kind === 'action' && typeof dependencies.fault.afterAction === 'function') dependencies.fault.afterAction(name);
  }
  function releaseId(release) { return `${release.version}-${release.artifact_sha256.slice(0, 12)}`; }
  function importedId(legacyId) { return `legacy-${legacyId}`; }
  function emptyReceipts() { return Object.fromEntries(RECEIPTS.map((name) => [name, false])); }

  function adoptionLayout(layout) {
    return { ...layout, adoptionJournal: path.join(layout.transactions, 'adoption.json') };
  }

  function normalizeJournal(document) {
    const oldKeys = ['candidate', 'config_canonical_sha256', 'config_source_sha256', 'failure_code', 'id', 'legacy_candidate', 'phase', 'prior_healthy', 'prior_running', 'receipts', 'schema', 'started_at', 'updated_at'];
    const oldReceipts = RECEIPTS.filter((name) => !['environment_before', 'environment_after'].includes(name));
    if (document && typeof document === 'object' && !Array.isArray(document)
      && Object.keys(document).sort().join('\0') === oldKeys.sort().join('\0')
      && document.receipts && typeof document.receipts === 'object' && !Array.isArray(document.receipts)
      && Object.keys(document.receipts).sort().join('\0') === oldReceipts.sort().join('\0')) {
      return {
        ...document, agent_environment: { before: null, after: null },
        receipts: { ...emptyReceipts(), ...document.receipts },
      };
    }
    return document;
  }

  function validateJournal(document) {
    document = normalizeJournal(document);
    const keys = ['agent_environment', 'candidate', 'config_canonical_sha256', 'config_source_sha256', 'failure_code', 'id', 'legacy_candidate', 'phase', 'prior_healthy', 'prior_running', 'receipts', 'schema', 'started_at', 'updated_at'];
    if (!document || typeof document !== 'object' || Array.isArray(document) || Object.keys(document).sort().join('\0') !== keys.sort().join('\0')
        || document.schema !== ADOPTION_SCHEMA || !/^[0-9a-f]{32}$/.test(document.id) || !PHASES.has(document.phase)
        || !/^legacy-[0-9a-f]{24}$/.test(document.prior_running) || document.prior_healthy !== document.prior_running
        || !/^legacy-[0-9a-f]{24}$/.test(document.legacy_candidate) || !/^[0-9A-Za-z][0-9A-Za-z.+-]{0,95}$/.test(document.candidate)
        || ![document.config_source_sha256, document.config_canonical_sha256].every((value) => /^[0-9a-f]{64}$/.test(value))
        || (document.failure_code !== null && !/^[a-z0-9_]{1,64}$/.test(document.failure_code))
        || !/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$/.test(document.started_at)
        || !/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$/.test(document.updated_at)) error('legacy_adoption_journal_invalid', 'the legacy adoption journal is invalid');
    if (!document.agent_environment || Object.keys(document.agent_environment).sort().join('\0') !== ['after', 'before'].sort().join('\0')) error('legacy_adoption_journal_invalid', 'AgentEnvironment custody receipt is invalid');
    try {
      if (document.agent_environment.before !== null) agentEnvironment.validatePreservation(document.agent_environment.before);
      if (document.agent_environment.after !== null) agentEnvironment.validatePreservation(document.agent_environment.after);
    } catch { error('legacy_adoption_journal_invalid', 'AgentEnvironment custody receipt is invalid'); }
    if (!document.receipts || Object.keys(document.receipts).sort().join('\0') !== [...RECEIPTS].sort().join('\0')
        || RECEIPTS.some((name) => typeof document.receipts[name] !== 'boolean')) error('legacy_adoption_journal_invalid', 'a legacy adoption receipt is invalid');
    return document;
  }

  function writeJournal(layout, journal, phase, dependencies, changes = {}) {
    const next = { ...journal, ...changes, phase, updated_at: timestamp(dependencies.clock) };
    if (changes.receipts) next.receipts = { ...journal.receipts, ...changes.receipts };
    validateJournal(next);
    installer.writeJson(layout.adoptionJournal, next, layout.identity.uid);
    fault(dependencies, 'write', phase);
    return next;
  }

  function readJournal(layout) { return validateJournal(installer.readPrivateJson(layout.adoptionJournal, layout.identity.uid)); }

  function inspectLegacy(options, dependencies, layout) {
    const legacyRoot = options.legacyRoot;
    const uid = layout.identity.uid;
    core.noSymlinkComponents(legacyRoot);
    core.ownedDirectory(legacyRoot, uid, 0o700);
    core.ownedDirectory(path.join(legacyRoot, 'releases'), uid, 0o700);
    const selectedRoot = core.safePointer(legacyRoot, 'current', /^[0-9a-f]{24}$/, uid);
    const previousRoot = core.safePointer(legacyRoot, 'previous', /^[0-9a-f]{24}$/, uid);
    if (!selectedRoot || previousRoot) error('legacy_split_not_eligible', 'legacy adoption requires selected-new, running-old, and no prior pointer');
    const selected = core.validateLegacyRelease(selectedRoot, legacyRoot, uid);
    const snapshot = dependencies.legacyProbe.inspectLegacySync
      ? dependencies.legacyProbe.inspectLegacySync({ serviceName: SERVICE_NAME, uid }) : null;
    return Promise.resolve(snapshot || dependencies.legacyProbe.inspectLegacy({ serviceName: SERVICE_NAME, uid })).then((observed) => {
      const runtimeId = observed && observed.runtime && observed.runtime.release_id;
      if (!/^[0-9a-f]{24}$/.test(runtimeId || '') || runtimeId === selected.release_id) error('legacy_split_not_eligible', 'the exact selected-new/running-old split is absent');
      const running = core.validateLegacyRelease(path.join(legacyRoot, 'releases', runtimeId), legacyRoot, uid);
      core.validateLegacyRunning(running, observed, {
        expectedUid: uid, serviceUnitPath: options.serviceUnitPath, serviceUnitOwner: options.serviceUnitOwner,
        expectedPythonPath: options.expectedPythonPath,
      });
      if (selected.document.configuration_path !== running.document.configuration_path
          || selected.document.configuration_locator_sha256 !== running.document.configuration_locator_sha256
          || selected.document.configuration_fingerprint !== running.document.configuration_fingerprint) {
        error('legacy_config_identity_mismatch', 'selected and running legacy configuration custody differs');
      }
      let docker;
      if (!observed.docker) docker = { state: 'unavailable', record: agentEnvironment.endpointRecord(uid) };
      else {
        try { docker = { state: 'available', record: agentEnvironment.endpointRecord(uid, { ...observed.docker, service_endpoint: agentEnvironment.endpointFor(uid) }) }; }
        catch { error('legacy_docker_endpoint_invalid', 'a rootful, foreign, group-authorized, ambient, or unverifiable Docker endpoint was refused'); }
      }
      return { selected, running, snapshot: observed, docker, legacyRoot };
    });
  }

  function copyTree(source, destination, uid) {
    const sourceMetadata = fs.lstatSync(source);
    if (!sourceMetadata.isDirectory() || sourceMetadata.isSymbolicLink() || sourceMetadata.uid !== uid) error('legacy_release_unowned', 'legacy import source custody is invalid');
    fs.mkdirSync(destination, { mode: 0o700 }); fs.chmodSync(destination, 0o700);
    function visit(from, to) {
      for (const name of fs.readdirSync(from).sort()) {
        if (from === source && name === 'release.json') continue;
        const sourcePath = path.join(from, name); const targetPath = path.join(to, name);
        const metadata = fs.lstatSync(sourcePath);
        if (metadata.uid !== uid) error('legacy_release_unowned', 'legacy import contains an unowned path');
        if (metadata.isDirectory()) { fs.mkdirSync(targetPath, { mode: 0o700 }); visit(sourcePath, targetPath); fs.chmodSync(targetPath, metadata.mode & 0o777); }
        else if (metadata.isFile()) {
          const bytes = core.readOwnedRegular(sourcePath, uid, null, 1024 * 1024 * 1024);
          fs.writeFileSync(targetPath, bytes, { flag: 'wx', mode: metadata.mode & 0o777 }); fs.chmodSync(targetPath, metadata.mode & 0o777);
        } else if (metadata.isSymbolicLink()) fs.symlinkSync(fs.readlinkSync(sourcePath), targetPath);
        else error('legacy_release_invalid', 'legacy import contains an unsupported path type');
      }
      installer.syncDirectory(to);
    }
    visit(source, destination);
  }

  function importLegacyRelease(layout, legacy, readiness, dependencies) {
    const id = importedId(legacy.release_id);
    const root = path.join(layout.releases, id);
    if (exists(root)) { validateImportedRelease(layout, id); return id; }
    const stage = path.join(layout.transactions, `import-${legacy.release_id}`);
    if (exists(stage)) error('legacy_import_stage_ambiguous', 'an unreceipted legacy import stage exists');
    fs.mkdirSync(stage, { mode: 0o700 });
    copyTree(legacy.root, path.join(stage, 'payload'), layout.identity.uid);
    const releaseBytes = core.readOwnedRegular(path.join(legacy.root, 'release.json'), layout.identity.uid, [0o600], 256 * 1024);
    const unitBytes = core.readOwnedRegular(path.join(legacy.root, 'ops', 'systemd', SERVICE_NAME), layout.identity.uid, null, 256 * 1024);
    installer.atomicWrite(path.join(stage, 'legacy-import-record.json'), Buffer.from(core.canonicalJson({
      schema: 'voice-agent.legacy-import-release.v1', release_id: id, legacy_release_id: legacy.release_id,
      build_id: legacy.build_id, immutable_inventory_sha256: legacy.document.release_tree_sha256,
      release_metadata_sha256: digest(releaseBytes), service_unit_sha256: digest(unitBytes),
      application_manifest: 'unsupported', readiness, checked_at: timestamp(dependencies.clock),
    })), 0o400, layout.identity.uid);
    fs.renameSync(stage, root); fs.chmodSync(path.join(root, 'payload'), 0o500); fs.chmodSync(root, 0o500); installer.syncDirectory(layout.releases);
    validateImportedRelease(layout, id);
    return id;
  }

  function validateImportedRelease(layout, id) {
    const root = path.join(layout.releases, id);
    installer.inspectManagedPath(root, layout.identity.uid, 0o500);
    const record = core.validateLegacyImportRecord(JSON.parse(core.readOwnedRegular(path.join(root, 'legacy-import-record.json'), layout.identity.uid, [0o400], 256 * 1024).toString('utf8')));
    if (record.release_id !== id || exists(path.join(root, 'payload', 'release.json'))
        || core.legacyTreeDigest(path.join(root, 'payload'), layout.identity.uid) !== record.immutable_inventory_sha256
        || digest(core.readOwnedRegular(path.join(root, 'payload', 'ops', 'systemd', SERVICE_NAME), layout.identity.uid, null, 256 * 1024)) !== record.service_unit_sha256) {
      error('legacy_import_record_invalid', 'imported legacy bytes differ from custody receipts');
    }
    return { id, root, record };
  }

  function canonicalServiceEnv(source, endpoint) {
    const text = source.toString('utf8');
    if (text.includes('\0') || (!text.endsWith('\n') && text.length)) error('legacy_config_invalid', 'legacy private configuration framing is invalid');
    const names = new Set();
    for (const line of text.split('\n')) {
      if (!line || /^\s*#/.test(line)) continue;
      const match = /^([A-Z][A-Z0-9_]*)=/.exec(line);
      if (!match || names.has(match[1])) error('legacy_config_invalid', 'legacy private configuration names are invalid or duplicated');
      names.add(match[1]);
    }
    const additions = {
      VOICE_AGENT_DIAGNOSTIC_CONTENT_CAPTURE: 'false', VOICE_AGENT_AGENT_RUN_ENABLED: 'false',
      VOICE_AGENT_TELEGRAM_ENABLED: 'false', VOICE_AGENT_DOCKER_HOST: endpoint || '',
    };
    let output = text;
    for (const [name, value] of Object.entries(additions)) if (!names.has(name)) output += `${name}=${value}\n`;
    return Buffer.from(output);
  }

  function preparePrivateConfig(layout, evidence, journal, dependencies) {
    const sourcePath = evidence.running.document.configuration_path;
    core.noSymlinkComponents(sourcePath);
    const source = core.readOwnedRegular(sourcePath, layout.identity.uid, [0o600], 4 * 1024 * 1024);
    if (digest(source) !== journal.config_source_sha256) error('legacy_config_cas_mismatch', 'legacy private configuration changed during adoption');
    const endpoint = evidence.docker.state === 'available' ? agentEnvironment.endpointFor(layout.identity.uid) : '';
    const canonical = canonicalServiceEnv(source, endpoint);
    if (digest(canonical) !== journal.config_canonical_sha256) error('legacy_config_cas_mismatch', 'canonical private configuration plan changed');
    const snapshot = path.join(layout.migrations, journal.id);
    if (!exists(snapshot)) { fs.mkdirSync(snapshot, { mode: 0o700 }); installer.syncDirectory(layout.migrations); }
    const staged = path.join(snapshot, 'service.env');
    if (!exists(staged)) installer.atomicWrite(staged, canonical, 0o600, layout.identity.uid);
    installer.writeJson(path.join(snapshot, 'receipt.json'), {
      schema: 'voice-agent.legacy-config-migration.v1', source_sha256: journal.config_source_sha256,
      canonical_sha256: journal.config_canonical_sha256, source_mode: '0600', canonical_mode: '0600',
    }, layout.identity.uid);
    if (digest(core.readOwnedRegular(staged, layout.identity.uid, [0o600], 4 * 1024 * 1024)) !== journal.config_canonical_sha256
        || digest(core.readOwnedRegular(sourcePath, layout.identity.uid, [0o600], 4 * 1024 * 1024)) !== journal.config_source_sha256) {
      error('legacy_config_cas_mismatch', 'staged or source private configuration changed before commit');
    }
    const target = path.join(layout.private, 'service.env');
    if (exists(target)) {
      const current = core.readOwnedRegular(target, layout.identity.uid, [0o600], 4 * 1024 * 1024);
      if (!current.equals(canonical)) error('canonical_config_conflict', 'an unexpected canonical private configuration was preserved');
    } else installer.atomicWrite(target, canonical, 0o600, layout.identity.uid);
    const config = path.join(layout.config, 'config.yaml');
    const expectedConfig = Buffer.from(installer.renderAgentConfig(layout, journal.id, endpoint || null, false));
    if (exists(config)) {
      if (!core.readOwnedRegular(config, layout.identity.uid, [0o600], 4 * 1024 * 1024).equals(expectedConfig)) error('canonical_config_conflict', 'an unexpected canonical v2 configuration was preserved');
    } else installer.atomicWrite(config, expectedConfig, 0o600, layout.identity.uid);
  }

  function recordDocker(layout, evidence, dependencies) {
    installer.writeJson(path.join(layout.private, 'docker-endpoint.json'), evidence.docker.record, layout.identity.uid);
    agentEnvironment.writePreservation(layout, {
      schema: agentEnvironment.PRESERVATION_SCHEMA, state: 'disabled', action: 'configure_agent', identity_digest: null,
      container_id_prefix: null, runtime_state: null, endpoint_identity: evidence.docker.record.socket_identity || null,
      reason_code: 'not_configured', checked_at: timestamp(dependencies.clock),
    }, installer.writeJson);
  }

  function selectRelease(channel) {
    const compatible = channel.releases.filter((release) => release.platform === core.SUPPORTED_PLATFORM && release.minimum_launcher_protocol <= core.LAUNCHER_PROTOCOL);
    if (!compatible.length) error('platform_release_unavailable', 'the signed channel has no compatible legacy-adoption candidate');
    return compatible.sort((left, right) => right.version.localeCompare(left.version, undefined, { numeric: true }))[0];
  }

  async function acquireCandidate(dependencies, layout, trustedSequence = 0, testMode = false) {
    const base = installer.hostPreflight(await dependencies.host.inspectBase());
    if (base.user.uid !== layout.identity.uid || base.user.name !== layout.identity.username || base.user.home !== layout.identity.home) error('host_user_invalid', 'host and invoking legacy service identities differ');
    let signed = await dependencies.source.acquireChannel();
    const authorityKey = core.releaseAuthorityKey(dependencies.source, signed, testMode);
    const channel = core.verifySignedChannel(signed.channelBytes, signed.signatureBytes, authorityKey, { now: dependencies.clock.now(), trustedSequence });
    const authoritySha256 = digest(Buffer.from(authorityKey));
    signed = { ...signed, publicKeyPem: authorityKey };
    const release = selectRelease(channel);
    const acquired = await dependencies.source.acquireArtifact(release);
    if (acquired.redirected === true || (acquired.url && acquired.url !== release.artifact_url)) error('artifact_redirect_refused', 'program artifact redirect or changed authority was refused');
    const manifest = core.verifyPlatformArtifact(acquired.artifactBytes, acquired.manifestBytes, release);
    const requirements = installer.artifactPreflight(manifest, release, acquired.archiveEntries, acquired.manifestBytes);
    const facts = await dependencies.host.inspectCompatibility({ release, manifest, requirements, layout });
    const payload = manifest.entries.reduce((total, entry) => total + (entry.type === 'file' ? entry.size : 0), 0);
    const requiredBytes = installer.FREE_SPACE_RESERVE + release.artifact_bytes + payload + release.assets.filter((item) => item.reachability === 'required' && item.kind !== 'agent_environment_image').reduce((total, item) => total + item.size, 0);
    if (!Number.isSafeInteger(facts.free_bytes) || facts.free_bytes < requiredBytes) error('insufficient_space', 'legacy adoption lacks space for the verified candidate and retained prior release');
    return { channel, release, acquired, manifest, requirements, requiredBytes, signed, authoritySha256 };
  }

  async function stageCandidate(layout, candidate, dependencies) {
    const id = releaseId(candidate.release); const root = path.join(layout.releases, id);
    if (exists(root)) return updater.readRelease(layout, id);
    const stage = path.join(layout.transactions, `stage-${candidate.release.artifact_sha256.slice(0, 16)}`);
    if (exists(stage)) error('release_stage_ambiguous', 'candidate stage is ambiguous');
    fs.mkdirSync(stage, { mode: 0o700 });
    await installer.extractVerifiedArchive(stage, candidate.manifest, candidate.acquired.manifestBytes, candidate.acquired);
    installer.verifyExtractedTree(stage, candidate.manifest, candidate.acquired.manifestBytes, layout.identity.uid);
    installer.atomicWrite(path.join(stage, 'release-record.json'), Buffer.from(core.canonicalJson({
      schema: 'voice-agent.release-record.v1', release_id: id, version: candidate.release.version, build_id: candidate.release.build_id,
      platform: candidate.release.platform, channel: 'stable', channel_sequence: candidate.channel.sequence,
      artifact_sha256: candidate.release.artifact_sha256, artifact_bytes: candidate.release.artifact_bytes,
      manifest_sha256: candidate.release.manifest_sha256, launcher_protocol: core.LAUNCHER_PROTOCOL,
      application_protocol: candidate.manifest.application_protocol, data_schema: candidate.manifest.data_schema,
      service_template_sha256: candidate.manifest.service_template_sha256,
      asset_digests: candidate.release.assets.map((item) => item.sha256).sort(), launcher_sha256: candidate.release.launcher ? candidate.release.launcher.sha256 : null,
      verified_at: timestamp(dependencies.clock), readiness: { state: 'not_verified', checked_at: null },
    })), 0o400, layout.identity.uid);
    fs.renameSync(stage, root); fs.chmodSync(root, 0o500); installer.syncDirectory(layout.releases);
    return updater.readRelease(layout, id);
  }

  function atomicPointer(layout, filename, id, transactionId) {
    const target = path.join(layout.releases, id); installer.inspectManagedPath(target, layout.identity.uid, 0o500);
    const temporary = path.join(layout.data, `.${path.basename(filename)}.${transactionId}`);
    if (exists(temporary)) fs.unlinkSync(temporary);
    fs.symlinkSync(`releases/${id}`, temporary); fs.renameSync(temporary, filename); installer.syncDirectory(layout.data);
  }

  function releaseLike(candidate) { return { version: candidate.record.version, artifact_sha256: candidate.record.artifact_sha256, build_id: candidate.record.build_id }; }
  async function candidateReady(layout, candidate, dependencies, wait = true) {
    let elapsed = 0;
    do {
      const value = await dependencies.service.probe({ release_id: candidate.id, build_id: candidate.record.build_id, deadline_ms: wait ? installer.STARTUP_DEADLINE_MS : 0 });
      if (installer.validateReadiness(value, releaseLike(candidate), layout.identity.uid)) return true;
      if (!wait || elapsed >= installer.STARTUP_DEADLINE_MS) return false;
      const delay = Math.min(1000, installer.STARTUP_DEADLINE_MS - elapsed); await dependencies.clock.sleep(delay); elapsed += delay;
    } while (elapsed <= installer.STARTUP_DEADLINE_MS);
    return false;
  }

  function markCandidateReady(layout, candidate, dependencies) {
    const filename = path.join(candidate.root, 'release-record.json');
    const record = core.validateReleaseRecord(JSON.parse(core.readOwnedRegular(filename, layout.identity.uid, [0o400], 256 * 1024).toString('utf8')));
    if (record.readiness.state !== 'ready') {
      record.readiness = { state: 'ready', checked_at: timestamp(dependencies.clock) };
      fs.chmodSync(candidate.root, 0o700); installer.atomicWrite(filename, Buffer.from(core.canonicalJson(record)), 0o400, layout.identity.uid); fs.chmodSync(candidate.root, 0o500); installer.syncDirectory(layout.releases);
    }
  }

  async function provePrior(layout, evidence, dependencies) {
    const snapshot = await dependencies.legacyProbe.inspectLegacy({ serviceName: SERVICE_NAME, uid: layout.identity.uid });
    core.validateLegacyRunning(evidence.running, snapshot, {
      expectedUid: layout.identity.uid, serviceUnitPath: evidence.serviceUnitPath,
      serviceUnitOwner: evidence.serviceUnitOwner, expectedPythonPath: evidence.expectedPythonPath,
    });
  }

  async function rollback(layout, journal, evidence, dependencies, code) {
    let next = writeJournal(layout, journal, 'failed_needs_repair', dependencies, { failure_code: code });
    try {
      try { await dependencies.service.stop({ unit: installer.SERVICE_UNIT }); } catch {}
      try { if (typeof dependencies.service.disable === 'function') await dependencies.service.disable({ unit: installer.SERVICE_UNIT }); } catch {}
      if (exists(layout.current)) { fs.unlinkSync(layout.current); installer.syncDirectory(layout.data); }
      if (exists(layout.installRecord)) { fs.unlinkSync(layout.installRecord); installer.syncDirectory(layout.data); }
      await dependencies.legacy.restore({ serviceName: SERVICE_NAME, release_id: evidence.running.release_id, unit_sha256: digest(core.readOwnedRegular(evidence.serviceUnitPath, evidence.serviceUnitOwner, null, 256 * 1024)) });
      fault(dependencies, 'action', 'prior_restored');
      await provePrior(layout, evidence, dependencies);
      const after = await agentEnvironment.capture(layout, dependencies, { phase: 'adoption_rollback' });
      if (next.agent_environment.before) agentEnvironment.assertPreserved(next.agent_environment.before, after);
      agentEnvironment.writePreservation(layout, after, installer.writeJson);
      next = writeJournal(layout, next, 'failed_safe', dependencies, { agent_environment: { ...next.agent_environment, after }, receipts: { environment_after: true, prior_restored: true, terminal: true } });
      error('update_failed_safe', `legacy adoption candidate failed (${code}); the exact prior system service was restored and proved ready`);
    } catch (reason) {
      if (reason instanceof core.LauncherError && reason.code === 'update_failed_safe') throw reason;
      try { writeJournal(layout, next, 'failed_needs_repair', dependencies, { failure_code: code, receipts: { terminal: true } }); } catch {}
      error('update_failed_needs_repair', `legacy adoption candidate failed (${code}) and exact prior readiness could not be restored`);
    }
  }

  async function installVoiceAgent(options = {}) {
    const testMode = options.testMode === true;
    if (!testMode && options.dependencies) error('test_injection_forbidden', 'legacy adoption injection is test-only');
    const dependencies = options.dependencies || defaultDependencies();
    const layout = adoptionLayout(installer.layoutFor(testMode ? dependencies.identity : undefined, testMode));
    if (exists(layout.installRecord) && !exists(layout.adoptionJournal)) return installer.installVoiceAgent(options);
    const legacyRoot = options.legacyRoot || path.join(layout.identity.home, '.local', 'share', 'voice-agent-v2');
    if (!exists(legacyRoot) && !exists(layout.adoptionJournal)) return installer.installVoiceAgent(options);
    const serviceUnitPath = options.serviceUnitPath || path.join('/etc', 'systemd', 'system', SERVICE_NAME);
    const expectedPythonPath = options.expectedPythonPath || path.join(layout.identity.home, '.cache', 'voice-agent-v2', 'slice-6', 'runtime', 'venv', 'bin', 'python');
    const serviceUnitOwner = options.serviceUnitOwner ?? 0;
    const lock = updater.acquireExclusiveLock(layout, dependencies);
    let journal = null;
    let evidence;
    try {
      if (exists(layout.adoptionJournal)) {
        journal = readJournal(layout);
        const runningLegacyId = journal.prior_running.slice('legacy-'.length);
        const selectedLegacyId = journal.legacy_candidate.slice('legacy-'.length);
        const running = core.validateLegacyRelease(path.join(legacyRoot, 'releases', runningLegacyId), legacyRoot, layout.identity.uid);
        const selected = core.validateLegacyRelease(path.join(legacyRoot, 'releases', selectedLegacyId), legacyRoot, layout.identity.uid);
        const snapshot = await dependencies.legacyProbe.inspectLegacy({ serviceName: SERVICE_NAME, uid: layout.identity.uid });
        let docker;
        const dockerRecord = path.join(layout.private, 'docker-endpoint.json');
        if (exists(dockerRecord)) {
          const recorded = agentEnvironment.validateEndpointRecord(JSON.parse(core.readOwnedRegular(dockerRecord, layout.identity.uid, [0o600], 256 * 1024).toString('utf8')), layout.identity.uid);
          docker = { state: recorded.endpoint ? 'available' : 'unavailable', record: recorded };
        } else if (!snapshot.docker) docker = { state: 'unavailable', record: agentEnvironment.endpointRecord(layout.identity.uid) };
        else {
          try { docker = { state: 'available', record: agentEnvironment.endpointRecord(layout.identity.uid, { ...snapshot.docker, service_endpoint: agentEnvironment.endpointFor(layout.identity.uid) }) }; }
          catch { error('legacy_docker_endpoint_invalid', 'a rootful, foreign, group-authorized, ambient, or unverifiable Docker endpoint was refused'); }
        }
        evidence = { running, selected, snapshot, docker, legacyRoot };
      } else {
        evidence = await inspectLegacy({ legacyRoot, serviceUnitPath, serviceUnitOwner, expectedPythonPath }, dependencies, layout);
      }
      evidence.serviceUnitPath = serviceUnitPath; evidence.serviceUnitOwner = serviceUnitOwner; evidence.expectedPythonPath = expectedPythonPath;

      let candidate = null;
      if (journal && journal.receipts.candidate_staged) candidate = updater.readRelease(layout, journal.candidate);
      let acquired = null;
      if (!candidate) acquired = await acquireCandidate(dependencies, layout, 0, testMode);
      const source = core.readOwnedRegular(evidence.running.document.configuration_path, layout.identity.uid, [0o600], 4 * 1024 * 1024);
      const endpoint = evidence.docker.state === 'available' ? agentEnvironment.endpointFor(layout.identity.uid) : '';
      const canonical = canonicalServiceEnv(source, endpoint);

      if (!journal) {
        installer.installDirectories(layout);
        journal = {
          schema: ADOPTION_SCHEMA, id: dependencies.randomBytes(16).toString('hex'), phase: 'discovered',
          prior_running: importedId(evidence.running.release_id), prior_healthy: importedId(evidence.running.release_id),
          legacy_candidate: importedId(evidence.selected.release_id), candidate: releaseId(acquired.release),
          config_source_sha256: digest(source), config_canonical_sha256: digest(canonical), agent_environment: { before: null, after: null }, receipts: emptyReceipts(),
          failure_code: null, started_at: timestamp(dependencies.clock), updated_at: timestamp(dependencies.clock),
        };
        installer.writeJson(layout.adoptionJournal, journal, layout.identity.uid); fault(dependencies, 'write', 'discovered');
        agentEnvironment.migrateLegacy(layout, journal.id, installer.writeJson);
      } else installer.installDirectories(layout);

      if (journal.phase === 'failed_needs_repair') {
        try { await provePrior(layout, evidence, dependencies); journal = writeJournal(layout, journal, 'failed_safe', dependencies, { receipts: { prior_restored: true } }); }
        catch { error('update_failed_needs_repair', 'the exact prior system service still requires repair'); }
      }
      if (journal.phase === 'failed_safe') {
        journal = writeJournal(layout, journal, 'prepared', dependencies, { failure_code: null, receipts: { legacy_stopped: false, candidate_activated: false, candidate_started: false, candidate_ready: false, prior_restored: false, terminal: false } });
      }

      if (!journal.receipts.prior_imported) { importLegacyRelease(layout, evidence.running, 'ready', dependencies); journal = writeJournal(layout, journal, journal.phase, dependencies, { receipts: { prior_imported: true } }); }
      if (!journal.receipts.selected_imported) { importLegacyRelease(layout, evidence.selected, 'not_verified', dependencies); journal = writeJournal(layout, journal, journal.phase, dependencies, { receipts: { selected_imported: true } }); }
      if (!journal.receipts.config_committed) { preparePrivateConfig(layout, evidence, journal, dependencies); journal = writeJournal(layout, journal, journal.phase, dependencies, { receipts: { config_committed: true } }); }
      if (!journal.receipts.docker_recorded) { recordDocker(layout, evidence, dependencies); fault(dependencies, 'action', 'docker_recorded'); journal = writeJournal(layout, journal, journal.phase, dependencies, { receipts: { docker_recorded: true } }); }
      if (!candidate) {
        const cache = core.loadAssetCache(installer);
        installer.writeJson(layout.channelReceipt, { schema: 'voice-agent.cached-channel.v1', channel_base64: Buffer.from(acquired.signed.channelBytes).toString('base64'), signature_base64: Buffer.from(acquired.signed.signatureBytes).toString('base64'), public_key_base64: Buffer.from(acquired.signed.publicKeyPem).toString('base64'), authority_sha256: acquired.authoritySha256, sequence: acquired.channel.sequence, expires_at: acquired.channel.expires_at, verified_at: timestamp(dependencies.clock) }, layout.identity.uid);
        await cache.reconcile(layout, acquired.release.assets, journal.id, dependencies.source, { applicationProtocol: acquired.manifest.application_protocol.minimum, imageInspector: dependencies.host.inspectAgentImage ? (descriptor) => dependencies.host.inspectAgentImage(descriptor) : null });
        if (acquired.release.launcher) { let outcome; do { outcome = await cache.acquire(layout, acquired.release.launcher, journal.id, dependencies.source, { applicationProtocol: acquired.manifest.application_protocol.minimum }); } while (outcome.state === 'partial'); }
        const programDescriptor = cache.programDescriptor(acquired.release); let programOutcome;
        do { programOutcome = await cache.acquire(layout, programDescriptor, journal.id, dependencies.source, { applicationProtocol: 1 }); } while (programOutcome.state === 'partial');
        if (digest(core.readOwnedRegular(cache.cachePath(layout, programDescriptor), layout.identity.uid, [0o400], acquired.release.artifact_bytes)) !== acquired.release.artifact_sha256) error('artifact_cache_invalid', 'cached program artifact digest differs');
        candidate = await stageCandidate(layout, acquired, dependencies);
        const modelSets = installer.loadModelSets(candidate.root, acquired.release, layout.identity.uid);
        cache.materializeViews(layout, modelSets, acquired.release.assets, journal.id);
        const closureFacts = await dependencies.host.inspectCompatibility({ release: acquired.release, manifest: acquired.manifest, requirements: acquired.requirements, layout, candidateRoot: candidate.root, modelSets });
        installer.compatibilityPreflight(closureFacts, acquired.requirements, acquired.requiredBytes);
        fault(dependencies, 'action', 'candidate_staged'); journal = writeJournal(layout, journal, journal.phase, dependencies, { receipts: { candidate_staged: true } });
      }
      const candidateModelSets = installer.loadModelSets(candidate.root, acquired ? acquired.release : candidate.record, layout.identity.uid);
      core.loadAssetCache(installer).materializeViews(layout, candidateModelSets, installer.modelDescriptorsForRelease(candidateModelSets, acquired ? acquired.release : candidate.record), journal.id);
      if (!journal.receipts.user_service_prepared) {
        const unitBytes = installer.renderUnit(layout, candidate.root);
        const unitExact = exists(layout.unit) && core.readOwnedRegular(layout.unit, layout.identity.uid, [0o600], 256 * 1024).equals(unitBytes);
        const enabled = unitExact && typeof dependencies.service.isEnabled === 'function' && await dependencies.service.isEnabled({ unit: installer.SERVICE_UNIT });
        if (!enabled) {
          await dependencies.service.enableLinger({ username: layout.identity.username, uid: layout.identity.uid, command: ['sudo', '-n', 'loginctl', 'enable-linger', layout.identity.username] });
          if (!unitExact) await dependencies.service.installUnit({ path: layout.unit, bytes: unitBytes, mode: 0o600, uid: layout.identity.uid });
          if (typeof dependencies.service.enable !== 'function') error('user_systemd_unavailable', 'the user service cannot be enabled without starting it');
          await dependencies.service.enable({ unit: installer.SERVICE_UNIT });
          fault(dependencies, 'action', 'user_service_prepared');
        }
        journal = writeJournal(layout, journal, 'prepared', dependencies, { receipts: { user_service_prepared: true } });
      }
      if (!exists(layout.rollback)) atomicPointer(layout, layout.rollback, journal.prior_healthy, journal.id);
      if (!journal.receipts.environment_before) {
        const before = await agentEnvironment.capture(layout, dependencies, { phase: 'adoption_before' });
        agentEnvironment.writePreservation(layout, before, installer.writeJson);
        journal = writeJournal(layout, journal, journal.phase, dependencies, { agent_environment: { before, after: null }, receipts: { environment_before: true } });
      }

      if (!journal.receipts.legacy_stopped) {
        let priorReady = false;
        try { await provePrior(layout, evidence, dependencies); priorReady = true; } catch {}
        if (priorReady) { await dependencies.legacy.quiesce({ serviceName: SERVICE_NAME, release_id: evidence.running.release_id }); fault(dependencies, 'action', 'legacy_stopped'); }
        const stopped = await dependencies.legacyProbe.inspectLegacy({ serviceName: SERVICE_NAME, uid: layout.identity.uid });
        if (stopped && stopped.service && stopped.service.active === 'active'
            || stopped && stopped.service && Number(stopped.service.main_pid) > 0
            || stopped && stopped.listener && stopped.listener.host === '127.0.0.1') error('legacy_service_quiesce_unproven', 'the legacy process/listener generation did not quiesce');
        journal = writeJournal(layout, journal, 'legacy_stopped', dependencies, { receipts: { legacy_stopped: true } });
      }
      if (!journal.receipts.candidate_activated) {
        atomicPointer(layout, layout.current, candidate.id, journal.id);
        installer.writeRuntimeConfig(layout, candidate.root, candidateModelSets);
        fault(dependencies, 'action', 'candidate_activated');
        journal = writeJournal(layout, journal, 'legacy_stopped', dependencies, { receipts: { candidate_activated: true } });
      }
      if (!journal.receipts.candidate_started) {
        if (!await candidateReady(layout, candidate, dependencies, false)) { await dependencies.service.startExactlyOnce({ unit: installer.SERVICE_UNIT, release_id: candidate.id, start_once: true }); fault(dependencies, 'action', 'candidate_started'); }
        journal = writeJournal(layout, journal, 'candidate_started', dependencies, { receipts: { candidate_started: true } });
      }
      if (!journal.receipts.candidate_ready) {
        if (!await candidateReady(layout, candidate, dependencies, true)) return rollback(layout, journal, evidence, dependencies, 'candidate_not_ready');
        fault(dependencies, 'action', 'candidate_ready'); journal = writeJournal(layout, journal, 'candidate_ready', dependencies, { receipts: { candidate_ready: true } });
      }
      if (!journal.receipts.environment_after) {
        const after = await agentEnvironment.capture(layout, dependencies, { phase: 'adoption_after' });
        agentEnvironment.assertPreserved(journal.agent_environment.before, after);
        agentEnvironment.writePreservation(layout, after, installer.writeJson);
        journal = writeJournal(layout, journal, journal.phase, dependencies, { agent_environment: { ...journal.agent_environment, after }, receipts: { environment_after: true } });
      }
      markCandidateReady(layout, candidate, dependencies);
      let channelAuthority = acquired && acquired.authoritySha256;
      if (!channelAuthority) {
        const cachedReceipt = installer.readPrivateJson(layout.channelReceipt, layout.identity.uid);
        const authorityKey = core.releaseAuthorityKey(dependencies.source, { publicKeyPem: Buffer.from(cachedReceipt.public_key_base64, 'base64') }, testMode);
        channelAuthority = digest(Buffer.from(authorityKey));
        if (cachedReceipt.authority_sha256 !== channelAuthority) error('channel_authority_invalid', 'cached adoption authority receipt differs from the compiled root');
      }
      if (!/^[0-9a-f]{64}$/.test(channelAuthority)) error('channel_authority_invalid', 'cached adoption channel authority is invalid');
      installer.writeJson(layout.installRecord, {
        schema: 'voice-agent.installation.v1', installation_id: journal.id, channel: 'stable', channel_sequence: candidate.record.channel_sequence,
        launcher_protocol: core.LAUNCHER_PROTOCOL, channel_authority_sha256: channelAuthority, release_id: candidate.id, healthy_release: candidate.id, rollback_release: journal.prior_healthy,
        version: candidate.record.version, build_id: candidate.record.build_id, artifact_sha256: candidate.record.artifact_sha256, installed_at: timestamp(dependencies.clock),
      }, layout.identity.uid);
      journal = writeJournal(layout, journal, 'committed', dependencies);
      if (!journal.receipts.legacy_retired) {
        const alreadyRetired = typeof dependencies.legacy.retired === 'function' && await dependencies.legacy.retired({ serviceName: SERVICE_NAME });
        if (!alreadyRetired) { await dependencies.legacy.retire({ serviceName: SERVICE_NAME }); fault(dependencies, 'action', 'legacy_retired'); }
        journal = writeJournal(layout, journal, 'committed', dependencies, { receipts: { legacy_retired: true } });
      }
      journal = writeJournal(layout, journal, 'committed', dependencies, { receipts: { terminal: true } });
      const snapshotRoot = path.join(layout.migrations, journal.id);
      if (exists(snapshotRoot)) { fs.rmSync(snapshotRoot, { recursive: true }); installer.syncDirectory(layout.migrations); }
      fs.unlinkSync(layout.adoptionJournal); installer.syncDirectory(layout.transactions);
      let launcherDescriptor = acquired && acquired.release.launcher;
      if (!acquired && exists(layout.channelReceipt)) {
        const receipt = installer.readPrivateJson(layout.channelReceipt, layout.identity.uid);
        try {
          const cachedSigned = { publicKeyPem: Buffer.from(receipt.public_key_base64, 'base64') };
          const authorityKey = core.releaseAuthorityKey(dependencies.source, cachedSigned, testMode);
          const channel = core.verifySignedChannel(Buffer.from(receipt.channel_base64, 'base64'), Buffer.from(receipt.signature_base64, 'base64'), authorityKey, { now: dependencies.clock.now(), trustedSequence: candidate.record.channel_sequence });
          const exactRelease = channel.releases.find((item) => item.artifact_sha256 === candidate.record.artifact_sha256);
          launcherDescriptor = exactRelease && exactRelease.launcher;
        } catch { error('channel_receipt_invalid', 'cached adoption channel receipt is invalid'); }
      }
      if (launcherDescriptor) {
        const replacement = core.loadSelfUpdater().replace(layout, launcherDescriptor, journal.id, candidate.id, dependencies.selfUpdate || {});
        if (replacement.state === 'old_launcher_restored') error('launcher_update_failed_safe', 'adopted application is durably healthy but launcher replacement failed and exact old launcher was restored');
      }
      const output = dependencies.output || { info() {} };
      output.info(`Voice Agent ${candidate.record.version} adopted the exact legacy runtime as rollback custody and is five-component ready.`);
      output.info(`Agent tools: disabled; Docker endpoint: ${evidence.docker.state === 'available' ? 'verified rootless' : 'unavailable'}; Telegram: disabled.`);
      return { state: 'legacy_adopted_healthy', release_id: candidate.id, rollback_release: journal.prior_healthy, legacy_candidate: journal.legacy_candidate };
    } catch (reason) {
      if (reason && ['legacy_adoption_interrupted', 'update_failed_safe', 'update_failed_needs_repair'].includes(reason.code)) throw reason;
      if (journal && exists(layout.adoptionJournal) && journal.receipts.legacy_stopped) return rollback(layout, readJournal(layout), evidence, dependencies, reason.code || 'legacy_adoption_failed');
      throw reason;
    } finally { lock.release(); }
  }

  class SystemLegacyService {
    async quiesce() {
      const result = spawnSync('sudo', ['-n', 'systemctl', 'stop', SERVICE_NAME], { encoding: 'utf8', timeout: 85000, env: { PATH: '/usr/bin:/bin', LC_ALL: 'C.UTF-8' } });
      if (result.status !== 0) error('legacy_service_stop_failed', 'the exact legacy system service did not quiesce');
    }
    async restore() {
      const result = spawnSync('sudo', ['-n', 'systemctl', 'start', SERVICE_NAME], { encoding: 'utf8', timeout: 315000, env: { PATH: '/usr/bin:/bin', LC_ALL: 'C.UTF-8' } });
      if (result.status !== 0) error('legacy_service_restore_failed', 'the exact legacy system service could not be restored');
    }
    async retired() {
      const result = spawnSync('systemctl', ['is-enabled', SERVICE_NAME], { encoding: 'utf8', timeout: 5000, env: { PATH: '/usr/bin:/bin', LC_ALL: 'C.UTF-8' } });
      return result.status !== 0;
    }
    async retire() {
      const result = spawnSync('sudo', ['-n', 'systemctl', 'disable', SERVICE_NAME], { encoding: 'utf8', timeout: 15000, env: { PATH: '/usr/bin:/bin', LC_ALL: 'C.UTF-8' } });
      if (result.status !== 0) error('legacy_service_retire_failed', 'the superseded legacy system service could not be disabled');
    }
  }

  function defaultDependencies() {
    const dependencies = installer.defaultDependencies();
    dependencies.legacyProbe = new core.SystemServiceProbe();
    dependencies.legacy = new SystemLegacyService();
    return dependencies;
  }

  return { ADOPTION_SCHEMA, PHASES, RECEIPTS, canonicalServiceEnv, defaultDependencies, installVoiceAgent, normalizeJournal, validateImportedRelease, validateJournal };
};
