#!/usr/bin/env bash
# Prints the CHANGELOG.md section for a version (without its heading). Fails if it's missing or empty.
# Usage: changelog-section.sh 0.2.0
set -euo pipefail
version="${1#v}"
section=$(awk -v v="$version" '
  /^## \[/ { if (found) exit; if (index($0, "## [" v "]") == 1) { found = 1; next } }
  found { print }
' "${CHANGELOG:-CHANGELOG.md}" | sed -e '/./,$!d' | sed -e :a -e '/^\n*$/{$d;N;ba' -e '}')
if [ -z "${section//[[:space:]]/}" ]; then
  echo "CHANGELOG.md has no notes for version $version" >&2
  exit 1
fi
printf '%s\n' "$section"
