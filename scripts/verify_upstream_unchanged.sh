#!/usr/bin/env bash
set -euo pipefail

upstream_ref="${1:-upstream/main}"

if ! git rev-parse --verify --quiet "${upstream_ref}^{tree}" >/dev/null; then
	printf 'Upstream ref not found: %s\n' "$upstream_ref" >&2
	exit 2
fi

temporary_index="$(mktemp)"
rm -f "$temporary_index"
trap 'rm -f "$temporary_index"' EXIT

GIT_INDEX_FILE="$temporary_index" git read-tree "$upstream_ref"
# 临时 index 没有工作树 stat 缓存；先刷新，相同文件才不会被全部标成修改。
GIT_INDEX_FILE="$temporary_index" git update-index --refresh >/dev/null 2>&1 || true

if GIT_INDEX_FILE="$temporary_index" git diff-files --quiet --; then
	printf 'All files from %s are unchanged.\n' "$upstream_ref"
	exit 0
fi

printf 'Upstream files changed in the working tree:\n' >&2
GIT_INDEX_FILE="$temporary_index" git diff-files --name-status -- >&2
exit 1
