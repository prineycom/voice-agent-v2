#!/bin/bash
set -euo pipefail
export HOME=/work/home XDG_CONFIG_HOME=/work/config XDG_CACHE_HOME=/work/xdg
export PIP_CONFIG_FILE=/dev/null NPM_CONFIG_USERCONFIG=/dev/null NPM_CONFIG_GLOBALCONFIG=/dev/null
export GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1
unset HTTP_PROXY HTTPS_PROXY FTP_PROXY ALL_PROXY NO_PROXY http_proxy https_proxy ftp_proxy all_proxy no_proxy
unset SSH_AUTH_SOCK GIT_ASKPASS GH_TOKEN GITHUB_TOKEN NODE_AUTH_TOKEN NPM_TOKEN
unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN GOOGLE_APPLICATION_CREDENTIALS
mkdir -p "$HOME" "$XDG_CONFIG_HOME" "$XDG_CACHE_HOME"

input() {
  local name=$1 digest
  digest=$(awk -F '\t' -v name="$name" '$2 == name { print $1 }' /build/input-map.tsv)
  test "$digest" && test -f "/inputs/$digest"
  printf '/inputs/%s' "$digest"
}
extract_node() {
  rm -rf /work/node && mkdir -p /work/node
  tar -xJf "$(input node-v26.7.0-linux-x64.tar.xz)" -C /work/node --strip-components=1
  test "$(/work/node/bin/node --version)" = v26.7.0
}
prepare_web() {
  extract_node
  rm -rf /work/web && cp -a /source/web /work/web && chmod -R u+rwX /work/web
  cd /work/web
  /work/node/bin/node /work/node/lib/node_modules/npm/bin/npm-cli.js ci --ignore-scripts --cache /npm-cache "$@"
}

case "${1:-}" in
  web-acquire)
    prepare_web --prefer-online
    exit 0
    ;;
  assemble) ;;
  *) echo 'usage: assemble-runtime.sh web-acquire|assemble' >&2; exit 64 ;;
esac

rm -rf /output/runtime /output/web /work/runtime /work/wheels /work/llama /work/cmake /work/patchelf
mkdir -p /output/runtime /output/web /work/wheels /work/llama /work/cmake /work/patchelf
prepare_web --offline
cd /work/web
VITE_APP_VERSION="$VOICE_AGENT_BUILD_ID" /work/node/bin/node /work/node/lib/node_modules/npm/bin/npm-cli.js run build:production-only
cp -a dist/. /output/web/
rm -rf /work/node /work/web /output/web/node_modules

# One exact relocatable CPython and one closed wheelhouse; no host Python/pip.
tar -xzf "$(input cpython-3.12.13%2B20260807-x86_64-unknown-linux-gnu-install_only.tar.gz)" -C /output/runtime
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
tar -xzf "$(input cmake-4.1.1-linux-x86_64.tar.gz)" -C /work/cmake --strip-components=1
tar -xzf "$(input patchelf-0.18.0-x86_64.tar.gz)" -C /work/patchelf
PATCHELF=$(find /work/patchelf -type f -name patchelf -print -quit)
tar -xzf "$(input 689e227db485c6b33d061555e74034c93a867649.tar.gz)" -C /work/llama --strip-components=1
/work/cmake/bin/cmake -S /work/llama -B /work/llama-build -G 'Unix Makefiles' \
  -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=/work/llama-install \
  -DCMAKE_INSTALL_RPATH='$ORIGIN:$ORIGIN/../lib:$ORIGIN/../../lib' \
  -DGGML_CUDA=ON -DGGML_NATIVE=OFF -DGGML_LTO=ON -DLLAMA_BUILD_SERVER=ON \
  -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF -DLLAMA_BUILD_TOOLS=OFF \
  -DLLAMA_CURL=OFF -DLLAMA_BUILD_COMMON=ON
/work/cmake/bin/cmake --build /work/llama-build --target llama-server -j12
/work/cmake/bin/cmake --install /work/llama-build --strip
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
import hashlib, os, pathlib, shutil, subprocess, sys
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
    # NVIDIA wheel libraries are providers, not importable Python extensions.
    # Move them into the one common closure so CUDA/cuDNN/cuBLAS are not
    # duplicated in the application archive and every consumer shares one byte.
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
