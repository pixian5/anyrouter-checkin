#!/usr/bin/env bash
# 守护脚本：确保 mihomo 代理运行，每天 12:00 执行签到
# 首次运行立即签到，之后每天定时
# 单实例：flock(退化为 pidfile) 独占锁，避免重跑部署残留旧守护 → 同一时刻重复签到/Bark 二次推送
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PROXY_DIR="$(cd "$REPO_DIR/.." && pwd)/checkin-proxy"
PYTHON="${REPO_DIR}/venv/bin/python"
LOG_FILE="${REPO_DIR}/checkin_daemon.log"
LOCK_FILE="${REPO_DIR}/.checkin_daemon.lock"
PID_FILE="${REPO_DIR}/.checkin_daemon.pid"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"; }

# ---------- 单实例守卫 ----------
if command -v flock >/dev/null 2>&1; then
  exec 9>"$LOCK_FILE"
  if ! flock -n 9; then
    log "已有签到守护进程运行，本实例退出（避免重复签到/重复 Bark 推送）"
    exit 0
  fi
elif [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE" 2>/dev/null)" 2>/dev/null; then
  log "已有签到守护进程运行，本实例退出（避免重复签到/重复 Bark 推送）"
  exit 0
else
  echo $$ > "$PID_FILE"
fi

# 收到 SIGTERM/SIGINT 立即退出（释放锁/pidfile），让部署脚本能可靠停掉旧守护
cleanup() { [ -n "${SLEEP_PID:-}" ] && kill "$SLEEP_PID" 2>/dev/null || true; rm -f "$PID_FILE" 2>/dev/null; exit 0; }
trap cleanup SIGTERM SIGINT

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
    # 9>&-：阻止锁 fd 泄漏给子进程（子进程继承 fd 9 会让 flock 永不释放）
    ./mihomo -d . -f config.yaml > mihomo.log 2>&1 9>&- &
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
    if "$PYTHON" scripts/aicheckin.py >> "$LOG_FILE" 2>&1; then
        log "签到完成"
    else
        log "签到出错（可能部分成功）"
    fi
}

# 首次立即签到
do_checkin

# 循环：每天 12:00 执行
while true; do
    now=$(date +%s)
    target=$(date -d "tomorrow 12:00" +%s 2>/dev/null || date -d "+1 day 12:00" +%s)
    wait_secs=$((target - now))
    if [ "$wait_secs" -le 0 ]; then
        wait_secs=86400
    fi
    log "下次签到: $(date -d "@$target" '+%Y-%m-%d %H:%M:%S') (等待 ${wait_secs}s)"
    # 后台 sleep + wait（而非前台 sleep）：bash 对前台 sleep 会推迟信号处理，
    # 导致 SIGTERM 在长达 24h 的等待期内无法及时停掉旧守护；wait 可被信号中断并触发 trap
    # 9>&-：同上，长时间运行的 sleep 不能持有锁 fd
    sleep "$wait_secs" 9>&- &
    SLEEP_PID=$!
    wait "$SLEEP_PID"
    SLEEP_PID=            # 正常等到点，清空以免 cleanup 误杀已结束的 pid
    do_checkin
done
