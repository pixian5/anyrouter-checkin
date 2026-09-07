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
- `.env`：**带密码，不入 git**，`chmod 600`；账号经 `ANYROUTER_ACCOUNTS`（含 `provider` 字段）配置

## systemd 定时任务

- 服务：`/etc/systemd/system/anyrouter-checkin.service`（`Type=oneshot`, `User=linux1`）
- 定时器：`/etc/systemd/system/anyrouter-checkin.timer`，每天 09:30（UTC+8），`Persistent=true`，随机延时 180s
- 查看：`systemctl list-timers anyrouter-checkin.timer`
- 手动跑一次：`sudo systemctl start anyrouter-checkin.service`；日志 `sudo journalctl -u anyrouter-checkin.service -n 20`

## 自动部署（不用 GH Actions）

- **GitHub Actions 在此账号/仓库不派发 runner**（所有 run 永久 `queued`，账号级问题，无法修复），故不要依赖 GH Actions 自动部署。
- 改为**服务器端 git 轮询自动部署**：
  - `scripts/deploy_poll.sh`（systemd 定时器 `anyrouter-deploy.timer` 每 5 分钟跑一次）：`git fetch` → 检测到新 commit → `git reset --hard` + 装依赖 → 重启 `anyrouter-checkin.service`。
  - 实现「本地改 → push → 服务器自动拉取部署」闭环，log 写到 `/opt/anyrouter-checkin/deploy.log`。
  - 手动立即触发：`sudo systemctl start anyrouter-deploy.service`。
  - GitHub 侧仍保留 `deploy.yml`，若日后 runner 恢复可直接复用。
- 部署用专用 ed25519 私钥（GitHub secret `SSH_DEPLOY_KEY`）+ 部署公钥已加入 `linux1` 的 `authorized_keys`；`/etc/sudoers.d/anyrouter-deploy` 允许对签到服务免密 systemctl。

## 已复位（2026-09-07 新服务器全新部署）

- l.sbbz.tech 主机曾在 09-06→09-07 间被重置（宿主密钥改变 / `/opt` 为空），已在该新机上重装：
  - node v18 + python3-venv + git，克隆仓库，venv 装 httpx，恢复 `.env`，配置 systemd。
- 3 账号签到全部成功：agentrouter hqlak47/g_sbbz 余额 $1025（+$25/天），anyrouter 85976 余额 $7501.30。
- 已知缺陷（已修）：同日重复运行的跳过账号曾因 `balance_change` 未初始化抛 KeyError；现跳过显示 `[SKIP]`，退出码仅真实失败才非零。

## 注意事项

- 若续接 anyrouter 账号，需在 `.env` 加 `session` cookie（签到走 session，非邮箱密码）。
- 修改脚本后 push 即可触发服务器自动部署（轮询 ≤5 分钟）；改 systemd unit 需手动 `daemon-reload`。
- 首次运行（无历史基线）通知显示「当前余额」，次日即有「签到前/后 + 余额变化」。