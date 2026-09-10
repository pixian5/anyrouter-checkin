# AnyRouter-Check-in · 部署与使用指南（AI 可执行版）

> 用途：让任何 AI / 开发者不依赖作者记忆，仅凭本文件即可从零部署、运行、排查「账号每日自动签到」。
> 项目根：本文件所在的仓库 `anyrouter-checkin`。
> 运行环境分两种，**机制完全不同，不要混用**：
> 1. **服务器**：Ubuntu（含 **s390x / IBM Z 大机**，无浏览器）→ 走 **纯 HTTP** 脚本。
> 2. **本机**：macOS → 同样用纯 HTTP 脚本，但依赖本机 node 解 WAF、并可能受 TUN/VPN 影响。

---

## 〇、给 AI 的起始指令（直接整段粘贴给任何 AI）

> 请处理这个仓库 `anyrouter-checkin` 的账号每日自动签到任务。
>
> 第一步，先读本文件 `docs/DEPLOY-GUIDE.md` 了解完整机制，再按本文件从零动手。本文件是唯一权威，文档里没覆盖的情况再问我。
>
> 你要做的完整流程如下：
>
> 1. **克隆仓库**：
>    ```bash
>    git clone https://github.com/pixian5/anyrouter-checkin.git
>    cd anyrouter-checkin
>    ```
> 2. **配置账号**：复制 `.env.example` 为 `.env`，按「账号配置（ACCOUNT_N 格式）」填入真实账号（每个账号一个 `ACCOUNT_N` 变量，含 provider/email+password 或 session）。`.env` 含密钥，不要提交 git。
> 3. **准备依赖**：`python3 -m venv venv && ./venv/bin/pip install "httpx[http2]"`，确认有 node（anyrouter 解 WAF 用）。
> 4. **手动跑通**：`./venv/bin/python scripts/s390x_checkin.py`，确认 3 个账号全部签到成功、日志显示余额/G奖励、Bark 收到一条「✅ 签到全部成功 (3/3)」。
> 5. **部署到服务器**（若需要）：照「服务器部署」节，用 `scripts/install_server.sh` 一键部署，然后手动放置真实 `.env` 并 `sudo systemctl start anyrouter-checkin.service` 验证一次。
> 6. **改代码后自动上线**：本地提交并 push 到 `main`，服务器经 GitHub Actions 或每 5 分钟的 git 轮询自动拉取并重启。改代码务必用 `scripts/s390x_checkin.py`，不要动仓库根目录旧的浏览器版 `checkin.py` / `pixian_overlay/*`。
>
> 请一步步执行并在每步完成后报告结果，遇到网络类 `ConnectError` 时先重跑确认（可能是本机 TUN 瞬态），不要误判。

---

## 一、项目能做什么

对一组**多平台账号**（agentrouter / anyrouter）每日自动「签到领余额」，并把结果合并成**一条** Bark 推送。

- 底层是 new-api 系站点的签到接口。
- **agentrouter**：邮箱+密码登录 → `GET /api/user/self` 带 `New-Api-User` 头触发自动签到。
- **anyrouter**：需先解腾讯系 WAF 指纹挑战（用 node vm 解出 `acw_sc__v2` cookie），再带 session 调签到接口。
- 每次签到把余额写进 SQLite 审计库，可计算「相对上次记录的余额变化」。

**当前唯一正式入口**：`scripts/s390x_checkin.py`（纯 HTTP，无浏览器）。
仓库根目录的 `checkin.py`、`pixian_overlay/*` 是**已废弃的旧浏览器实现**，不要在部署中使用。

---

## 二、配置（`.env`）

复制 `.env.example` 为 `.env`，填写以下核心项。`.env` **含密码/密钥，禁止提交 git**，权限 `chmod 600`。

### 账号：每个账号一个独立变量 `ACCOUNT_N`

从 `ACCOUNT_1` 起顺序编号，**不可跳号**，遇到空缺即停止读取。每个值是一个 JSON 对象，`provider` 必填：

```env
# agentrouter：邮箱+密码登录
ACCOUNT_1={"name":"主账号","provider":"agentrouter","email":"user@mail.com","password":"pass","api_user":"用户ID数字"}

# anyrouter：用 session cookie 登录（先登录网页控制台复制 session）
ACCOUNT_2={"name":"85976","provider":"anyrouter","session":"<很长的session串>","api_user":"85976"}
```

字段说明：

| 字段 | 必填 | 说明 |
|------|------|------|
| `provider` | ✅ | `"agentrouter"` / `"anyrouter"`，决定走哪套签到协议 |
| `name` | 可选 | 显示名，缺省用 `ACCOUNT_N` |
| `email`+`password` | agentrouter | 邮箱/密码登录 |
| `session` | anyrouter | Web session cookie（续接时必须提供） |
| `api_user` | 可选 | `New-Api-User` 头；缺省登录后自动从 `data.id` 取 |

### 通知（Bark）

```env
BARK_SERVER=https://api.day.app
BARK_KEY=<你的Bark key>
```

缺省 BARK_KEY 则不推送。通知必须带 `icon={url}`，标题固定 `✅ 签到全部成功 (X/X)`。

---

## 三、运行方式

### 依赖

```bash
# 服务器 / 本机通用：Python ≥3.11 仅需 httpx
python3 -m venv venv
./venv/bin/pip install "httpx[http2]"
# anyrouter 的 WAF 解算依赖 node（服务器 s390x 需有 node18+，本机 macOS 自带 node 即可）
node --version
```

### 手动跑一次

```bash
./venv/bin/python scripts/s390x_checkin.py
```

退出码：仅当存在「真实失败」（非跳过、非成功）才返回 1，否则 0。

### 输出/验证

- 成功显示各账号「签到前/后余额、签到获得、余额变化」。
- if 失败显示错误原因（`ConnectError` = 网络，`WAF 获取失败` = anyrouter WAF 卡在连接阶段）。
- 结果写入 `checkin_history.sqlite3`（表 `checkin_history`）。
- 可查历史：`./venv/bin/python scripts/show_checkin_history.py`。

---

## 四、服务器部署（Ubuntu / s390x）

### 方案 A：一键脚本（推荐，可复现）

在一台全新服务器上用有 sudo 的用户执行：

```bash
sudo bash /path/to/scripts/install_server.sh   # 可选第2参数为仓库URL，缺省 https://github.com/pixian5/anyrouter-checkin.git
# 或远程拉脚本： bash <(curl -sL <raw链接>)
```

脚本做的事：
1. 装依赖（git/nodejs/python3-venv）
2. 克隆仓库到 `/opt/anyrouter-checkin`
3. 建 venv 装 `httpx[http2]`
4. 写 systemd 单位：
   - `anyrouter-checkin.service`（oneshot，签到）
   - `anyrouter-checkin.timer`（每日 09:30，Persistent，随机延时 180s）
   - `anyrouter-deploy.service/.timer`（每 5 分钟 git 轮询自动部署）
5. 启用定时器

之后**必须**：
```bash
# 放置真实 .env（含账号/密钥），然后手动验证一次
sudo systemctl start anyrouter-checkin.service
sudo journalctl -u anyrouter-checkin.service -n 30
```

### 方案 B：手动部署

```bash
sudo mkdir -p /opt && sudo git clone https://github.com/pixian5/anyrouter-checkin.git /opt/anyrouter-checkin
cd /opt/anyrouter-checkin
sudo chown -R $(whoami):$(whoami) .
python3 -m venv venv && ./venv/bin/pip install "httpx[http2]"
# 手写 systemd 单位（参考 deploy/systemd/*）或直接复用 install_server.sh
```

### 定时器管理

```bash
systemctl list-timers anyrouter-checkin.timer       # 查看下次触发
sudo systemctl start anyrouter-checkin.service      # 立即手动签到
sudo journalctl -u anyrouter-checkin.service -n 20  # 日志
```

---

## 五、自动部署（改代码后自动上线，双保险）

1. **GitHub Actions**（`on: push` 到 `main`）：SSH 到服务器 `git reset --hard` + 装依赖 + 重启签到服务。
   - 依赖仓库 Secrets：`DEPLOY_HOST`、`DEPLOY_USER`、`SSH_DEPLOY_KEY`。
   - **重要**：若 Action 永久卡 `queued`，先查仓库 Actions 开关是否被关：`gh api repos/{owner}/{repo}/actions/permissions`，为 `enabled:false` 时用 `gh api ... -X PUT -F enabled=true` 开启（**非账号级限制**）。
2. **服务器端 git 轮询**（备用）：`scripts/deploy_poll.sh` + `anyrouter-deploy.timer` 每 5 分钟 `git fetch`，检测新 commit 即拉取并重启签到服务，日志写 `/opt/anyrouter-checkin/deploy.log`。

因此**本地只push代码，服务器最多 5 分钟内自动拉取并重签**。

---

## 六、s390x 架构特殊性

- **无浏览器引擎**：Playwright / curl_cffi 均无 s390x 轮子，`js2py` 在 Py3.12 损坏 → 只能纯 HTTP。
- anyrouter WAF `acw_sc__v2` 必须用 **node `vm` 注入完整浏览器全局**执行混淆 JS 求解；用正则抓取得到的是**假 cookie**（表达式残片），务必用 vm 完整解。
- 部署后务必在服务器实测 3 个账号均成功再收尾。

---

## 七、本机（macOS）运行注意

- 用上面「手动跑一次」即可，依赖 node（macOS 自带，或用 Homebrew）。
- **若开启了 TUN 增强模式**：刚开启时直连 anyrouter.top/Servers 会瞬态 `ConnectError`（路由/fake-ip 未就绪），**等 TUN 稳定后重跑即恢复**。因此遇 `ConnectError` 先重跑，**不要急着断定需加代理**。
- 网络设置里可能残留 TUN/VPN 自带本地转发口（如 `127.0.0.1:58964`，状态 Disabled），它是 TUN 自带、**非用户主动代理，不要据此改脚本**。

---

## 八、排查清单

| 症状 | 原因与处理 |
|------|-----------|
| `ConnectError` / `WAF 获取失败` | 网络。TUN 场景先重跑；服务器场景 ping 不通可能是机器/防火墙/被暂停 |
| Actions 一直 `queued` | 仓库 Actions 被关，用 API 重新启用（见第五节） |
| 某账号总是 fails 不通知 | 已按账号级跳过追踪；失败账号每轮会重试并计入通知 |
| 无 Bark 通知 | BARK_KEY 未配 / iOS 通知被系统杀掉或勿扰 |
| `401`（anyrouter） | session cookie 过期，替换 `.env` 里该账号的 `session` |

---

## 九、经验教训（移植时务必带上）

- agentrouter 需备用域 `https://ps.air-outer.com` 直连；主域 agentrouter.org 会触发 WAF 滑块。
- anyrouter.top 可能对部分海外数据中心 IP 做 TCP/TLS 层阻断；必要时经节点/代理路由。
- `New-Api-User` 头必须在登录后拿**真实用户 ID**（前端 localStorage 的 `user.id`），不能用 -1。
- 同日重复运行会显示「今日已签到，无变化」，正确防重复领取。