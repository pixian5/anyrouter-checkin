#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -gt 1 ]; then
	printf '用法: %s [项目根目录]\n' "$0" >&2
	exit 2
fi
repo_root="${1:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)}"
repo_root="$(cd -- "$repo_root" && pwd -P)"
if ! git -C "$repo_root" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
	printf '不是 Git 工作树: %s\n' "$repo_root" >&2
	exit 1
fi

exclude_file="$(git -C "$repo_root" rev-parse --path-format=absolute --git-path info/exclude)"
mkdir -p "$(dirname -- "$exclude_file")"
touch "$exclude_file"
begin_marker='# BEGIN aicheckin deployment-only excludes'
end_marker='# END aicheckin deployment-only excludes'
if grep -Fqx "$begin_marker" "$exclude_file"; then
	if grep -Fqx "$end_marker" "$exclude_file"; then
		printf '本地运行产物忽略规则已存在: %s\n' "$exclude_file"
		exit 0
	fi
	printf '本地忽略规则块不完整: %s\n' "$exclude_file" >&2
	exit 1
fi

cat >> "$exclude_file" <<'RULES'
# BEGIN aicheckin deployment-only excludes
/venv/
/checkin_daemon.log
/checkin_daemon.log.*
/.checkin_daemon.lock
/.checkin_daemon.pid
/balance_hash.txt
/daily_checkin_state.json
/checkin_history.sqlite3
/.browser_profiles/
/checkin_screenshots/
/deploy.log
/scripts/aicheckin_test_agentrouter.py
/scripts/aicheckin_test_anyrouter.py
# END aicheckin deployment-only excludes
RULES
printf '已写入本地运行产物忽略规则: %s\n' "$exclude_file"
