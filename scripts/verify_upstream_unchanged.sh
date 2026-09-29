#!/usr/bin/env bash
set -euo pipefail

upstream_ref="${1:-upstream/main}"
if ! git rev-parse --verify --quiet "${upstream_ref}^{tree}" >/dev/null; then
	printf '上游引用不存在: %s\n' "$upstream_ref" >&2
	exit 2
fi

temporary_index="$(mktemp)"
normalized_readme="$(mktemp)"
expected_readme="$(mktemp)"
trap 'rm -f "$temporary_index" "$normalized_readme" "$expected_readme"' EXIT
rm -f "$temporary_index"
GIT_INDEX_FILE="$temporary_index" git read-tree "$upstream_ref"
GIT_INDEX_FILE="$temporary_index" git update-index --refresh >/dev/null 2>&1 || true

# README 只允许项目全局要求的两个进度区块，比较其余内容与上游原版。
sed '/^## 当前开发进度$/,/^## 功能特性$/ { /^## 功能特性$/!d; }' README.md > "$normalized_readme"
git show "${upstream_ref}:README.md" > "$expected_readme"
if ! cmp -s "$normalized_readme" "$expected_readme"; then
	printf 'README.md 除进度区块外也偏离上游原版:\n' >&2
	diff -u "$expected_readme" "$normalized_readme" >&2 || true
	exit 1
fi
if [ -n "$(git diff --summary "$upstream_ref" -- README.md)" ]; then
	printf 'README.md 文件模式偏离上游原版。\n' >&2
	exit 1
fi

# 从临时上游 index 中排除 README；其余上游路径必须保持内容和文件模式原样。
GIT_INDEX_FILE="$temporary_index" git update-index --remove -- README.md
if GIT_INDEX_FILE="$temporary_index" git diff-files --quiet --; then
	printf '除 README 必需的进度区块外，%s 中的上游文件均保持原样。\n' "$upstream_ref"
	exit 0
fi

printf '上游文件存在非预期修改、删除或文件模式变化:\n' >&2
GIT_INDEX_FILE="$temporary_index" git diff-files --name-status -- >&2
exit 1
