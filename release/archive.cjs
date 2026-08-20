'use strict';

const crypto = require('node:crypto');
const zlib = require('node:zlib');

function fail(message) { throw new Error(message); }
function octal(value, length) {
  const digits = value.toString(8);
  if (digits.length > length - 1) fail('tar numeric field overflow');
  return Buffer.from(`${digits.padStart(length - 1, '0')}\0`, 'ascii');
}
function pathFields(name) {
  const bytes = Buffer.byteLength(name);
  if (bytes <= 100) return { name, prefix: '' };
  const candidates = [];
  for (let index = 0; index < name.length; index += 1) if (name[index] === '/') candidates.push(index);
  for (const index of candidates.reverse()) {
    const prefix = name.slice(0, index); const tail = name.slice(index + 1);
    if (Buffer.byteLength(prefix) <= 155 && Buffer.byteLength(tail) <= 100) return { name: tail, prefix };
  }
  fail('tar path cannot be represented as ustar');
}
function field(header, offset, length, bytes) {
  if (bytes.length > length) fail('tar field overflow');
  bytes.copy(header, offset);
}
function headerFor(entry, timestamp) {
  const header = Buffer.alloc(512);
  const fields = pathFields(entry.path);
  field(header, 0, 100, Buffer.from(fields.name));
  field(header, 100, 8, octal(Number.parseInt(entry.mode, 8), 8));
  field(header, 108, 8, octal(0, 8)); field(header, 116, 8, octal(0, 8));
  field(header, 124, 12, octal(entry.type === 'file' ? entry.bytes.length : 0, 12));
  field(header, 136, 12, octal(timestamp, 12));
  header.fill(0x20, 148, 156);
  header[156] = entry.type === 'file' ? 0x30 : entry.type === 'directory' ? 0x35 : entry.type === 'symlink' ? 0x32 : entry.type === 'hardlink' ? 0x31 : fail('unsupported tar type');
  if (entry.target) field(header, 157, 100, Buffer.from(entry.target));
  field(header, 257, 6, Buffer.from('ustar\0', 'ascii')); field(header, 263, 2, Buffer.from('00', 'ascii'));
  field(header, 345, 155, Buffer.from(fields.prefix));
  let checksum = 0; for (const byte of header) checksum += byte;
  const checksumBytes = Buffer.from(`${checksum.toString(8).padStart(6, '0')}\0 `, 'ascii');
  field(header, 148, 8, checksumBytes);
  return header;
}

function createTar(entries, timestamp) {
  if (!Number.isSafeInteger(timestamp) || timestamp < 0) fail('tar timestamp is invalid');
  const chunks = [];
  for (const entry of entries) {
    chunks.push(headerFor(entry, timestamp));
    if (entry.type === 'file') {
      chunks.push(entry.bytes);
      const padding = (512 - (entry.bytes.length % 512)) % 512;
      if (padding) chunks.push(Buffer.alloc(padding));
    }
  }
  chunks.push(Buffer.alloc(1024));
  return Buffer.concat(chunks);
}

function createTarZstd(entries, timestamp) {
  return zlib.zstdCompressSync(createTar(entries, timestamp), { params: { [zlib.constants.ZSTD_c_compressionLevel]: 19, [zlib.constants.ZSTD_c_checksumFlag]: 1 } });
}

function sha256(bytes) { return crypto.createHash('sha256').update(bytes).digest('hex'); }

module.exports = { createTar, createTarZstd, sha256 };
