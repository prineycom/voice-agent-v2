'use strict';

module.exports = function createReleaseSourceModule(core) {
  const crypto = require('node:crypto');
  const dns = require('node:dns');
  const fs = require('node:fs');
  const https = require('node:https');
  const net = require('node:net');
  const os = require('node:os');
  const path = require('node:path');
  const { TextDecoder } = require('node:util');
  const zlib = require('node:zlib');

  const CHANNEL_LIMIT = 256 * 1024;
  const SIGNATURE_LIMIT = 1024;
  // The closed CPython/CUDA/Torch runtime is intentionally large. These remain
  // explicit synchronous-archive ceilings; physical assembly must measure below them.
  const ARTIFACT_LIMIT = 3 * 1024 * 1024 * 1024;
  const EXPANDED_LIMIT = (4 * 1024 * 1024 * 1024) - 1;
  const ENTRY_LIMIT = 1024 * 1024 * 1024;
  const ENTRY_COUNT_LIMIT = 100001;
  const PATH_BYTES_LIMIT = 1024;
  const EXPANSION_RATIO_LIMIT = 256;
  const CONNECT_TIMEOUT_MS = 10000;
  const RESPONSE_TIMEOUT_MS = 30000;
  const RANGE_CHUNK_BYTES = 8 * 1024 * 1024;
  const SHA256 = /^[0-9a-f]{64}$/;
  const VERSION = /^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-[0-9A-Za-z.-]+)?$/;
  const RFC3339 = /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$/;
  const COMMIT = /^[0-9a-f]{40}$/;
  const decoder = new TextDecoder('utf-8', { fatal: true });

  function error(code, message) { throw new core.LauncherError(code, message); }
  function exactKeys(value, keys, code) {
    if (!value || typeof value !== 'object' || Array.isArray(value)
      || Object.keys(value).sort().join('\0') !== [...keys].sort().join('\0')) error(code, 'release source configuration is not closed');
  }
  function digest(bytes) { return crypto.createHash('sha256').update(bytes).digest('hex'); }

  function parseHttpsUrl(value, code = 'release_url_invalid', allowSearch = false) {
    if (typeof value !== 'string' || value.length > 2048) error(code, 'release URL is invalid');
    let url;
    try { url = new URL(value); } catch { error(code, 'release URL is invalid'); }
    const labels = url.hostname.split('.');
    if (url.protocol !== 'https:' || url.username || url.password || url.hash || (!allowSearch && url.search) || url.port
      || value !== url.href || net.isIP(url.hostname) || url.hostname !== url.hostname.toLowerCase()
      || url.hostname === 'localhost' || labels.length < 2
      || labels.some((label) => !/^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/.test(label))
      || url.pathname.length < 2 || url.pathname.includes('\\') || url.pathname.includes('//')
      || url.pathname.split('/').some((part) => part === '.' || part === '..' || /%2f|%5c|%00/i.test(part))) {
      error(code, 'release URL authority is invalid');
    }
    return url;
  }

  function validateBuildConfiguration(document) {
    exactKeys(document, ['github_api_origin', 'github_asset_origins', 'github_channel_path', 'github_ref', 'github_repository', 'launcher_protocol', 'launcher_version', 'public_key_pem', 'source_commit', 'source_timestamp'], 'release_authority_unprovisioned');
    if (!VERSION.test(document.launcher_version) || !Number.isSafeInteger(document.launcher_protocol) || document.launcher_protocol < 1
      || !COMMIT.test(document.source_commit) || !RFC3339.test(document.source_timestamp)
      || new Date(document.source_timestamp).toISOString().replace('.000Z', 'Z') !== document.source_timestamp
      || !/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(document.github_repository)
      || !/^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$/.test(document.github_ref)
      || document.github_ref.includes('..') || !/^[A-Za-z0-9][A-Za-z0-9._/-]{0,255}\.json$/.test(document.github_channel_path)
      || document.github_channel_path.split('/').some((part) => part === '.' || part === '..')) {
      error('release_authority_unprovisioned', 'launcher build authority inputs are invalid');
    }
    let api;
    try { api = new URL(document.github_api_origin); } catch { error('release_authority_unprovisioned', 'private GitHub API authority is invalid'); }
    if (api.href !== 'https://api.github.com/') error('release_authority_unprovisioned', 'private GitHub API authority is invalid');
    if (!Array.isArray(document.github_asset_origins) || document.github_asset_origins.length < 1 || document.github_asset_origins.length > 2
      || new Set(document.github_asset_origins).size !== document.github_asset_origins.length
      || document.github_asset_origins.some((origin) => !['https://release-assets.githubusercontent.com/', 'https://objects.githubusercontent.com/'].includes(origin))) {
      error('release_authority_unprovisioned', 'private GitHub asset authority is invalid');
    }
    let key;
    try {
      key = crypto.createPublicKey(document.public_key_pem);
      const canonical = key.export({ type: 'spki', format: 'pem' }).toString();
      if (key.asymmetricKeyType !== 'ed25519' || canonical !== document.public_key_pem
        || (document.public_key_pem.match(/BEGIN PUBLIC KEY/g) || []).length !== 1) throw new Error('invalid');
    } catch { error('release_authority_unprovisioned', 'launcher trust root is invalid'); }
    return Object.freeze({ ...document, github_asset_origins: Object.freeze([...document.github_asset_origins]), public_key_sha256: digest(key.export({ type: 'spki', format: 'der' })) });
  }

  function publicAddress(address) {
    const family = net.isIP(address);
    if (family === 4) {
      const octets = address.split('.').map(Number);
      return !(octets[0] === 0 || octets[0] === 10 || octets[0] === 127 || octets[0] >= 224
        || (octets[0] === 100 && octets[1] >= 64 && octets[1] <= 127)
        || (octets[0] === 169 && octets[1] === 254) || (octets[0] === 172 && octets[1] >= 16 && octets[1] <= 31)
        || (octets[0] === 192 && [0, 2, 168].includes(octets[1])) || (octets[0] === 198 && [18, 19, 51].includes(octets[1]))
        || (octets[0] === 203 && octets[1] === 0 && octets[2] === 113));
    }
    if (family === 6) {
      const normalized = address.toLowerCase();
      if (normalized.startsWith('::ffff:')) return publicAddress(normalized.slice(7));
      return !(normalized === '::' || normalized === '::1' || normalized.startsWith('fc') || normalized.startsWith('fd') || normalized.startsWith('fe8') || normalized.startsWith('fe9') || normalized.startsWith('fea') || normalized.startsWith('feb')
        || normalized.startsWith('ff') || normalized.startsWith('2001:db8'));
    }
    return false;
  }

  function strictLookup(hostname, options, callback) {
    dns.lookup(hostname, { ...options, all: true, verbatim: true }, (reason, addresses) => {
      if (reason) return callback(reason);
      const accepted = addresses.filter((item) => publicAddress(item.address));
      if (!accepted.length || accepted.length !== addresses.length) return callback(new Error('release DNS resolved outside public authority'));
      if (options && options.all) return callback(null, accepted);
      return callback(null, accepted[0].address, accepted[0].family);
    });
  }

  function systemTransport(request) {
    return new Promise((resolve, reject) => {
      const url = parseHttpsUrl(request.url, 'release_url_invalid', true);
      let settled = false;
      const finish = (reason, value) => {
        if (settled) return;
        settled = true;
        clearTimeout(overall);
        if (reason) reject(reason); else resolve(value);
      };
      const client = https.request(url, {
        method: 'GET', headers: request.headers || {}, lookup: strictLookup, rejectUnauthorized: true,
        minVersion: 'TLSv1.2', maxVersion: 'TLSv1.3', servername: url.hostname,
        timeout: CONNECT_TIMEOUT_MS, agent: false,
      }, (response) => {
        const chunks = []; let length = 0;
        response.setTimeout(RESPONSE_TIMEOUT_MS, () => response.destroy(new Error('release response timed out')));
        response.on('data', (chunk) => {
          length += chunk.length;
          if (length > request.maximumBytes) response.destroy(new Error('release response exceeded bound'));
          else chunks.push(chunk);
        });
        response.on('end', () => finish(null, {
          status: response.statusCode, headers: response.headers, bytes: Buffer.concat(chunks),
          url: url.href, redirected: false, authorized: Boolean(response.socket.authorized),
          encrypted: Boolean(response.socket.encrypted), remoteAddress: response.socket.remoteAddress,
        }));
      });
      const overall = setTimeout(() => client.destroy(new Error('release request deadline exceeded')), CONNECT_TIMEOUT_MS + RESPONSE_TIMEOUT_MS);
      client.on('timeout', () => client.destroy(new Error('release connection timed out')));
      client.on('error', (reason) => finish(reason));
      client.end();
    });
  }

  function headerValue(headers, name) {
    const value = headers && headers[name];
    if (Array.isArray(value)) error('release_response_invalid', 'release response header is ambiguous');
    return value === undefined ? null : String(value);
  }

  function validateResponse(response, request) {
    if (!response || !Buffer.isBuffer(response.bytes) || response.url !== request.url || response.redirected === true
      || response.location || response.encrypted === false || response.authorized === false
      || (response.remoteAddress && !publicAddress(response.remoteAddress))) error('release_response_invalid', 'release response authority is invalid');
    if (response.status >= 300 && response.status <= 399) error('release_redirect_refused', 'release redirect was refused');
    if (response.status !== request.status) error('release_response_invalid', 'release response status is invalid');
    const contentLength = headerValue(response.headers, 'content-length');
    if (!/^(0|[1-9][0-9]*)$/.test(contentLength || '') || Number(contentLength) !== response.bytes.length
      || response.bytes.length > request.maximumBytes || (request.exactBytes !== null && response.bytes.length !== request.exactBytes)
      || headerValue(response.headers, 'transfer-encoding') !== null
      || ![null, 'identity'].includes(headerValue(response.headers, 'content-encoding'))) {
      error('release_size_invalid', 'release response length is invalid');
    }
    return response;
  }

  function octal(field, label, allowEmpty = false) {
    const text = field.toString('ascii').replace(/\0.*$/, '').trim();
    if (allowEmpty && text === '') return 0;
    if (!/^[0-7]+$/.test(text)) error('archive_metadata_invalid', `${label} is not canonical octal`);
    const value = Number.parseInt(text, 8);
    if (!Number.isSafeInteger(value)) error('archive_metadata_invalid', `${label} is out of range`);
    return value;
  }

  function text(field, label) {
    const end = field.indexOf(0); const slice = end < 0 ? field : field.subarray(0, end);
    try { return decoder.decode(slice); } catch { error('archive_metadata_invalid', `${label} is not UTF-8`); }
  }

  function tarChecksum(header) {
    let sum = 0;
    for (let index = 0; index < 512; index += 1) sum += index >= 148 && index < 156 ? 32 : header[index];
    return sum;
  }

  function decodeArtifact(artifactBytes) {
    const compressed = artifactBytes.length >= 4 && artifactBytes.subarray(0, 4).equals(Buffer.from([0x28, 0xb5, 0x2f, 0xfd]));
    let bytes;
    try {
      bytes = compressed ? zlib.zstdDecompressSync(artifactBytes, { maxOutputLength: EXPANDED_LIMIT }) : Buffer.from(artifactBytes);
    } catch { error('archive_compression_invalid', 'release archive compression is invalid or exceeds its bound'); }
    if (bytes.length > EXPANDED_LIMIT || bytes.length > artifactBytes.length * EXPANSION_RATIO_LIMIT + 16 * 1024 * 1024) {
      error('archive_ratio_invalid', 'release archive expansion ratio is unsafe');
    }
    return bytes;
  }

  function indexArchive(artifactBytes) {
    const tar = decodeArtifact(artifactBytes);
    const archiveEntries = []; const contents = new Map();
    let offset = 0; let ended = false; let totalFileBytes = 0;
    while (offset + 512 <= tar.length) {
      const header = tar.subarray(offset, offset + 512); offset += 512;
      if (header.every((byte) => byte === 0)) {
        if (offset + 512 > tar.length || !tar.subarray(offset, offset + 512).every((byte) => byte === 0)) error('archive_truncated', 'release archive end marker is incomplete');
        offset += 512;
        if (!tar.subarray(offset).every((byte) => byte === 0)) error('archive_trailing_data', 'release archive has trailing data');
        ended = true; break;
      }
      if (archiveEntries.length >= ENTRY_COUNT_LIMIT) error('archive_entry_limit', 'release archive has too many entries');
      if (text(header.subarray(257, 263), 'tar magic') !== 'ustar' || text(header.subarray(263, 265), 'tar version') !== '00') error('archive_metadata_invalid', 'release archive is not canonical ustar');
      if (octal(header.subarray(148, 156), 'checksum') !== tarChecksum(header)) error('archive_checksum_invalid', 'release archive header checksum differs');
      const prefix = text(header.subarray(345, 500), 'path prefix'); const name = text(header.subarray(0, 100), 'path');
      const pathname = prefix ? `${prefix}/${name}` : name;
      core.validateArchivePath(pathname, 'archive_path_invalid');
      if (Buffer.byteLength(pathname) > PATH_BYTES_LIMIT) error('archive_path_invalid', 'release archive path is too long');
      const mode = octal(header.subarray(100, 108), 'mode'); const uid = octal(header.subarray(108, 116), 'uid'); const gid = octal(header.subarray(116, 124), 'gid');
      const size = octal(header.subarray(124, 136), 'size'); octal(header.subarray(136, 148), 'mtime');
      const typeByte = header[156]; const type = typeByte === 0 || typeByte === 48 ? 'file' : typeByte === 53 ? 'directory' : typeByte === 50 ? 'symlink' : typeByte === 49 ? 'hardlink' : null;
      const target = type === 'symlink' || type === 'hardlink' ? text(header.subarray(157, 257), 'link target') : null;
      if (!type) error('archive_unsafe_type', 'release archive contains an unsafe or extended metadata type');
      if (uid !== 0 || gid !== 0 || mode > 0o777 || (mode & 0o7000) !== 0
        || text(header.subarray(265, 297), 'owner name') !== '' || text(header.subarray(297, 329), 'group name') !== ''
        || octal(header.subarray(329, 337), 'device major', true) !== 0 || octal(header.subarray(337, 345), 'device minor', true) !== 0) {
        error('archive_metadata_invalid', 'release archive contains undeclared ownership or device metadata');
      }
      if (size > ENTRY_LIMIT || ((type !== 'file') && size !== 0)) error('archive_entry_limit', 'release archive entry size is unsafe');
      const padded = Math.ceil(size / 512) * 512;
      if (offset + padded > tar.length) error('archive_truncated', 'release archive entry is truncated');
      const bytes = tar.subarray(offset, offset + size); offset += padded;
      if (type === 'file') { totalFileBytes += size; contents.set(pathname, Buffer.from(bytes)); }
      if (totalFileBytes > EXPANDED_LIMIT) error('archive_entry_limit', 'release archive expanded bytes exceed the bound');
      archiveEntries.push({ path: pathname, type, mode: `0${mode.toString(8).padStart(3, '0')}`, size, sha256: type === 'file' ? digest(bytes) : null, target });
    }
    if (!ended || archiveEntries.length < 2) error('archive_truncated', 'release archive is incomplete');
    const manifestMatches = archiveEntries.filter((entry) => entry.path === 'release-manifest.json');
    const manifestBytes = contents.get('release-manifest.json');
    if (manifestMatches.length !== 1 || manifestMatches[0].type !== 'file' || manifestMatches[0].mode !== '0444' || !manifestBytes || manifestBytes.length > 4 * 1024 * 1024) {
      error('archive_manifest_invalid', 'release archive manifest is missing or ambiguous');
    }
    const manifest = core.validateArtifactManifest(core.parseCanonicalJson(manifestBytes, 4 * 1024 * 1024));
    core.validateArchiveEntries(archiveEntries, manifest, manifestBytes);
    return { manifest, manifestBytes, archiveEntries, readEntry: async (name) => {
      if (!contents.has(name)) error('archive_entry_unavailable', 'declared archive entry bytes are unavailable');
      return Buffer.from(contents.get(name));
    } };
  }

  class HttpsReleaseSource {
    constructor(buildConfiguration, options = {}) {
      this.configuration = validateBuildConfiguration(buildConfiguration);
      this.transport = options.transport || systemTransport;
      this.tokenPath = options.tokenPath || path.join(process.env.XDG_CONFIG_HOME || path.join(os.homedir(), '.config'), 'voice-agent', 'private', 'github-release-token');
      this.tokenReader = options.tokenReader || (() => {
        let descriptor;
        try {
          descriptor = fs.openSync(this.tokenPath, fs.constants.O_RDONLY | (fs.constants.O_NOFOLLOW || 0));
          const metadata = fs.fstatSync(descriptor);
          const lexical = fs.lstatSync(this.tokenPath);
          if (!metadata.isFile() || !lexical.isFile() || metadata.uid !== process.geteuid() || lexical.uid !== process.geteuid()
            || metadata.ino !== lexical.ino || metadata.dev !== lexical.dev || metadata.nlink !== 1 || (metadata.mode & 0o777) !== 0o600 || metadata.size < 20 || metadata.size > 512) throw new Error('custody');
          const token = fs.readFileSync(descriptor, 'utf8').trim();
          if (!/^[A-Za-z0-9_]+$/.test(token) || token.length < 20 || token.length > 500) throw new Error('token');
          return token;
        } catch { error('github_token_unavailable', 'create one mode-0600 fine-grained read-only GitHub token file in the Voice Agent private config directory'); }
        finally { if (descriptor !== undefined) fs.closeSync(descriptor); }
      });
    }

    githubApi(pathname) {
      return `https://api.github.com/repos/${this.configuration.github_repository}/${pathname}`;
    }

    authorizationHeaders(accept = 'application/vnd.github+json') {
      return { Accept: accept, 'Accept-Encoding': 'identity', Authorization: `Bearer ${this.tokenReader()}`, 'User-Agent': 'voice-agent-release-launcher' };
    }

    async rawFetch(urlValue, options = {}) {
      const url = parseHttpsUrl(urlValue, 'release_url_invalid', true);
      let response;
      try { response = await this.transport({ url: url.href, headers: options.headers || { Accept: 'application/octet-stream', 'Accept-Encoding': 'identity' }, maximumBytes: options.maximumBytes }); }
      catch { error(options.timeoutCode || 'release_network_unavailable', 'release request failed without content'); }
      return response;
    }

    async fetch(urlValue, options = {}) {
      const url = parseHttpsUrl(urlValue, 'release_url_invalid', options.allowSearch === true);
      const response = await this.rawFetch(url.href, options);
      return validateResponse(response, { url: url.href, maximumBytes: options.maximumBytes, exactBytes: options.exactBytes ?? null, status: options.status || 200 });
    }

    async githubContent(relative, maximumBytes) {
      const apiUrl = `${this.githubApi(`contents/${relative}`)}?ref=${encodeURIComponent(this.configuration.github_ref)}`;
      const response = await this.fetch(apiUrl, { maximumBytes: Math.min(1024 * 1024, maximumBytes * 2 + 65536), headers: this.authorizationHeaders(), allowSearch: true });
      let value;
      try { value = JSON.parse(response.bytes.toString('utf8')); } catch { error('github_response_invalid', 'private GitHub content response is invalid'); }
      if (!value || value.type !== 'file' || value.path !== relative || value.name !== relative.split('/').at(-1) || value.encoding !== 'base64'
        || value.url !== apiUrl || !Number.isSafeInteger(value.size) || value.size < 1 || value.size > maximumBytes || typeof value.content !== 'string') {
        error('github_response_invalid', 'private GitHub content identity differs');
      }
      const bytes = Buffer.from(value.content.replace(/\s/g, ''), 'base64');
      if (bytes.length !== value.size || bytes.length > maximumBytes || bytes.toString('base64') !== value.content.replace(/\s/g, '')) error('github_response_invalid', 'private GitHub content bytes differ');
      return bytes;
    }

    githubAssetIdentity(urlValue) {
      const url = parseHttpsUrl(urlValue, 'artifact_unavailable');
      const prefix = `/repos/${this.configuration.github_repository}/releases/assets/`;
      if (url.origin !== this.configuration.github_api_origin.slice(0, -1) || !url.pathname.startsWith(prefix)
        || !/^[1-9][0-9]*$/.test(url.pathname.slice(prefix.length))) error('artifact_unavailable', 'signed private GitHub asset identity is invalid');
      return url;
    }

    async githubAsset(urlValue, options) {
      const url = this.githubAssetIdentity(urlValue);
      const first = await this.rawFetch(url.href, { maximumBytes: 0, headers: this.authorizationHeaders('application/octet-stream') });
      if (!first || first.status !== 302 || !first.headers || Buffer.isBuffer(first.bytes) === false || first.bytes.length !== 0) error('release_redirect_refused', 'private GitHub asset redirect is invalid');
      const location = headerValue(first.headers, 'location');
      let target;
      try { target = parseHttpsUrl(location, 'release_redirect_refused', true); } catch { error('release_redirect_refused', 'private GitHub asset redirect is invalid'); }
      if (!this.configuration.github_asset_origins.includes(`${target.origin}/`) || target.search.length > 1536
        || /[^\x21-\x7e]/.test(target.search) || [...target.searchParams.keys()].some((name) => !/^[A-Za-z0-9_-]{1,32}$/.test(name))) error('release_redirect_refused', 'private GitHub asset redirect host or query is invalid');
      const headers = { Accept: 'application/octet-stream', 'Accept-Encoding': 'identity', ...(options.rangeHeaders || {}) };
      const second = await this.fetch(target.href, { ...options, headers });
      return { ...second, url: url.href, redirected: false };
    }

    trustedPublicKey() { return this.configuration.public_key_pem; }

    async acquireChannel() {
      const channelBytes = await this.githubContent(this.configuration.github_channel_path, CHANNEL_LIMIT);
      const signatureBytes = await this.githubContent(`${this.configuration.github_channel_path}.sig`, SIGNATURE_LIMIT);
      return { channelBytes, signatureBytes, publicKeyPem: this.configuration.public_key_pem };
    }

    async acquireArtifact(release) {
      let artifactUrl;
      try { artifactUrl = this.githubAssetIdentity(release && release.artifact_url); } catch { error('artifact_unavailable', 'signed artifact authority is invalid'); }
      if (!release || !Number.isSafeInteger(release.artifact_bytes) || release.artifact_bytes < 1 || release.artifact_bytes > ARTIFACT_LIMIT || !SHA256.test(release.artifact_sha256)) error('artifact_unavailable', 'signed artifact bounds are invalid');
      const response = await this.githubAsset(artifactUrl.href, { maximumBytes: release.artifact_bytes, exactBytes: release.artifact_bytes, status: 200 });
      if (digest(response.bytes) !== release.artifact_sha256 || !response.bytes.subarray(0, 4).equals(Buffer.from([0x28, 0xb5, 0x2f, 0xfd]))) error('artifact_identity_mismatch', 'artifact digest or compression differs');
      return { artifactBytes: response.bytes, ...indexArchive(response.bytes), cached: false, url: release.artifact_url, redirected: false };
    }

    async downloadAsset(request) {
      const descriptor = request && request.descriptor;
      if (!descriptor || request.url !== descriptor.url || !Number.isSafeInteger(request.offset) || request.offset < 0 || request.offset >= descriptor.size) error('asset_unavailable', 'asset request is invalid');
      const status = 206;
      const end = Math.min(descriptor.size - 1, request.offset + RANGE_CHUNK_BYTES - 1);
      const rangeHeaders = { Range: `bytes=${request.offset}-${end}` };
      if (request.offset > 0) rangeHeaders['If-Range'] = request.validator;
      const response = await this.githubAsset(request.url, { maximumBytes: end - request.offset + 1, exactBytes: end - request.offset + 1, status, rangeHeaders });
      const etag = headerValue(response.headers, 'etag');
      if (!etag || !/^"[\x21\x23-\x7e]{1,250}"$/.test(etag)) error('asset_validator_invalid', 'asset response has no exact strong validator');
      if (headerValue(response.headers, 'accept-ranges') !== 'bytes') error('asset_range_invalid', 'asset server does not promise exact byte ranges');
      const contentRange = headerValue(response.headers, 'content-range');
      if (contentRange !== `bytes ${request.offset}-${end}/${descriptor.size}`) error('asset_range_invalid', 'asset range response is invalid');
      return { status, bytes: response.bytes, validator: etag, content_range: contentRange, redirected: false, url: request.url };
    }
  }

  class UnprovisionedSource {
    trustedPublicKey() { error('release_authority_unprovisioned', 'launcher build has no valid pinned release authority'); }
    async acquireChannel() { error('release_authority_unprovisioned', 'launcher build has no valid pinned release authority; nothing was installed'); }
    async acquireArtifact() { error('artifact_unavailable', 'release artifact is unavailable'); }
    async downloadAsset() { error('asset_unavailable', 'release asset is unavailable'); }
  }

  function createProductionSource(buildConfiguration, options = {}) {
    try { return new HttpsReleaseSource(buildConfiguration, options); } catch (reason) {
      if (options.failClosed === false) throw reason;
      return new UnprovisionedSource();
    }
  }

  return {
    ARTIFACT_LIMIT, CHANNEL_LIMIT, CONNECT_TIMEOUT_MS, ENTRY_COUNT_LIMIT, ENTRY_LIMIT, EXPANSION_RATIO_LIMIT,
    HttpsReleaseSource, RANGE_CHUNK_BYTES, RESPONSE_TIMEOUT_MS, UnprovisionedSource, createProductionSource, indexArchive,
    parseHttpsUrl, publicAddress, validateBuildConfiguration, validateResponse,
  };
};
