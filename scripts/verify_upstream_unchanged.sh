#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
printf '提示：此兼容命令已改为检查上游历史是否已合并。\n'
exec "$script_dir/verify_upstream_merged.sh" "$@"
