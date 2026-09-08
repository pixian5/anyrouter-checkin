#!/bin/bash
# anyrouter-checkin 服务器一键部署脚本 (s390x / Ubuntu)
#
# 作用：在一台全新服务器上完整复现部署：
#   1. 安装基础依赖 (git / nodejs / python3-venv)
#   2. 克隆仓库到 /opt/anyrouter-checkin
#   3. 创建 venv 并安装 httpx[http2]
#   4. 写入 systemd 服务 + 定时器（每日签到 + 每5分钟自动部署轮询）
#   5. 启用定时器
#
# 用法（在服务器上，用有 sudo 的用户执行）：
#   bash <(curl -sL <该文件raw链接>)          # 或
#   sudo bash scripts/install_server.sh [仓库URL]
#
# 注意：
#   - 默认以当前 Linux 用户运行系统服务（签到/部署均以该用户身份）
#   - 部署完成后必须另放一份真实 .env 到 $APP_DIR/.env（含密码/密钥，chmod 600），否则签到会失败
#   - 首次执行后，服务器会通过 deploy_poll 每5分钟自动拉取 GitHub 新代码并重启
set -euo pipefail

REPO_URL="${1:-https://github.com/pixian5/anyrouter-checkin.git}"
BRANCH="main"
APP_DIR="${APP_DIR:-/opt/anyrouter-checkin}"
RUN_USER="${SUDO_USER:-$(whoami)}"

# systemd 单位名称（与现有部署保持一致）
SERVICE_NAME="anyrouter-checkin"
TIMER_NAME="anyrouter-checkin.timer"
DEPLOY_SERVICE="anyrouter-deploy.service"
DEPLOY_TIMER="anyrouter-deploy.timer"

log() { echo -e "[install] $(date '+%F %T') $*"; }

# 需要 root 才能写 /etc/systemd/system 与 /opt
if [[ "$(id -u)" -ne 0 ]]; then
  echo "请用 root 或 sudo 运行本脚本：" 
  echo "  sudo bash $0 ${1:-\$REPO_URL}"
  exit 1
fi

# ---------- 1. 基础依赖 ----------
log "安装基础依赖 (git/nodejs/python3-venv)..."
export DEBIAN_FRONTEND=noninteractive
if command -v apt-get >/dev/null 2>&1; then
  apt-get update -y
  apt-get install -y git nodejs npm python3 python3-venv python3-pip curl
else
  echo "[install] 未检测到 apt-get，请手动安装 git/nodejs/python3-venv"
  exit 1
fi

# ---------- 2. 克隆仓库 ----------
if [[ -d "$APP_DIR/.git" ]]; then
  log "仓库已存在，跳过克隆 ($APP_DIR)"
else
  log "克隆仓库 $REPO_URL -> $APP_DIR ..."
  install -d -o "$RUN_USER" -g "$RUN_USER" "$APP_DIR"
  git clone --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
  chown -R "$RUN_USER:$RUN_USER" "$APP_DIR"
fi
cd "$APP_DIR"

# ---------- 3. venv + 依赖 ----------
if [[ ! -x "$APP_DIR/venv/bin/python" ]]; then
  log "创建虚拟环境并安装 httpx..."
  python3 -m venv "$APP_DIR/venv"
fi
"$APP_DIR/venv/bin/pip" install --upgrade pip -q
"$APP_DIR/venv/bin/pip" install -q "httpx[http2]"
chown -R "$RUN_USER:$RUN_USER" "$APP_DIR"

# ---------- 4. systemd 单位文件 ----------
log "写入 systemd 单位文件..."
cat > "/etc/systemd/system/${SERVICE_NAME}.service" <<EOF
[Unit]
Description=AnyRouter/AgentRouter 纯HTTP签到
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=$RUN_USER
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/venv/bin/python $APP_DIR/scripts/s390x_checkin.py

[Install]
WantedBy=multi-user.target
EOF

cat > "/etc/systemd/system/${TIMER_NAME}" <<EOF
[Unit]
Description=每日 AnyRouter 签到定时器

[Timer]
OnCalendar=*-*-* 09:30:00
Persistent=true
RandomizedDelaySec=180

[Install]
WantedBy=timers.target
EOF

cat > "/etc/systemd/system/${DEPLOY_SERVICE}" <<EOF
[Unit]
Description=anyrouter-checkin git 轮询自动部署
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=$RUN_USER
ExecStart=$APP_DIR/scripts/deploy_poll.sh
TimeoutStartSec=120
EOF

cat > "/etc/systemd/system/${DEPLOY_TIMER}" <<EOF
[Unit]
Description=anyrouter-checkin 自动部署轮询

[Timer]
OnBootSec=2min
OnUnitActiveSec=5min
Unit=$DEPLOY_SERVICE

[Install]
WantedBy=timers.target
EOF

chmod +x "$APP_DIR/scripts/deploy_poll.sh"
chown -R "$RUN_USER:$RUN_USER" "$APP_DIR"

# ---------- 5. 启用并启动定时器 ----------
log "重载 systemd 并启用定时器..."
systemctl daemon-reload
systemctl enable --now "${TIMER_NAME}"
systemctl enable --now "${DEPLOY_TIMER}"

# ---------- 6. 校验 .env ----------
log ""
log "===== 部署完成 ====="
systemctl list-timers "${TIMER_NAME}" "${DEPLOY_TIMER}" --no-pager

if [[ -f "$APP_DIR/.env" ]]; then
  log "检测到 .env（$(wc -l < "$APP_DIR/.env") 行），可立即手动签到：sudo systemctl start ${SERVICE_NAME}"
else
  echo "[install] ⚠️ 未检测到 $APP_DIR/.env —— 请手动放置真实 .env 后执行：sudo systemctl restart ${SERVICE_NAME}"
  echo "[install]  .env 样例见 $APP_DIR/.env.example；密码等密钥文件请 chmod 600 且不要提交到 git。"
fi

log "自动部署轮询已启用：此后本地 git push 后最多 5 分钟内服务器会自动拉取并重启。"
exit 0