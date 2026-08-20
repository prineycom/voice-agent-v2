#!/bin/bash
set -euo pipefail
export LC_ALL=C

if test "$#" -ne 3; then
  printf '%s\n' 'usage: tool-preflight.sh <authority.tsv> <report.tsv> <phase>' >&2
  exit 64
fi
authority=$1
report=$2
selected_phase=$3
case "$selected_phase" in builder|content) ;; *) exit 64;; esac

declare -A tool_status=()
declare -A tool_version=()

path_has_no_links() {
  local value=$1 component current=
  test "${value#/}" != "$value" || return 1
  IFS=/ read -r -a components <<<"${value#/}"
  for component in "${components[@]}"; do
    test "$component" && current="$current/$component"
    test ! -L "$current" || return 1
  done
}

probe_tool() {
  local command=$1 argv_spec=$2 expected_exit=$3 maximum=$4 prefix=$5 version=$6 output exit_file observed probe_exit output_size output_sha
  output=$7
  exit_file=$8
  IFS='|' read -r -a argv <<<"$argv_spec"
  /usr/bin/rm -f "$output" "$exit_file"
  set +e
  { "$command" "${argv[@]}" 2>&1; printf '%s' "$?" >"$exit_file"; } | /usr/bin/head -c "$((maximum + 1))" >"$output"
  set -e
  test -f "$exit_file" || return 1
  probe_exit=$(<"$exit_file")
  output_size=$(/usr/bin/find "$output" -maxdepth 0 -type f -printf '%s')
  test "$probe_exit" = "$expected_exit" && test "$output_size" -le "$maximum" || return 1
  observed=$(<"$output")
  [[ "$observed" == "$prefix"* && "$observed" == *"$version"* ]] || return 1
  output_sha=$(/usr/bin/sha256sum "$output"); output_sha=${output_sha%% *}
  printf 'probe:%s:sha256:%s' "$version" "$output_sha"
}

while IFS=$'\t' read -r phase provenance name tool_path kind command argv_spec expected_exit maximum prefix version expected_sha owner_uid mode tool_root parent parent_version detail; do
  test "$phase" = "$selected_phase" || continue
  status=ok
  evidence=invalid
  if test "$kind" = probe; then
    if ! path_has_no_links "$command" || ! path_has_no_links "$tool_path" || test ! -f "$command" || test ! -x "$command" || test -L "$command" \
      || test ! -e "$tool_path" || test -L "$tool_path"; then
      status=absent
      evidence=absent
    elif test "$owner_uid" != - && test -z "$(/usr/bin/find "$command" -maxdepth 0 -type f -uid "$owner_uid" -perm /111 -print)"; then
      status=mismatch
    elif test "$parent" != - && { test "${tool_status[$parent]:-}" != ok || test "${tool_version[$parent]:-}" != "$parent_version"; }; then
      status=mismatch
    else
      work=${report}.work
      /usr/bin/mkdir -p "$work"
      if receipt=$(probe_tool "$command" "$argv_spec" "$expected_exit" "$maximum" "$prefix" "$version" "$work/$name.output" "$work/$name.exit"); then
        evidence=$receipt
      else
        status=mismatch
      fi
    fi
  elif test "$kind" = custody; then
    if ! path_has_no_links "$tool_path" || [[ "$tool_path" != "$tool_root/"* ]] || test ! -f "$tool_path" || test ! -x "$tool_path" || test -L "$tool_path"; then
      status=absent
      evidence=absent
    elif test -z "$(/usr/bin/find "$tool_path" -maxdepth 0 -type f -uid "$owner_uid" -perm "$mode" -print)" \
      || test "${tool_status[$parent]:-}" != ok || test "${tool_version[$parent]:-}" != "$parent_version"; then
      status=mismatch
    else
      observed_sha=$(/usr/bin/sha256sum "$tool_path"); observed_sha=${observed_sha%% *}
      if test "$observed_sha" = "$expected_sha"; then
        evidence="custody:sha256:$observed_sha:parent:$parent@$parent_version"
      else
        status=mismatch
      fi
    fi
  else
    status=mismatch
  fi
  tool_status[$name]=$status
  tool_version[$name]=$version
  printf '%s\t%s\t%s\t%s\t%s\n' "$provenance" "$name" "$tool_path" "$evidence" "$status" >>"$report"
done <"$authority"

/usr/bin/rm -rf "${report}.work"
