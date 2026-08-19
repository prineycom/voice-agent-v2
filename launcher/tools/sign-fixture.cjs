#!/usr/bin/env node
'use strict';

const fs = require('node:fs');
const path = require('node:path');
const { canonicalJson, signCanonicalFixture } = require('../voice-agent.cjs');

function usage() {
  process.stderr.write('usage: sign-fixture.cjs --private-key <test-key.pem> --input <document.json> --output <prefix>\n');
  process.exit(2);
}

const values = {};
for (let index = 2; index < process.argv.length; index += 2) {
  const name = process.argv[index];
  const value = process.argv[index + 1];
  if (!['--private-key', '--input', '--output'].includes(name) || !value) usage();
  values[name] = value;
}
if (Object.keys(values).length !== 3) usage();
const document = JSON.parse(fs.readFileSync(values['--input'], 'utf8'));
const privateKey = fs.readFileSync(values['--private-key']);
const signed = signCanonicalFixture(document, privateKey);
const prefix = path.resolve(values['--output']);
fs.writeFileSync(`${prefix}.json`, signed.bytes, { mode: 0o600 });
fs.writeFileSync(`${prefix}.json.sig`, signed.signature, { mode: 0o600 });
process.stdout.write(`${canonicalJson({ bytes: signed.bytes.length, sha256: require('node:crypto').createHash('sha256').update(signed.bytes).digest('hex') })}\n`);
