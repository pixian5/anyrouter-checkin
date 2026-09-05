# s390x 新服务器部署（l.sbbz.tech）纯 HTTP 签到

> 日期：2026-09-06
> 背景：旧签到服务器已删除，签到迁移到新服务器 l.sbbz.tech（Ubuntu 24.04，**s390x** 架构）。

## 为什么需要重构为纯 HTTP

- s390x（IBM Z 大型机）**无可用浏览器引擎**：Playwright / curl_cffi 都没有 s390x 预编译轮子，`js2py` 在 Python 3.12 上已损坏（`KeyError: 3`）。
- 服务器有 **Node.js v18**，可执行 anyrouter 的 WAF 混淆 JS 解出 `acw_sc__v2`。

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

## 已验证（2026-09-06）

- 两账号（hqlak47 id=313043、g_sbbz id=257232）均签到成功，余额正常
- Bark 推送 status=200，标题「✅ 签到全部成功 (2/2)」
- 退出码 0

## 注意事项

- 若续接 anyrouter 账号，需在 `.env` 加 `session` cookie（签到走 session，非邮箱密码）。
- 修改脚本后需同步并 `sudo systemctl daemon-reload`（改 service 时）。