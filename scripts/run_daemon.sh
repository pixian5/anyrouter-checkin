#!/usr/bin/env bash
# 守护脚本：确保 mihomo 代理运行，每天 09:30 执行签到
# 首次运行立即签到，之后每天定时
set -euo pipefail

REPO_DIR="/root/workspace/anyrouter-checkin"
PROXY_DIR="/root/workspace/checkin-proxy"
PYTHON="${REPO_DIR}/venv/bin/python"
LOG_FILE="${REPO_DIR}/checkin_daemon.log"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"; }

ensure_proxy() {
    if ss -tlnp 2>/dev/null | grep -q ':7890' && curl -fsS -x http://127.0.0.1:7890 --max-time 15 https://www.google.com/generate_204 -o /dev/null 2>/dev/null; then
        return 0
    fi
    if ss -tlnp 2>/dev/null | grep -q ':7890'; then
        log "mihomo 端口在但节点不可达，重启..."
        pkill -x mihomo 2>/dev/null || true
        sleep 2
    else
        log "mihomo 未运行，启动..."
    fi
    cd "$PROXY_DIR"
    ./mihomo -d . -f config.yaml > mihomo.log 2>&1 &
    sleep 5
    if ss -tlnp 2>/dev/null | grep -q ':7890' && curl -fsS -x http://127.0.0.1:7890 --max-time 15 https://www.google.com/generate_204 -o /dev/null 2>/dev/null; then
        log "mihomo 启动成功"
    else
        log "mihomo 启动失败!"
        return 1
    fi
}

do_checkin() {
    log "开始签到..."
    ensure_proxy || { log "代理不可用，跳过签到"; return 1; }
    cd "$REPO_DIR"
    if "$PYTHON" scripts/s390x_checkin.py >> "$LOG_FILE" 2>&1; then
        log "签到完成"
    else
        log "签到出错（可能部分成功）"
    fi
}

# 首次立即签到
do_checkin

# 循环：每天 09:30 执行
while true; do
    now=$(date +%s)
    target=$(date -d "tomorrow 09:30" +%s 2>/dev/null || date -d "+1 day 09:30" +%s)
    wait_secs=$((target - now))
    if [ "$wait_secs" -le 0 ]; then
        wait_secs=86400
    fi
    log "下次签到: $(date -d "@$target" '+%Y-%m-%d %H:%M:%S') (等待 ${wait_secs}s)"
    sleep "$wait_secs"
    do_checkin
done
