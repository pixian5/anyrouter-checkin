# s390x 新服务器部署（l.sbbz.tech）纯 HTTP 签到

> 日期：2026-09-06
> 背景：旧签到服务器已删除，签到迁移到新服务器 l.sbbz.tech（Ubuntu 24.04，**s390x** 架构）。

## 为什么需要重构为纯 HTTP

- s390x（IBM Z 大型机）**无可用浏览器引擎**：Playwright / curl_cffi 都没有 s390x 预编译轮子，`js2py` 在 Python 3.12 上已损坏（`KeyError: 3`）。
- 服务器有 **Node.js v18**，可执行 anyrouter 的 WAF 混淆 JS 解出 `acw_sc__v2`。

### anyrouter WAF 解算(node vm)

- challenge 为腾讯系混淆指纹引擎，明文 Node 无法直接解出。
- 用 Node `vm.createContext` 注入完整浏览器全局(document/navigator/screen/location…)，`runInContext` 执行混淆 JS，从 `document.cookie` 读出真实 `acw_sc__v2`。
- 早期用正则抓 `acw_sc__v2=([^;]+)` 抓到的是表达式残片（`+v+L(0x120)+new Dat…`）系假 cookie，务必用 vm 完整求解。

## 数据存储(每日余额存档)

- 每次签到把余额/累计消耗写入 SQLite 审计库 `checkin_history.sqlite3`(表 `checkin_history`,按 account_key+id 索引)。
- 依据最近一次成功记录计算「相对上次记录的余额变化」。
- 余额原始单位→美元换算：`$ = quota / 500000`(500000 单位 = $1)。
- 首次运行无基线，通知显示「当前余额」；次日运行即有「签到前/签到后」与余额变化。

## 新脚本

- `scripts/s390x_checkin.py` —— 唯一入口，无需浏览器。
  - **agentrouter**：邮箱密码 `POST /api/user/login` → 从响应 `data.id` 提取用户 ID → `GET /api/user/self` 携带 `New-Api-User` 头**自动签到**。
  - **anyrouter**：`GET /login` 拿 `acw_tc`/`cdn_sec_tc` + WAF JS → node 解 `acw_sc__v2` → 注入 session → `POST /api/user/sign_in`。
  - 合并所有 provider 结果为**一条** Bark 通知，格式符合既有规范。

## 服务器环境

- 路径：`/opt/anyrouter-checkin`（属主 `linux1`）
- venv：`/opt/anyrouter-checkin/venv`，仅依赖 `httpx[http2]`
- `.env`：**带密码，不入 git**，`chmod 600`；账号经 `ACCOUNT_1..ACCOUNT_N`（每账号一个独立变量，含 `provider` 字段）配置

## 账号配置（ACCOUNT_N 格式）

> （2026-09-08 重构）废弃单变量 `ANYROUTER_ACCOUNTS`（一个 JSON 数组塞全部账号）。改为**每个账号一个独立环境变量** `ACCOUNT_1`、`ACCOUNT_2`…，每个值是单个 JSON 对象，用 `provider` 区分平台。脚本顺序扫描 `ACCOUNT_1` 起，**遇到空缺即停止（不可跳号）**。

```env
ACCOUNT_1={"name":"主账号","provider":"agentrouter","email":"your@email.com","password":"xxx","api_user":"313043"}
ACCOUNT_2={"name":"g_sbbz","provider":"agentrouter","email":"g@sbbz.tech","password":"xxx","api_user":"257232"}
ACCOUNT_3={"name":"85976","provider":"anyrouter","session":"你的session","api_user":"85976"}
```

| 字段 | 必填 | 说明 |
|------|------|------|
| `provider` | ✅ | `"agentrouter"` / `"anyrouter"`，决定走哪种签到协议 |
| `name` | 可选 | 显示名，缺省用 `ACCOUNT_N` |
| `email`+`password` | agentrouter | 账号邮箱/密码登录（登录后自动从 `data.id` 取 New-Api-User） |
| `session` | anyrouter | Web session cookie 登录（续接 anyrouter 账号必须提供） |
| `api_user` | 可选 | `New-Api-User` 头值，缺省登录后自动取 |

- 单值必须是合法 JSON 对象，否则脚本直接报错退出。
- 完整实例见仓库 `.env.example`。

## systemd 定时任务

- 服务：`/etc/systemd/system/anyrouter-checkin.service`（`Type=oneshot`, `User=linux1`）
- 定时器：`/etc/systemd/system/anyrouter-checkin.timer`，每天 09:30（UTC+8），`Persistent=true`，随机延时 180s
- 查看：`systemctl list-timers anyrouter-checkin.timer`
- 手动跑一次：`sudo systemctl start anyrouter-checkin.service`；日志 `sudo journalctl -u anyrouter-checkin.service -n 20`

## 自动部署

**2026-09-08 根因澄清**：GitHub Actions 卡 `queued` 的根因是**该仓库的 Actions 功能被关闭**（`GET /repos/{owner}/{repo}/actions/permissions` 返回 `enabled:false`，非账号级限制，也不是 runner 不派发）。已通过 `gh api .../actions/permissions -X PUT -F enabled=true -f allowed_actions=all` 重新启用，之后所有 workflow 正常派发并 `success`。

- GitHub Actions：`.github/workflows/deploy.yml`，`on: push` 到 `main` → SSH 到服务器 `git reset --hard` + 装依赖 + 重启签到服务。已实测 `conclusion: success`。
- 服务器端 git 轮询（备用，防 GH Actions 故障）：`scripts/deploy_poll.sh` + `anyrouter-deploy.timer` 每 5 分钟 `git fetch`，检测到新 commit 即拉取+重启，log 写 `/opt/anyrouter-checkin/deploy.log`。手动立即触发 `sudo systemctl start anyrouter-deploy.service`。
- 部署用专用 ed25519 私钥（GitHub secret `SSH_DEPLOY_KEY`）+ 公钥加入 `linux1.authorized_keys`；`/etc/sudoers.d/anyrouter-deploy` 允许对签到服务免密 systemctl。

**遗留事项**：启用 Actions 前遗留的几个 `AnyRouter 自动签到`（checkin.yml 旧浏览器流程）run 处于坏掉态，`gh run cancel` 报"completed"无法取消，无害，会自然淡出。

## 已复位（2026-09-07 新服务器全新部署）

- l.sbbz.tech 主机曾在 09-06→09-07 间被重置（宿主密钥改变 / `/opt` 为空），已在该新机上重装：
  - node v18 + python3-venv + git，克隆仓库，venv 装 httpx，恢复 `.env`，配置 systemd。
- 3 账号签到全部成功：agentrouter hqlak47/g_sbbz 余额 $1025（+$25/天），anyrouter 85976 余额 $7501.30。
- 已知缺陷（已修）：同日重复运行的跳过账号曾因 `balance_change` 未初始化抛 KeyError；现跳过显示 `[SKIP]`，退出码仅真实失败才非零。

## 服务器一键安装脚本（新机复现部署）

> （2026-09-08）当再次换新服务器时，用 `scripts/install_server.sh` 一键复现整套部署，无需手工配置。

- 作用：安装依赖（git/nodejs/python3-venv）→ 克隆仓库到 `/opt/anyrouter-checkin` → 建 venv 装 `httpx[http2]` → 写入签到 + 自动部署的 systemd 服务与定时器 → 启用定时器。
- 用法（在服务器上，用 有 sudo 的用户执行）：

  ```bash
  # 方式A：下载脚本直接跑（需能访问 raw 链接）
  bash <(curl -sL <该文件raw链接>)
  # 方式B：本地脚本
  sudo bash scripts/install_server.sh [仓库URL]
  ```

- 默认以当前 Linux 用户身份跑服务；仓库 URL 缺省为 `https://github.com/pixian5/anyrouter-checkin.git`。
- **跑完后必须另放一份真实 `.env`**（含账号/密钥，`chmod 600`，见上文「账号配置（ACCOUNT_N 格式）」），然后 `sudo systemctl start anyrouter-checkin.service` 手动验证一次。
- 装完即已启用 5 分钟自动部署轮询，此后本地 `git push` 即自动拉取重启。

## 注意事项

- 若续接 anyrouter 账号，需在 `.env` 加 `session` cookie（签到走 session，非邮箱密码）。
- 修改脚本后 push 即可触发服务器自动部署（轮询 ≤5 分钟）；改 systemd unit 需手动 `daemon-reload`。
- 首次运行（无历史基线）通知显示「当前余额」，次日即有「签到前/后 + 余额变化」。