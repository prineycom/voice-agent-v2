'use strict';

const crypto = require('node:crypto');

function hash(bytes) { return crypto.createHash('sha256').update(bytes).digest('hex'); }

function aggregate(files) {
  const digest = crypto.createHash('sha256');
  for (const file of [...files].sort((left, right) => left.relative_path.localeCompare(right.relative_path))) {
    digest.update(Buffer.from(`${file.relative_path}\0${file.size}\0${file.sha256}\n`));
  }
  return digest.digest('hex');
}

function assetDescriptor(core, id, kind, bytes, options = {}) {
  const sha256 = hash(bytes);
  const image = kind === 'agent_environment_image';
  return {
    authority: { origin: 'https://assets.example.invalid', path_prefix: '/voice-agent/' },
    compatibility: { minimum_launcher_protocol: 1, maximum_launcher_protocol: 1, minimum_application_protocol: 1, maximum_application_protocol: 1 },
    digest: `sha256:${sha256}`,
    id,
    kind,
    license: { id: image ? 'OCI-fixture' : 'Fixture-Test-Only', acceptance: 'accepted' },
    platform: core.SUPPORTED_PLATFORM,
    reachability: image ? 'optional' : 'required',
    required_free_space_reserve: 1024,
    sha256,
    size: bytes.length,
    url: image
      ? `https://assets.example.invalid/voice-agent/images/environment@sha256:${sha256}`
      : `https://assets.example.invalid/voice-agent/${kind}/${sha256}`,
    ...options,
  };
}

function closedFixture(core, suffix = 'fixture') {
  const modelMembers = [
    ['stt', 'config.json'], ['stt', 'model.bin'], ['stt', 'preprocessor_config.json'], ['stt', 'tokenizer.json'], ['stt', 'vocabulary.json'],
    ['llm', 'LFM2.5-2.6B-Q4_K_M.gguf'], ['tts', 'v5_5_ru.pt'], ['vad', 'silero_vad_v6.onnx'],
  ];
  const assetContents = new Map();
  const assets = [];
  const grouped = new Map();
  for (const [kind, relativePath] of modelMembers) {
    const id = `${kind}-${relativePath.replace(/[^A-Za-z0-9]+/g, '-').replace(/-+$/g, '').toLowerCase()}-${suffix}`.slice(0, 64);
    const bytes = Buffer.from(`${suffix}:${kind}:${relativePath}:exact`);
    const descriptor = assetDescriptor(core, id, 'model', bytes);
    assets.push(descriptor); assetContents.set(id, bytes);
    const file = { asset_id: id, relative_path: relativePath, sha256: descriptor.sha256, size: descriptor.size };
    if (!grouped.has(kind)) grouped.set(kind, []);
    grouped.get(kind).push(file);
  }
  const modelSets = {
    schema: 'voice-agent.model-sets.v1',
    sets: ['stt', 'llm', 'tts', 'vad'].map((kind) => {
      const files = grouped.get(kind);
      return { aggregate_sha256: aggregate(files), files, id: `${kind}-${suffix}`.slice(0, 64), kind };
    }),
  };
  const modelSetsBytes = Buffer.from(core.canonicalJson(modelSets));
  const runtimeBytes = Buffer.from(`${suffix}:runtime:exact`);
  const runtime = assetDescriptor(core, `runtime-${suffix}`.slice(0, 64), 'runtime', runtimeBytes);
  const imageBytes = Buffer.from(`${suffix}:image:exact`);
  const image = assetDescriptor(core, `environment-${suffix}`.slice(0, 64), 'agent_environment_image', imageBytes);
  assets.push(runtime, image); assetContents.set(runtime.id, runtimeBytes); assetContents.set(image.id, imageBytes);

  const runtimeFiles = new Map([
    ['runtime/python/bin/python3', Buffer.from(`${suffix}:fixture-python`)],
    ['runtime/livekit/bin/livekit-server', Buffer.from(`${suffix}:fixture-livekit`)],
    ['runtime/llama/bin/llama-server', Buffer.from(`${suffix}:fixture-llama`)],
  ]);
  const receiptFiles = [...runtimeFiles].map(([name, bytes]) => ({ mode: '0555', path: name.slice('runtime/'.length), sha256: hash(bytes), size: bytes.length }));
  const runtimeReceipt = {
    schema: 'voice-agent.runtime-bundle.v1',
    source: { sha256: hash(Buffer.from(`${suffix}:runtime-source`)) },
    test_only: false,
    files: receiptFiles,
  };
  const runtimeDescriptor = {
    schema: 'voice-agent.bundled-runtime.v1', platform: core.SUPPORTED_PLATFORM, application_protocol: 1,
    python: { version: '3.12.13' }, cuda: { runtime: 'cuda-12.9' }, libc: { family: 'glibc', minimum: '2.28' },
  };
  const runtimeTemplate = {
    schema: 'voice-agent.runtime-config-template.v1', application_protocol: 1,
    executables: { python: 'runtime/python/bin/python3', livekit: 'runtime/livekit/bin/livekit-server', llama: 'runtime/llama/bin/llama-server' },
    model_sets: Object.fromEntries(modelSets.sets.map((set) => [set.kind, set.aggregate_sha256])),
    mutable_paths: { agent_data: 'xdg-data/agent-environment', logs: 'xdg-state/logs', state: 'xdg-state/runtime', temp: 'xdg-runtime/service' }, web: 'web',
  };
  const contents = new Map([
    ['bin/voice-agent-runtime', Buffer.from('#!/bin/sh\nexit 0\n')],
    ['descriptors/local-models.json', modelSetsBytes],
    ['descriptors/model-sets.json', modelSetsBytes],
    ['descriptors/runtime.json', Buffer.from(core.canonicalJson(runtimeDescriptor))],
    ['descriptors/runtime-receipt.json', Buffer.from(core.canonicalJson(runtimeReceipt))],
    ['descriptors/runtime-config-template.json', Buffer.from(core.canonicalJson(runtimeTemplate))],
    ['app/src/voice_agent_v2/faster_whisper_runner.py', Buffer.from('# production fixture STT runner\n')],
    ...runtimeFiles,
  ]);
  function bytesForDescriptor(descriptor, artifactBytes = null) {
    if (descriptor.kind === 'program') return artifactBytes;
    return assetContents.get(descriptor.id);
  }
  return { assets, assetContents, bytesForDescriptor, contents, modelSets, runtimeReceipt };
}

function entriesFor(contents, hashBytes = hash) {
  const directories = new Set();
  for (const name of contents.keys()) {
    const parts = name.split('/');
    for (let index = 1; index < parts.length; index += 1) directories.add(parts.slice(0, index).join('/'));
  }
  return [
    ...[...directories].sort((a, b) => a.split('/').length - b.split('/').length || a.localeCompare(b)).map((name) => ({ path: name, type: 'directory', mode: '0555', size: 0, sha256: null, target: null })),
    ...[...contents].sort(([left], [right]) => left.localeCompare(right)).map(([name, bytes]) => ({ path: name, type: 'file', mode: name.startsWith('bin/') || name.startsWith('runtime/') ? '0555' : '0444', size: bytes.length, sha256: hashBytes(bytes), target: null })),
  ];
}

module.exports = { aggregate, assetDescriptor, closedFixture, entriesFor, hash };
