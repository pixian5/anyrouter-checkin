#!/usr/bin/env bash
set -euo pipefail

upstream_ref="${1:-upstream/main}"
if ! git rev-parse --verify --quiet "${upstream_ref}^{commit}" >/dev/null; then
	printf '上游引用不存在: %s\n' "$upstream_ref" >&2
	exit 2
fi

if ! git merge-base --is-ancestor "$upstream_ref" HEAD; then
	printf '当前分支尚未合并上游提交: %s\n' "$upstream_ref" >&2
	exit 1
fi

if ! git diff --check "${upstream_ref}...HEAD"; then
	printf '上游合并后的差异存在空白错误。\n' >&2
	exit 1
fi

printf '当前分支已包含上游提交 %s，差异空白检查通过。\n' "$upstream_ref"
