#!/usr/bin/bash
set -euo pipefail
export HOME=/work/home XDG_CONFIG_HOME=/work/config XDG_CACHE_HOME=/work/xdg
export PIP_CONFIG_FILE=/dev/null NPM_CONFIG_USERCONFIG=/dev/null NPM_CONFIG_GLOBALCONFIG=/dev/null
export GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1
unset HTTP_PROXY HTTPS_PROXY FTP_PROXY ALL_PROXY NO_PROXY http_proxy https_proxy ftp_proxy all_proxy no_proxy
unset SSH_AUTH_SOCK GIT_ASKPASS GH_TOKEN GITHUB_TOKEN NODE_AUTH_TOKEN NPM_TOKEN
unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN GOOGLE_APPLICATION_CREDENTIALS
/usr/bin/mkdir -p "$HOME" "$XDG_CONFIG_HOME" "$XDG_CACHE_HOME"

input() {
  local name=$1 digest
  digest=$(awk -F '\t' -v name="$name" '$2 == name { print $1 }' /build/input-map.tsv)
  test "$digest" && test -f "/inputs/$digest"
  printf '/inputs/%s' "$digest"
}
builder_tool_preflight() {
  /usr/bin/rm -rf /build/tool-bin /build/tools /build/web /build/tool-report.tsv /build/node-compatibility-report.tsv
  /usr/bin/mkdir -p /build/tool-bin /build/tools
  /tool-preflight /build/tool-authority.tsv /build/tool-report.tsv builder
  local phase provenance name tool_path kind command argv expected_exit maximum prefix version expected_sha owner_uid mode tool_root parent parent_version detail
  local report_provenance report_name report_path evidence status failures=0
  declare -A builder_status=()
  while IFS=$'\t' read -r report_provenance report_name report_path evidence status; do builder_status[$report_name]=$status; done </build/tool-report.tsv
  while IFS=$'\t' read -r phase provenance name tool_path kind command argv expected_exit maximum prefix version expected_sha owner_uid mode tool_root parent parent_version detail; do
    test "$phase" = builder || continue
    if test "${builder_status[$name]:-absent}" = ok; then /usr/bin/ln -s "$tool_path" "/build/tool-bin/$name"; else failures=$((failures + 1)); fi
  done </build/tool-authority.tsv
  if test "$failures" -ne 0; then
    while IFS=$'\t' read -r phase provenance name tool_path kind command argv expected_exit maximum prefix version expected_sha owner_uid mode tool_root parent parent_version detail; do
      [[ "$phase" = content || "$phase" = web ]] || continue
      printf '%s\t%s\t%s\t%s\tblocked\n' "$provenance" "$name" "$tool_path" blocked >>/build/tool-report.tsv
    done </build/tool-authority.tsv
    return 0
  fi

  export PATH=/build/tool-bin
  local node_archive libatomic_archive python_archive cmake_archive patchelf_archive extraction_failed=0
  mkdir -p /build/tools/node /build/tools/node-runtime/lib /build/tools/node-runtime/unpacked /build/tools/cmake
  node_archive=$(input node-v26.7.0-linux-x64.tar.gz)
  libatomic_archive=$(input libatomic-8.5.0-26.el8_10.x86_64.rpm)
  python_archive=$(input cpython-3.12.13%2B20260807-x86_64-unknown-linux-gnu-install_only.tar.gz)
  cmake_archive=$(input cmake-4.1.1-linux-x86_64.tar.gz)
  patchelf_archive=$(input patchelf-0.18.0-x86_64.tar.gz)
  tar -xzf "$node_archive" -C /build/tools/node --strip-components=1 || extraction_failed=1
  cp "$libatomic_archive" /build/tools/node-runtime/libatomic.rpm || extraction_failed=1
  if test "$extraction_failed" -eq 0; then
    rpm --checksig --verbose /build/tools/node-runtime/libatomic.rpm > /build/tools/node-runtime/rpm-signature.txt || extraction_failed=1
    grep -F 'V4 RSA/SHA256 Signature, key ID 6d745a60: OK' /build/tools/node-runtime/rpm-signature.txt >/dev/null || extraction_failed=1
    (cd /build/tools/node-runtime && rpm2archive -n libatomic.rpm) || extraction_failed=1
    tar -xf /build/tools/node-runtime/libatomic.rpm.tar -C /build/tools/node-runtime/unpacked || extraction_failed=1
    if test -f /build/tools/node-runtime/unpacked/usr/lib64/libatomic.so.1.2.0; then
      cp /build/tools/node-runtime/unpacked/usr/lib64/libatomic.so.1.2.0 /build/tools/node-runtime/lib/libatomic.so.1
    else
      extraction_failed=1
    fi
  fi
  rm -rf /build/tools/node-runtime/unpacked /build/tools/node-runtime/libatomic.rpm /build/tools/node-runtime/libatomic.rpm.tar /build/tools/node-runtime/rpm-signature.txt
  test -f /build/tools/node/bin/node && chmod 0555 /build/tools/node/bin/node
  test -f /build/tools/node-runtime/lib/libatomic.so.1 && chmod 0555 /build/tools/node-runtime/lib/libatomic.so.1
  /node-compatibility-preflight /build/node-compatibility-authority.tsv /build/node-compatibility-report.tsv
  tar -xzf "$python_archive" -C /build/tools || extraction_failed=1
  tar -xzf "$cmake_archive" -C /build/tools/cmake --strip-components=1 || extraction_failed=1
  mkdir -p /build/tools/patchelf-unpacked /build/tools/patchelf
  tar -xzf "$patchelf_archive" -C /build/tools/patchelf-unpacked || extraction_failed=1
  local located
  located=$(find /build/tools/patchelf-unpacked -type f -name patchelf -print -quit)
  if test "$located"; then cp "$located" /build/tools/patchelf/patchelf; else extraction_failed=1; fi

  if test "$extraction_failed" -ne 0; then
    while IFS=$'\t' read -r phase provenance name tool_path kind command argv expected_exit maximum prefix version expected_sha owner_uid mode tool_root parent parent_version detail; do
      test "$phase" = content || continue
      printf '%s\t%s\t%s\tabsent\tabsent\n' "$provenance" "$name" "$tool_path" >>/build/tool-report.tsv
    done </build/tool-authority.tsv
  else
    /tool-preflight /build/tool-authority.tsv /build/tool-report.tsv content
  fi
  local content_failures=0
  declare -A closure_status=()
  while IFS=$'\t' read -r report_provenance report_name report_path evidence status; do closure_status[$report_name]=$status; done </build/tool-report.tsv
  while IFS=$'\t' read -r phase provenance name tool_path kind command argv expected_exit maximum prefix version expected_sha owner_uid mode tool_root parent parent_version detail; do
    test "$phase" = content || continue
    test "${closure_status[$name]:-absent}" = ok || content_failures=$((content_failures + 1))
  done </build/tool-authority.tsv
  if test "$content_failures" -ne 0; then
    while IFS=$'\t' read -r phase provenance name tool_path kind command argv expected_exit maximum prefix version expected_sha owner_uid mode tool_root parent parent_version detail; do
      test "$phase" = web || continue
      printf '%s\t%s\t%s\tblocked\tblocked\n' "$provenance" "$name" "$tool_path" >>/build/tool-report.tsv
    done </build/tool-authority.tsv
  fi
}

acquire_web_cache() {
  export PATH=/build/tool-bin
  cat >/work/acquire-web-cache.cjs <<'NODE'
'use strict';
const fs = require('node:fs');
const pacote = require('/build/tools/node/lib/node_modules/npm/node_modules/pacote');
const lock = JSON.parse(fs.readFileSync('/source/web/package-lock.json'));
const accepted = new Map();
for (const item of Object.values(lock.packages || {})) {
  if (!item || !item.resolved) continue;
  const url = new URL(item.resolved);
  if (url.protocol !== 'https:' || url.hostname !== 'registry.npmjs.org' || url.username || url.password || url.port || url.search || url.hash || !/^sha512-[A-Za-z0-9+/]+={0,2}$/.test(item.integrity || '')) throw new Error('npm lock authority is not closed');
  const prior = accepted.get(item.resolved);
  if (prior && prior !== item.integrity) throw new Error('npm lock locator is ambiguous');
  accepted.set(item.resolved, item.integrity);
}
(async () => {
  for (const [resolved, integrity] of [...accepted].sort(([a], [b]) => a.localeCompare(b))) {
    await pacote.tarball.stream(resolved, (stream) => new Promise((resolve, reject) => {
      stream.on('data', () => {}); stream.on('end', resolve); stream.on('error', reject);
    }), { cache: '/npm-cache', integrity, preferOnline: true });
  }
})().catch(() => { process.exitCode = 1; });
NODE
  /build/tools/node/bin/node /work/acquire-web-cache.cjs
}

prepare_web_tools() {
  export PATH=/build/tool-bin
  rm -rf /build/web
  cp -a /source/web /build/web
  chmod -R u+rwX /build/web
  cd /build/web
  /build/tools/node/bin/node /build/tools/node/lib/node_modules/npm/bin/npm-cli.js ci --ignore-scripts --offline --cache /npm-cache
  /build/tools/node/bin/node <<'NODE' >>/build/tool-report.tsv
'use strict';
const fs = require('node:fs');
for (const line of fs.readFileSync('/build/tool-authority.tsv', 'utf8').trimEnd().split('\n')) {
  const fields = line.split('\t');
  const [phase, provenance, name, packageName] = fields; const expected = fields[10];
  if (phase !== 'web') continue;
  let observed = 'absent'; let status = 'absent';
  try {
    observed = JSON.parse(fs.readFileSync(`/build/web/node_modules/${packageName}/package.json`)).version;
    status = observed === expected ? 'ok' : 'mismatch';
  } catch {}
  const evidence = status === 'ok' ? `lock:${observed}` : status;
  process.stdout.write(`${provenance}\t${name}\t${packageName}\t${evidence}\t${status}\n`);
}
NODE
}

case "${1:-}" in
  tool-preflight)
    builder_tool_preflight
    exit 0
    ;;
  web-acquire)
    acquire_web_cache
    exit 0
    ;;
  web-prepare)
    prepare_web_tools
    exit 0
    ;;
  assemble) ;;
  *) echo 'usage: assemble-runtime.sh tool-preflight|web-acquire|web-prepare|assemble' >&2; exit 64 ;;
esac

export PATH=/build/tool-bin
rm -rf /output/runtime /output/web /work/runtime /work/wheels /work/llama
mkdir -p /output/runtime /output/web /work/wheels /work/llama
cp -a /build/web /work/web
cd /work/web
VITE_APP_VERSION="$VOICE_AGENT_BUILD_ID" /build/tools/node/bin/node /build/tools/node/lib/node_modules/npm/bin/npm-cli.js run build:production-only
cp -a dist/. /output/web/
rm -rf /work/web /output/web/node_modules

# One exact relocatable CPython and one closed wheelhouse; no host Python/pip.
cp -a /build/tools/python /output/runtime/python
PY=/output/runtime/python/bin/python3
"$PY" -VV | grep -F '3.12.13'
while IFS=$'\t' read -r digest filename; do
  case "$filename" in *.whl) cp "/inputs/$digest" "/work/wheels/$filename";; esac
done < /build/input-map.tsv
for lock in gateway.lock stt.lock tts.lock; do
  "$PY" -I -m pip install --disable-pip-version-check --no-input --no-index --only-binary=:all: --require-hashes --find-links=/work/wheels -r "/source/requirements-release/$lock"
done
"$PY" -I -m pip check
rm -rf /output/runtime/python/lib/python3.12/site-packages/pip /output/runtime/python/lib/python3.12/site-packages/pip-*.dist-info
find /output/runtime/python -type d \( -name __pycache__ -o -name tests -o -name test \) -prune -exec rm -rf {} +
find /output/runtime/python -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete

# Exact static LiveKit binary.
mkdir -p /output/runtime/livekit/bin /work/livekit
tar -xzf "$(input livekit_1.13.5_linux_amd64.tar.gz)" -C /work/livekit
LIVEKIT=$(find /work/livekit -type f -name livekit-server -print -quit)
test "$LIVEKIT" && test "$(sha256sum "$LIVEKIT" | cut -d' ' -f1)" = 51a1bbe04439b33d6d7a6d6d83fdefad9b938162c341f0f07f75af03e456b49a
cp "$LIVEKIT" /output/runtime/livekit/bin/livekit-server

# Exact CMake/patchelf build tools and exact llama.cpp source; tools never ship.
PATCHELF=/build/tools/patchelf/patchelf
tar -xzf "$(input 689e227db485c6b33d061555e74034c93a867649.tar.gz)" -C /work/llama --strip-components=1
/build/tools/cmake/bin/cmake -S /work/llama -B /work/llama-build -G 'Unix Makefiles' \
  -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=/work/llama-install \
  -DCMAKE_C_COMPILER=/build/tool-bin/gcc -DCMAKE_CXX_COMPILER=/build/tool-bin/g++ \
  -DCMAKE_CUDA_COMPILER=/build/tool-bin/nvcc -DCMAKE_MAKE_PROGRAM=/build/tool-bin/make \
  -DCMAKE_DISABLE_FIND_PACKAGE_Git=TRUE \
  -DCMAKE_INSTALL_RPATH='$ORIGIN:$ORIGIN/../lib:$ORIGIN/../../lib' \
  -DGGML_CUDA=ON -DGGML_NATIVE=OFF -DGGML_LTO=ON -DGGML_CCACHE=OFF -DGGML_CUDA_NCCL=OFF \
  -DLLAMA_BUILD_SERVER=ON -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF -DLLAMA_BUILD_TOOLS=OFF \
  -DLLAMA_BUILD_UI=OFF -DLLAMA_USE_PREBUILT_UI=OFF -DLLAMA_LLGUIDANCE=OFF -DLLAMA_OPENSSL=OFF \
  -DLLAMA_CURL=OFF -DLLAMA_BUILD_COMMON=ON
/build/tools/cmake/bin/cmake --build /work/llama-build --target llama-server -j12
/build/tools/cmake/bin/cmake --install /work/llama-build --strip
mkdir -p /output/runtime/llama/bin /output/runtime/llama/lib /output/runtime/lib
cp /work/llama-install/bin/llama-server /output/runtime/llama/bin/
while IFS= read -r -d '' library; do
  cp -L "$library" "/output/runtime/llama/lib/$(basename "$library")"
done < <(find /work/llama-install \( -type f -o -type l \) \( -name '*.so' -o -name '*.so.*' \) -print0)
# CUDA driver remains host-provided; cudart is copied from the pinned builder OCI.
CUDART=$(readlink -f /usr/local/cuda/targets/x86_64-linux/lib/libcudart.so.12)
test -f "$CUDART" && cp "$CUDART" /output/runtime/lib/libcudart.so.12

# Resolve every DT_NEEDED recursively. Only the documented glibc/kernel/libcuda boundary may resolve on the host.
cat >/work/close-elf.py <<'PY'
import hashlib, os, pathlib, shutil, subprocess
root=pathlib.Path('/output/runtime'); lib=root/'lib'
allowed={'linux-vdso.so.1','ld-linux-x86-64.so.2','libc.so.6','libm.so.6','libpthread.so.0','libdl.so.2','librt.so.1','libutil.so.1','libresolv.so.2','libcuda.so.1'}
def elf(p):
 try: return p.is_file() and p.read_bytes()[:4] == b'\x7fELF'
 except OSError: return False
def needed(p):
 text=subprocess.check_output(['readelf','-dW',str(p)],text=True)
 return [line.split('[',1)[1].split(']',1)[0] for line in text.splitlines() if '(NEEDED)' in line]
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def candidates(name):
 inside=sorted(p for p in root.rglob(name) if p.is_file())
 system=[]
 for base in ('/usr/local/cuda/targets/x86_64-linux/lib','/lib64','/usr/lib64'):
  p=pathlib.Path(base)/name
  if p.exists(): system.append(p.resolve())
 return inside+system
changed=True
while changed:
 changed=False
 for binary in sorted(p for p in root.rglob('*') if elf(p)):
  for name in needed(binary):
   if name in allowed or (lib/name).exists(): continue
   values=candidates(name)
   if not values: raise SystemExit(f'unresolved DT_NEEDED: {name}')
   selected=values[0]; target=lib/name
   if target.exists() and sha(target)!=sha(selected): raise SystemExit(f'colliding DT_NEEDED: {name}')
   if not target.exists():
    if '/site-packages/nvidia/' in selected.as_posix() or '/runtime/llama/lib/' in selected.as_posix(): shutil.move(selected,target)
    else: shutil.copyfile(selected,target)
    changed=True
PY
"$PY" -I /work/close-elf.py

# Materialize every internal symlink as a regular file before final hashing.
while IFS= read -r -d '' link; do
  target=$(readlink -f "$link")
  case "$target" in /output/runtime/*) ;; *) echo 'runtime symlink escaped closure' >&2; exit 1;; esac
  temporary="${link}.materialized"
  cp --reflink=auto "$target" "$temporary" && rm "$link" && mv "$temporary" "$link"
done < <(find /output/runtime -type l -print0)

# Every dynamic object receives only a relative RUNPATH to itself and the closed common lib directory.
while IFS= read -r -d '' file; do
  if head -c4 "$file" | grep -q $'\x7fELF' && readelf -dW "$file" 2>/dev/null | grep -q 'Dynamic section'; then
    relative=$("$PY" -I -c 'import os,sys; print(os.path.relpath("/output/runtime/lib", os.path.dirname(sys.argv[1])))' "$file")
    "$PATCHELF" --set-rpath "\$ORIGIN:\$ORIGIN/$relative" "$file"
  fi
done < <(find /output/runtime -type f -print0)
"$PY" -I /work/close-elf.py

# Deterministic link-free modes/timestamps; Node and build tools are excluded by construction.
find /output/runtime /output/web -type d -exec chmod 0555 {} +
find /output/runtime /output/web -type f -exec chmod 0444 {} +
chmod 0555 /output/runtime/python/bin/python3 /output/runtime/livekit/bin/livekit-server /output/runtime/llama/bin/llama-server
find /output/runtime /output/web -exec touch -h -d "@$SOURCE_DATE_EPOCH" {} +
test -z "$(find /output/runtime /output/web -type l -print -quit)"
test ! -e /output/runtime/node
test -f /output/web/index.html
