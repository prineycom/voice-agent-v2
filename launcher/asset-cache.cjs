'use strict';

module.exports = function createAssetCache(core, installer) {
  const crypto = require('node:crypto');
  const fs = require('node:fs');
  const path = require('node:path');

  const ASSET_KINDS = new Set(['program', 'model', 'runtime', 'agent_environment_image', 'launcher']);
  const ASSET_KEYS = ['authority', 'compatibility', 'digest', 'id', 'kind', 'license', 'platform', 'reachability', 'required_free_space_reserve', 'sha256', 'size', 'url'];
  const RANGE_KEYS = ['maximum_application_protocol', 'maximum_launcher_protocol', 'minimum_application_protocol', 'minimum_launcher_protocol'];
  const SAFE_ID = /^[a-z0-9][a-z0-9._-]{0,63}$/;
  const SHA256 = /^[0-9a-f]{64}$/;

  function error(code, message) { throw new core.LauncherError(code, message); }
  function exactKeys(value, keys, code) {
    if (!value || typeof value !== 'object' || Array.isArray(value)
      || Object.keys(value).sort().join('\0') !== [...keys].sort().join('\0')) error(code, 'asset descriptor fields are not closed');
  }
  function digest(bytes) { return crypto.createHash('sha256').update(bytes).digest('hex'); }
  function exists(filename) { try { fs.lstatSync(filename); return true; } catch (reason) { if (reason.code === 'ENOENT') return false; throw reason; } }

  function validateDescriptor(document) {
    exactKeys(document, ASSET_KEYS, 'asset_descriptor_invalid');
    exactKeys(document.authority, ['origin', 'path_prefix'], 'asset_descriptor_invalid');
    exactKeys(document.license, ['acceptance', 'id'], 'asset_descriptor_invalid');
    exactKeys(document.compatibility, RANGE_KEYS, 'asset_descriptor_invalid');
    if (!SAFE_ID.test(document.id) || !ASSET_KINDS.has(document.kind) || document.platform !== core.SUPPORTED_PLATFORM
      || !SHA256.test(document.sha256) || document.digest !== `sha256:${document.sha256}`
      || !Number.isSafeInteger(document.size) || document.size < 1
      || !Number.isSafeInteger(document.required_free_space_reserve) || document.required_free_space_reserve < 0
      || !['required', 'optional'].includes(document.reachability)
      || typeof document.license.id !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9 .+_:/()-]{0,127}$/.test(document.license.id)
      || !['accepted', 'not_required'].includes(document.license.acceptance)) error('asset_descriptor_invalid', 'asset descriptor identity, size, license, or reachability is invalid');
    for (const key of RANGE_KEYS) if (!Number.isSafeInteger(document.compatibility[key]) || document.compatibility[key] < 1) error('asset_descriptor_invalid', 'asset compatibility is invalid');
    if (document.compatibility.maximum_launcher_protocol < document.compatibility.minimum_launcher_protocol
      || document.compatibility.maximum_application_protocol < document.compatibility.minimum_application_protocol) error('asset_descriptor_invalid', 'asset compatibility range is invalid');
    let url; let origin;
    try { url = new URL(document.url); origin = new URL(document.authority.origin); } catch { error('asset_authority_invalid', 'asset URL authority is invalid'); }
    if (url.protocol !== 'https:' || origin.protocol !== 'https:' || origin.pathname !== '/' || origin.search || origin.hash
      || url.origin !== origin.origin || url.username || url.password || url.hash || url.search
      || typeof document.authority.path_prefix !== 'string' || !document.authority.path_prefix.startsWith('/')
      || document.authority.path_prefix.includes('..') || !url.pathname.startsWith(document.authority.path_prefix)
      || document.url !== url.href || document.url.length > 2048) error('asset_authority_invalid', 'asset URL is outside its exact signed allowlist authority');
    if (document.kind === 'agent_environment_image' && !url.pathname.includes(`sha256:${document.sha256}`)) error('asset_descriptor_invalid', 'AgentEnvironment image URL is not digest-pinned');
    return document;
  }

  function validateDescriptors(descriptors) {
    if (!Array.isArray(descriptors) || descriptors.length > 64) error('asset_descriptor_invalid', 'asset descriptor set is invalid');
    const identities = new Set();
    return descriptors.map((value) => {
      const descriptor = validateDescriptor(value);
      const identity = `${descriptor.kind}\0${descriptor.id}`;
      if (identities.has(identity)) error('asset_descriptor_invalid', 'asset descriptor identity is duplicated');
      identities.add(identity);
      return descriptor;
    });
  }

  function compatible(descriptor, applicationProtocol = 1) {
    return descriptor.compatibility.minimum_launcher_protocol <= core.LAUNCHER_PROTOCOL
      && descriptor.compatibility.maximum_launcher_protocol >= core.LAUNCHER_PROTOCOL
      && descriptor.compatibility.minimum_application_protocol <= applicationProtocol
      && descriptor.compatibility.maximum_application_protocol >= applicationProtocol;
  }

  function rootFor(layout, descriptor) {
    if (descriptor.kind === 'model') return layout.models;
    if (descriptor.kind === 'runtime') return layout.runtimes;
    if (descriptor.kind === 'launcher') return layout.launchers;
    if (descriptor.kind === 'program') return path.join(layout.downloads, 'sha256');
    return null;
  }

  function programDescriptor(release) {
    if (!release || typeof release.version !== 'string' || !SHA256.test(release.artifact_sha256)
      || !Number.isSafeInteger(release.artifact_bytes) || release.artifact_bytes < 1 || typeof release.artifact_url !== 'string') error('artifact_descriptor_invalid', 'signed program artifact descriptor is invalid');
    let url; try { url = new URL(release.artifact_url); } catch { error('artifact_descriptor_invalid', 'signed program artifact URL is invalid'); }
    const slash = url.pathname.lastIndexOf('/') + 1;
    return validateDescriptor({
      authority: { origin: url.origin, path_prefix: url.pathname.slice(0, slash) || '/' },
      compatibility: { minimum_launcher_protocol: release.minimum_launcher_protocol, maximum_launcher_protocol: Math.max(core.LAUNCHER_PROTOCOL, release.minimum_launcher_protocol), minimum_application_protocol: 1, maximum_application_protocol: 1 },
      digest: `sha256:${release.artifact_sha256}`, id: `program-${release.artifact_sha256.slice(0, 16)}`, kind: 'program',
      license: { id: 'release-distribution-contract', acceptance: 'not_required' }, platform: release.platform,
      reachability: 'required', required_free_space_reserve: 0, sha256: release.artifact_sha256, size: release.artifact_bytes, url: release.artifact_url,
    });
  }

  function cachePath(layout, descriptor) {
    const root = rootFor(layout, descriptor);
    return root ? path.join(root, descriptor.sha256) : null;
  }

  function verifyCached(layout, descriptor) {
    validateDescriptor(descriptor);
    const filename = cachePath(layout, descriptor);
    if (!filename || !exists(filename)) return false;
    installer.inspectManagedPath(path.dirname(filename), layout.identity.uid, 0o700);
    const metadata = installer.inspectManagedPath(filename, layout.identity.uid, 0o400, 'file');
    if (!metadata || metadata.nlink !== 1 || metadata.size !== descriptor.size) error('asset_cache_invalid', 'cached asset custody or declared size differs');
    const bytes = core.readOwnedRegular(filename, layout.identity.uid, [0o400], descriptor.size);
    if (digest(bytes) !== descriptor.sha256) error('asset_cache_invalid', 'cached asset digest differs');
    return true;
  }

  function partialPaths(layout, descriptor, owner) {
    if (!/^[0-9a-f]{32}$/.test(owner)) error('asset_partial_invalid', 'asset partial owner is invalid');
    const base = `${descriptor.kind}-${descriptor.sha256}.${owner}`;
    return { partial: path.join(layout.downloads, `${base}.partial`), validator: path.join(layout.downloads, `${base}.validator`) };
  }

  function inspectPartial(filename, uid) {
    if (!exists(filename)) return null;
    const metadata = installer.inspectManagedPath(filename, uid, 0o600, 'file');
    if (!metadata || metadata.nlink !== 1) error('asset_partial_invalid', 'asset partial custody is invalid');
    return metadata;
  }

  function removePartial(paths, layout) {
    for (const filename of [paths.partial, paths.validator]) {
      if (!exists(filename)) continue;
      const metadata = installer.inspectManagedPath(filename, layout.identity.uid, 0o600, 'file');
      if (!metadata || metadata.nlink !== 1) error('asset_partial_invalid', 'asset partial custody is invalid');
      fs.unlinkSync(filename);
    }
    installer.syncDirectory(layout.downloads);
  }

  function parseContentRange(value, offset, total) {
    const match = /^bytes ([0-9]+)-([0-9]+)\/([0-9]+)$/.exec(value || '');
    return Boolean(match && Number(match[1]) === offset && Number(match[2]) + 1 >= offset && Number(match[3]) === total);
  }

  async function acquire(layout, descriptor, owner, source, options = {}) {
    validateDescriptor(descriptor);
    if (descriptor.kind === 'agent_environment_image') return { state: 'not_acquired_optional_image', descriptor };
    if (!compatible(descriptor, options.applicationProtocol || 1)) error('asset_compatibility_invalid', 'required exact asset is incompatible with this launcher/application protocol');
    if (verifyCached(layout, descriptor)) return { state: 'cached_verified', descriptor, path: cachePath(layout, descriptor), downloaded: 0 };
    if (options.offline) error('offline_material_insufficient', 'verified cached material is insufficient for offline reconciliation');
    if (!source || typeof source.downloadAsset !== 'function') error('asset_unavailable', 'the exact signed asset is unavailable');
    installer.ensurePrivateDirectory(layout.downloads, layout.identity.uid);
    installer.ensurePrivateDirectory(rootFor(layout, descriptor), layout.identity.uid);
    const names = partialPaths(layout, descriptor, owner);
    let partial = inspectPartial(names.partial, layout.identity.uid);
    let offset = partial ? partial.size : 0;
    if (offset > descriptor.size) { removePartial(names, layout); offset = 0; partial = null; }
    let validator = null;
    if (offset > 0) {
      const metadata = inspectPartial(names.validator, layout.identity.uid);
      if (!metadata || metadata.size < 1 || metadata.size > 256) { removePartial(names, layout); offset = 0; }
      else validator = core.readOwnedRegular(names.validator, layout.identity.uid, [0o600], 256).toString('utf8');
    }
    let attempts = 0;
    while (attempts < 2) {
      attempts += 1;
      const response = await source.downloadAsset({ url: descriptor.url, offset, validator, maximum_bytes: descriptor.size - offset, descriptor });
      if (!response || !Buffer.isBuffer(response.bytes) || ![200, 206].includes(response.status)
        || response.redirected === true || response.url !== descriptor.url || response.location) error('asset_redirect_refused', 'asset download redirect or response authority was refused');
      if (typeof response.validator !== 'string' || response.validator.length < 1 || response.validator.length > 256) error('asset_validator_invalid', 'asset download validator is missing or invalid');
      if (offset > 0 && response.status === 206 && (response.validator !== validator || !parseContentRange(response.content_range, offset, descriptor.size))) {
        removePartial(names, layout); offset = 0; validator = null; continue;
      }
      if (offset > 0 && response.status === 200) { removePartial(names, layout); offset = 0; validator = null; }
      if (offset === 0 && response.status === 206 && !parseContentRange(response.content_range, 0, descriptor.size)) error('asset_range_invalid', 'asset range response is not exact');
      if (response.bytes.length > descriptor.size - offset) { removePartial(names, layout); error('asset_oversize', 'asset exceeded its signed declared-size ceiling'); }
      let descriptorFd;
      try {
        descriptorFd = fs.openSync(names.partial, fs.constants.O_WRONLY | fs.constants.O_CREAT | fs.constants.O_APPEND | (fs.constants.O_NOFOLLOW || 0), 0o600);
        const before = fs.fstatSync(descriptorFd);
        if (!before.isFile() || before.uid !== layout.identity.uid || before.nlink !== 1 || before.size !== offset) error('asset_partial_invalid', 'asset partial changed during resume');
        fs.writeFileSync(descriptorFd, response.bytes); fs.fsyncSync(descriptorFd); fs.fchmodSync(descriptorFd, 0o600);
      } finally { if (descriptorFd !== undefined) fs.closeSync(descriptorFd); }
      installer.atomicWrite(names.validator, Buffer.from(response.validator), 0o600, layout.identity.uid);
      offset += response.bytes.length; validator = response.validator;
      if (offset < descriptor.size) return { state: 'partial', descriptor, path: names.partial, downloaded: response.bytes.length, offset };
      const bytes = core.readOwnedRegular(names.partial, layout.identity.uid, [0o600], descriptor.size);
      if (digest(bytes) !== descriptor.sha256) { removePartial(names, layout); error('asset_hash_mismatch', 'asset digest differs from its signed descriptor'); }
      fs.chmodSync(names.partial, 0o400);
      const target = cachePath(layout, descriptor);
      if (exists(target)) {
        if (!verifyCached(layout, descriptor)) error('asset_cache_invalid', 'complete asset target is invalid');
        fs.unlinkSync(names.partial);
      } else fs.renameSync(names.partial, target);
      if (exists(names.validator)) fs.unlinkSync(names.validator);
      installer.syncDirectory(path.dirname(target)); installer.syncDirectory(layout.downloads);
      return { state: 'downloaded_verified', descriptor, path: target, downloaded: response.bytes.length };
    }
    error('asset_range_reset_failed', 'asset resume could not establish exact range and validator semantics');
  }

  async function reconcile(layout, descriptors, owner, source, options = {}) {
    const values = validateDescriptors(descriptors);
    const outcomes = [];
    let requiredBytes = 0;
    let optionalImage = 'not_declared';
    for (const descriptor of values) {
      if (descriptor.kind === 'agent_environment_image') {
        optionalImage = options.imageInspector && await options.imageInspector(descriptor) === true ? 'exact_available' : 'stale_spec';
        outcomes.push({ id: descriptor.id, kind: descriptor.kind, state: optionalImage });
        continue;
      }
      if (descriptor.reachability === 'required' && !verifyCached(layout, descriptor)) requiredBytes += descriptor.size;
      if (descriptor.reachability === 'optional') { outcomes.push({ id: descriptor.id, kind: descriptor.kind, state: verifyCached(layout, descriptor) ? 'cached_verified' : 'optional_unavailable' }); continue; }
      let outcome;
      do { outcome = await acquire(layout, descriptor, owner, source, options); } while (outcome.state === 'partial');
      outcomes.push({ id: descriptor.id, kind: descriptor.kind, state: outcome.state });
    }
    return { outcomes, required_bytes: requiredBytes, optional_agent_environment_image: optionalImage };
  }

  function scanPartials(layout) {
    if (!exists(layout.downloads)) return { count: 0, bytes: 0 };
    let count = 0; let bytes = 0;
    for (const name of fs.readdirSync(layout.downloads).sort()) {
      if (!/^(?:program|model|runtime|launcher)-[0-9a-f]{64}\.[0-9a-f]{32}\.(?:partial|validator)$/.test(name)) continue;
      const filename = path.join(layout.downloads, name);
      const metadata = installer.inspectManagedPath(filename, layout.identity.uid, 0o600, 'file');
      if (!metadata || metadata.nlink !== 1) error('asset_partial_invalid', 'asset partial is unsafe');
      count += 1; bytes += metadata.size;
    }
    return { count, bytes };
  }

  function collectPartials(layout, keepOwners = new Set()) {
    let count = 0; let bytes = 0;
    if (!exists(layout.downloads)) return { count, bytes };
    for (const name of fs.readdirSync(layout.downloads).sort()) {
      const match = /^(?:program|model|runtime|launcher)-[0-9a-f]{64}\.([0-9a-f]{32})\.(?:partial|validator)$/.exec(name);
      if (!match || keepOwners.has(match[1])) continue;
      const filename = path.join(layout.downloads, name);
      const metadata = installer.inspectManagedPath(filename, layout.identity.uid, 0o600, 'file');
      if (!metadata || metadata.nlink !== 1) error('asset_partial_invalid', 'asset partial is unsafe');
      fs.unlinkSync(filename); count += 1; bytes += metadata.size;
    }
    if (count) installer.syncDirectory(layout.downloads);
    return { count, bytes };
  }

  function collectAssets(layout, referencedDigests, options = {}) {
    const protectedSet = new Set(referencedDigests || []);
    let count = 0; let bytes = 0;
    const candidates = [];
    for (const root of [path.join(layout.downloads, 'sha256'), layout.runtimes, layout.launchers]) {
      if (!exists(root)) continue;
      for (const name of fs.readdirSync(root)) {
        if (!SHA256.test(name) || protectedSet.has(name)) continue;
        const filename = path.join(root, name);
        const metadata = installer.inspectManagedPath(filename, layout.identity.uid, 0o400, 'file');
        if (!metadata || metadata.nlink !== 1) continue;
        candidates.push({ filename, root, digest: name, size: metadata.size, mtime: metadata.mtimeMs });
      }
    }
    candidates.sort((left, right) => left.mtime - right.mtime || left.digest.localeCompare(right.digest));
    for (const candidate of candidates) {
      const refreshed = options.references ? new Set(options.references()) : protectedSet;
      if (refreshed.has(candidate.digest)) continue;
      const metadata = installer.inspectManagedPath(candidate.filename, layout.identity.uid, 0o400, 'file');
      if (!metadata || metadata.nlink !== 1 || metadata.size !== candidate.size) error('asset_gc_target_invalid', 'asset GC target changed before deletion');
      fs.unlinkSync(candidate.filename); installer.syncDirectory(candidate.root); count += 1; bytes += candidate.size;
      if (options.bytesNeeded && bytes >= options.bytesNeeded) break;
    }
    return { count, bytes };
  }

  function spaceSummary(facts) {
    const keys = ['available', 'reclaimable', 'required'];
    exactKeys(facts, keys, 'space_preflight_invalid');
    for (const key of keys) if (!Number.isSafeInteger(facts[key]) || facts[key] < 0) error('space_preflight_invalid', 'space summary is invalid');
    return facts;
  }

  function requireSpace(facts) {
    const summary = spaceSummary(facts);
    if (summary.available < summary.required) error('insufficient_space', `required=${summary.required} available=${summary.available} reclaimable=${summary.reclaimable} bytes; preserve active/rollback data and free space outside Voice Agent reconstructible storage`);
    return summary;
  }

  return {
    ASSET_KINDS, acquire, cachePath, collectAssets, collectPartials, compatible, programDescriptor, reconcile, requireSpace,
    scanPartials, spaceSummary, validateDescriptor, validateDescriptors, verifyCached,
  };
};
