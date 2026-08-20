#!/bin/bash
set -euo pipefail
export LC_ALL=C

if test "$#" -ne 2; then
  printf '%s\n' 'usage: node-compatibility-preflight.sh <authority.tsv> <report.tsv>' >&2
  exit 64
fi
authority=$1
report=$2
node=/build/tools/node/bin/node
library=/build/tools/node-runtime/lib/libatomic.so.1
loader=/usr/lib64/ld-2.28.so
interpreter=/lib64/ld-linux-x86-64.so.2
work=${report}.work
rm -rf "$work" "$report" /build/tools/node-command
mkdir -p "$work"
unset LD_LIBRARY_PATH LD_PRELOAD

declare -A provenance=()
declare -A expected=()
while IFS=$'\t' read -r source name value; do
  provenance[$name]=$source
  expected[$name]=$value
done <"$authority"

failures=0
record() {
  local name=$1 observed=${2:-} presence=${3:-present} status=ok evidence
  if test "$presence" = absent; then
    status=absent
    evidence=absent
  elif test "${expected[$name]:-}" != "$observed"; then
    status=mismatch
    evidence=mismatch
  else
    evidence=$observed
  fi
  test "$status" = ok || failures=$((failures + 1))
  printf '%s\t%s\t%s\t%s\n' "${provenance[$name]:-invalid}" "$name" "$evidence" "$status" >>"$report"
}

path_has_no_links() {
  local value=$1 component current=
  test "${value#/}" != "$value" || return 1
  IFS=/ read -r -a components <<<"${value#/}"
  for component in "${components[@]}"; do
    test "$component" && current="$current/$component"
    test ! -L "$current" || return 1
  done
}

field() {
  local label=$1 filename=$2
  readelf -hW "$filename" 2>/dev/null | awk -F: -v label="$label" '$1 ~ label { sub(/^ +/, "", $2); print $2; exit }'
}

interpreter_of() {
  readelf -lW "$1" 2>/dev/null | awk -F': ' '/Requesting program interpreter/ { gsub(/]/, "", $2); print $2; exit }'
}

needed_of() {
  readelf -dW "$1" 2>/dev/null | awk -F'[][]' '/\(NEEDED\)/ { print $2 }' | sort -u | awk 'BEGIN { first=1 } { if (!first) printf ","; printf "%s", $0; first=0 } END { print "" }'
}

versions_of() {
  local prefix=$1 filename=$2
  readelf -VW "$filename" 2>/dev/null | awk -v prefix="$prefix" '
    { rest=$0; pattern=prefix "_[0-9]+(\\.[0-9]+)*"; while (match(rest, pattern)) { print substr(rest, RSTART, RLENGTH); rest=substr(rest, RSTART+RLENGTH) } }
  ' | sort -Vu | awk 'BEGIN { first=1 } { if (!first) printf ","; printf "%s", $0; first=0 } END { print "" }'
}

if test -f "$node" && test -x "$node" && test -n "$(find "$node" -maxdepth 0 -type f -perm 0555 -print)" && path_has_no_links "$node"; then
  record architecture "$(field 'Class' "$node")|$(field 'Data' "$node")|$(field 'Type' "$node")|$(field 'Machine' "$node")"
  node_sha=$(sha256sum "$node"); node_sha=${node_sha%% *}
  record node-layout "regular:0555:sha256:$node_sha"
  observed_interpreter=$(interpreter_of "$node")
  resolved_interpreter=$(readlink -f "$observed_interpreter" 2>/dev/null || true)
  record interpreter "$observed_interpreter->$resolved_interpreter"
  record needed "$(needed_of "$node")"
  record required-glibc "$(versions_of GLIBC "$node")"
  record required-glibcxx "$(versions_of GLIBCXX "$node")"
  record required-cxxabi "$(versions_of CXXABI "$node")"
else
  for fact in architecture node-layout interpreter needed required-glibc required-glibcxx required-cxxabi; do record "$fact" '' absent; done
fi

if test -f "$loader" && test -x "$loader" && path_has_no_links "$loader"; then
  loader_sha=$(sha256sum "$loader"); loader_sha=${loader_sha%% *}
  record loader "sha256:$loader_sha"
else
  record loader '' absent
fi

if test -f "$library" && test ! -L "$library" && test -n "$(find "$library" -maxdepth 0 -type f -perm 0555 -print)" && path_has_no_links "$library"; then
  library_sha=$(sha256sum "$library"); library_sha=${library_sha%% *}
  record library-layout "regular:0555:sha256:$library_sha"
  record library-needed "$(needed_of "$library")"
  record library-glibc "$(versions_of GLIBC "$library")"
else
  record library-layout '' absent
  record library-needed '' absent
  record library-glibc '' absent
fi

raw_stdout=$work/raw.stdout
raw_stderr=$work/raw.stderr
set +e
"$node" --version >"$raw_stdout" 2>"$raw_stderr"
raw_exit=$?
set -e
record raw-exit "$raw_exit"
raw_size=$(find "$raw_stderr" -maxdepth 0 -type f -printf '%s' 2>/dev/null || printf 0)
raw_sha=$(sha256sum "$raw_stderr"); raw_sha=${raw_sha%% *}
if test "$raw_size" -le 1024 && grep -Eq '^/build/tools/node/bin/node: error while loading shared libraries: libatomic\.so\.1: cannot open shared object file: No such file or directory$' "$raw_stderr"; then
  record raw-stderr "missing-libatomic:sha256:$raw_sha"
else
  record raw-stderr mismatch
fi

corrected_stdout=$work/corrected.stdout
corrected_stderr=$work/corrected.stderr
set +e
"$loader" --inhibit-cache --library-path /build/tools/node-runtime/lib:/lib64:/usr/lib64 "$node" --version >"$corrected_stdout" 2>"$corrected_stderr"
corrected_exit=$?
set -e
record corrected-exit "$corrected_exit"
corrected_size=$(find "$corrected_stdout" -maxdepth 0 -type f -printf '%s' 2>/dev/null || printf 0)
corrected_sha=$(sha256sum "$corrected_stdout"); corrected_sha=${corrected_sha%% *}
corrected_text=$(<"$corrected_stdout")
if test "$corrected_size" -le 1024 && test ! -s "$corrected_stderr"; then
  record corrected-output "$corrected_text:sha256:$corrected_sha"
else
  record corrected-output mismatch
fi
if test -z "${LD_LIBRARY_PATH+x}" && test -z "${LD_PRELOAD+x}"; then
  record environment LD_LIBRARY_PATH-and-LD_PRELOAD-denied
else
  record environment mismatch
fi

if test "$failures" -eq 0; then
  cat >/build/tools/node-command <<'WRAPPER'
#!/usr/bin/bash
set -euo pipefail
unset LD_LIBRARY_PATH LD_PRELOAD
exec /usr/lib64/ld-2.28.so --inhibit-cache --library-path /build/tools/node-runtime/lib:/lib64:/usr/lib64 /build/tools/node/bin/node "$@"
WRAPPER
  chmod 0755 /build/tools/node-command
fi
rm -rf "$work"
exit 0
