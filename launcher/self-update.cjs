'use strict';

module.exports = function createSelfUpdater(core, installer) {
  const crypto = require('node:crypto');
  const fs = require('node:fs');
  const path = require('node:path');
  const { spawnSync } = require('node:child_process');
  const assets = core.loadAssetCache(installer);
  const PHASES = new Set(['staged', 'old_renamed', 'live_renamed', 'exec_requested', 'validated']);
  const KEYS = ['application_release', 'id', 'live_path_hash', 'new_sha256', 'old_sha256', 'phase', 'schema'];

  function error(code, message) { throw new core.LauncherError(code, message); }
  function digest(bytes) { return crypto.createHash('sha256').update(bytes).digest('hex'); }
  function exists(filename) { try { fs.lstatSync(filename); return true; } catch (reason) { if (reason.code === 'ENOENT') return false; throw reason; } }
  function fault(dependencies, name) { if (dependencies.fault && typeof dependencies.fault.afterSelfUpdatePhase === 'function') dependencies.fault.afterSelfUpdatePhase(name); }
  function hashFile(filename, uid, modes = [0o755, 0o700]) { return digest(core.readOwnedRegular(filename, uid, modes, 1024 * 1024 * 1024)); }

  function validateReceipt(value, layout) {
    if (!value || typeof value !== 'object' || Array.isArray(value) || Object.keys(value).sort().join('\0') !== [...KEYS].sort().join('\0')
      || value.schema !== 'voice-agent.launcher-self-update.v1' || !/^[0-9a-f]{32}$/.test(value.id)
      || !/^[0-9A-Za-z][0-9A-Za-z.+-]{0,95}$/.test(value.application_release) || !PHASES.has(value.phase)
      || !/^[0-9a-f]{64}$/.test(value.old_sha256) || !/^[0-9a-f]{64}$/.test(value.new_sha256)
      || value.live_path_hash !== digest(Buffer.from(layout.launcher))) error('launcher_update_receipt_invalid', 'launcher replacement receipt is invalid');
    return value;
  }

  function readReceipt(layout) { return validateReceipt(installer.readPrivateJson(layout.selfUpdateJournal, layout.identity.uid), layout); }
  function writeReceipt(layout, value, phase, dependencies) {
    const next = validateReceipt({ ...value, phase }, layout);
    installer.writeJson(layout.selfUpdateJournal, next, layout.identity.uid); fault(dependencies, `receipt_${phase}`); return next;
  }
  function inspectExecutable(filename, layout, expectedHash) {
    const metadata = installer.inspectManagedPath(filename, layout.identity.uid, null, 'file');
    if (!metadata || metadata.nlink !== 1 || ![0o700, 0o755].includes(metadata.mode & 0o777) || hashFile(filename, layout.identity.uid) !== expectedHash) error('launcher_update_hash_mismatch', 'launcher replacement bytes or custody differ');
  }
  function safeUnlink(filename, layout, expectedHash) {
    if (!exists(filename)) return;
    inspectExecutable(filename, layout, expectedHash); fs.unlinkSync(filename); installer.syncDirectory(path.dirname(filename));
  }

  function applicationHealthy(layout, receipt) {
    if (exists(layout.updateJournal)) error('launcher_update_application_unhealthy', 'application transaction is not durably terminal');
    const install = installer.readPrivateJson(layout.installRecord, layout.identity.uid);
    if (install.healthy_release !== receipt.application_release || install.release_id !== receipt.application_release) error('launcher_update_application_unhealthy', 'healthy application identity differs from launcher receipt');
    const pointer = fs.lstatSync(layout.current);
    if (!pointer.isSymbolicLink() || fs.readlinkSync(layout.current) !== `releases/${receipt.application_release}`) error('launcher_update_application_unhealthy', 'selected application identity differs from launcher receipt');
    return true;
  }

  function restoreOld(layout, receipt, dependencies) {
    const live = layout.launcher; const old = `${live}.old`; const fresh = `${live}.new`;
    if (!exists(old)) error('launcher_update_backup_missing', 'exact old launcher backup is unavailable');
    inspectExecutable(old, layout, receipt.old_sha256);
    if (exists(live)) {
      inspectExecutable(live, layout, receipt.new_sha256);
      safeUnlink(fresh, layout, receipt.new_sha256);
      fs.renameSync(live, fresh); installer.syncDirectory(path.dirname(live)); fault(dependencies, 'failed_live_moved');
    }
    fs.renameSync(old, live); installer.syncDirectory(path.dirname(live)); fault(dependencies, 'old_restored');
    safeUnlink(fresh, layout, receipt.new_sha256);
    if (exists(layout.selfUpdateJournal)) { fs.unlinkSync(layout.selfUpdateJournal); installer.syncDirectory(layout.transactions); }
    return { state: 'old_launcher_restored', launcher_sha256: receipt.old_sha256 };
  }

  function postSelfUpdate(layout, transactionId, dependencies = {}) {
    if (!exists(layout.selfUpdateJournal)) error('launcher_update_receipt_missing', 'launcher replacement receipt is unavailable');
    let receipt = readReceipt(layout);
    if (receipt.id !== transactionId) error('launcher_update_receipt_invalid', 'launcher transaction identity differs');
    applicationHealthy(layout, receipt);
    inspectExecutable(layout.launcher, layout, receipt.new_sha256);
    inspectExecutable(`${layout.launcher}.old`, layout, receipt.old_sha256);
    receipt = writeReceipt(layout, receipt, 'validated', dependencies); fault(dependencies, 'validation_complete');
    safeUnlink(`${layout.launcher}.old`, layout, receipt.old_sha256); fault(dependencies, 'backup_removed');
    fs.unlinkSync(layout.selfUpdateJournal); installer.syncDirectory(layout.transactions); fault(dependencies, 'receipt_removed');
    return { state: 'launcher_updated', launcher_sha256: receipt.new_sha256, application_release: receipt.application_release };
  }

  function defaultProcess(layout, transactionId) {
    const result = spawnSync(layout.launcher, ['__post-self-update', transactionId], { encoding: 'utf8', timeout: 30000, env: { PATH: '/usr/bin:/bin', HOME: layout.identity.home } });
    if (result.status !== 0) error('launcher_update_exec_failed', 'new launcher post-update validation failed');
    return { completed: true };
  }

  function replace(layout, descriptor, transactionId, applicationRelease, dependencies = {}) {
    assets.validateDescriptor(descriptor);
    if (descriptor.kind !== 'launcher' || descriptor.reachability !== 'required') error('launcher_descriptor_invalid', 'launcher replacement descriptor is invalid');
    if (exists(layout.selfUpdateJournal)) return recover(layout, dependencies);
    applicationHealthy(layout, { application_release: applicationRelease });
    const directory = path.dirname(layout.launcher);
    installer.inspectManagedPath(directory, layout.identity.uid, 0o700);
    inspectExecutable(layout.launcher, layout, hashFile(layout.launcher, layout.identity.uid));
    const oldHash = hashFile(layout.launcher, layout.identity.uid);
    const cached = assets.cachePath(layout, descriptor); assets.verifyCached(layout, descriptor);
    const newPath = `${layout.launcher}.new`; const oldPath = `${layout.launcher}.old`;
    if (exists(newPath) || exists(oldPath)) error('launcher_update_backup_ambiguous', 'launcher .new/.old state is ambiguous without an exact receipt');
    let receipt = writeReceipt(layout, {
      schema: 'voice-agent.launcher-self-update.v1', id: transactionId, application_release: applicationRelease,
      live_path_hash: digest(Buffer.from(layout.launcher)), old_sha256: oldHash, new_sha256: descriptor.sha256, phase: 'staged',
    }, 'staged', dependencies);
    try {
      installer.atomicWrite(newPath, core.readOwnedRegular(cached, layout.identity.uid, [0o400], descriptor.size), 0o755, layout.identity.uid); fault(dependencies, 'new_fsynced');
      fs.renameSync(layout.launcher, oldPath); fault(dependencies, 'old_renamed');
      installer.syncDirectory(directory); fault(dependencies, 'old_rename_directory_fsynced');
      receipt = writeReceipt(layout, receipt, 'old_renamed', dependencies);
      fs.renameSync(newPath, layout.launcher); fault(dependencies, 'new_renamed');
      installer.syncDirectory(directory); fault(dependencies, 'new_rename_directory_fsynced');
      receipt = writeReceipt(layout, receipt, 'live_renamed', dependencies);
      receipt = writeReceipt(layout, receipt, 'exec_requested', dependencies); fault(dependencies, 'exec_requested');
      const processOwner = dependencies.process || { execPost: () => defaultProcess(layout, transactionId) };
      const outcome = processOwner.execPost({ path: layout.launcher, args: ['__post-self-update', transactionId] });
      fault(dependencies, 'exec_returned');
      if (outcome && outcome.completed === true) {
        if (exists(layout.selfUpdateJournal)) return postSelfUpdate(layout, transactionId, dependencies);
        return { state: 'launcher_updated', launcher_sha256: descriptor.sha256, application_release: applicationRelease };
      }
      return { state: 'launcher_exec_requested', launcher_sha256: descriptor.sha256, application_release: applicationRelease };
    } catch (reason) {
      if (reason && reason.code === 'launcher_update_interrupted') throw reason;
      try { return restoreOld(layout, receipt, dependencies); } catch { error('launcher_update_recovery_required', 'old launcher could not be restored; healthy application was left undisturbed'); }
    }
  }

  function recover(layout, dependencies = {}) {
    if (!exists(layout.selfUpdateJournal)) {
      if (exists(`${layout.launcher}.new`) || exists(`${layout.launcher}.old`)) error('launcher_update_backup_ambiguous', 'foreign or unreceipted launcher backup is ambiguous');
      return { state: 'no_launcher_recovery' };
    }
    const receipt = readReceipt(layout); const live = layout.launcher; const fresh = `${live}.new`; const old = `${live}.old`;
    const classify = (filename) => {
      if (!exists(filename)) return 'absent';
      const metadata = fs.lstatSync(filename);
      if (metadata.isSymbolicLink() || !metadata.isFile() || metadata.uid !== layout.identity.uid || metadata.nlink !== 1) error('launcher_update_backup_ambiguous', 'launcher recovery path is foreign or linked');
      const value = hashFile(filename, layout.identity.uid);
      if (value === receipt.old_sha256) return 'old'; if (value === receipt.new_sha256) return 'new'; return 'foreign';
    };
    const state = { live: classify(live), fresh: classify(fresh), old: classify(old) };
    if (Object.values(state).includes('foreign')) error('launcher_update_backup_ambiguous', 'launcher recovery hash is not authorized by its exact receipt');
    try {
      applicationHealthy(layout, receipt);
      if (state.live === 'new' && state.old === 'old' && state.fresh === 'absent') return postSelfUpdate(layout, receipt.id, dependencies);
      if (state.live === 'new' && state.old === 'absent' && state.fresh === 'absent' && receipt.phase === 'validated') { fs.unlinkSync(layout.selfUpdateJournal); installer.syncDirectory(layout.transactions); return { state: 'launcher_updated', launcher_sha256: receipt.new_sha256, application_release: receipt.application_release }; }
      if (state.live === 'old' && state.old === 'absent') { safeUnlink(fresh, layout, receipt.new_sha256); fs.unlinkSync(layout.selfUpdateJournal); installer.syncDirectory(layout.transactions); return { state: 'old_launcher_restored' }; }
      if (state.live === 'absent' && state.old === 'old') { fs.renameSync(old, live); installer.syncDirectory(path.dirname(live)); safeUnlink(fresh, layout, receipt.new_sha256); fs.unlinkSync(layout.selfUpdateJournal); installer.syncDirectory(layout.transactions); return { state: 'old_launcher_restored' }; }
      error('launcher_update_backup_ambiguous', 'launcher recovery state has multiple possible owners');
    } catch (reason) {
      if (reason instanceof core.LauncherError && reason.code === 'launcher_update_application_unhealthy' && state.old === 'old') return restoreOld(layout, receipt, dependencies);
      throw reason;
    }
  }

  return { PHASES, postSelfUpdate, recover, replace, validateReceipt };
};
