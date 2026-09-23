#!/bin/bash
# 服务器端 git 轮询自动部署（不依赖 GitHub Actions runner）
# 用法: 系统定时器每分钟调用；有新 commit 则拉取并重启签到服务。
set -u
REPO_DIR=/opt/anyrouter-checkin
LOG=/opt/anyrouter-checkin/deploy.log

cd "$REPO_DIR" || { echo "$(date '+%F %T') repo-missing" >>"$LOG"; exit 0; }

# 远端可达性检查，失败静默跳过
git fetch origin --quiet --prune 2>/dev/null || exit 0

LOCAL=$(git rev-parse HEAD 2>/dev/null || echo none)
REMOTE=$(git rev-parse origin/main 2>/dev/null || echo $LOCAL)

if [ "$LOCAL" = "$REMOTE" ]; then
  exit 0
fi

git reset --hard "$REMOTE" >/dev/null 2>&1
chown -R "${SUDO_USER:-linux1}:${SUDO_USER:-linux1}" "$REPO_DIR" 2>/dev/null || true
./venv/bin/pip install -q "httpx[http2]" 2>/dev/null || true
# 重启签到服务（oneshot 会执行一次签到）；不再运行 poll 自身
sudo systemctl restart anyrouter-checkin.service

echo "$(date '+%F %T') deployed $(git rev-parse --short HEAD)" >>"$LOG"
exit 0