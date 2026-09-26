# aicheckin 新服务器部署（l.sbbz.tech）纯 HTTP 签到

> 日期：2026-09-06
> 状态：**历史文档**。当前生产环境已非本文件描述的 l.sbbz.tech / s390x / systemd 架构，详见 `docs/DEPLOY-GUIDE.md` 的当前说明。

本文件保留作为**历史参考**，技术细节（WAF 解算、数据存储、账号配置）仍有效。

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

- `scripts/aicheckin.py` —— 唯一入口，无需浏览器。
  - **agentrouter**：邮箱密码 `POST /api/user/login` → 从响应 `data.id` 提取用户 ID → `GET /api/user/self` 携带 `New-Api-User` 头**自动签到**。
  - **anyrouter**：`GET /login` 拿 `acw_tc`/`cdn_sec_tc` + WAF JS → node 解 `acw_sc__v2` → 注入 session → `POST /api/user/sign_in`。
  - 合并所有 provider 结果为**一条** Bark 通知，格式符合既有规范。

## 服务器环境

> **历史配置（2026-09-06）**：路径 `/opt/anyrouter-checkin`（属主 `linux1`），venv `/opt/anyrouter-checkin/venv`，仅依赖 `httpx[http2]`。
>
> **当前生产环境**：本机容器即生产，项目路径 `/root/workspace/anyrouter-checkin`，无 systemd，由 supervisor 守护进程管理。

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

> **历史配置（2026-09-06）**：服务 `/etc/systemd/system/anyrouter-checkin.service`（`Type=oneshot`, `User=linux1`），定时器 `/etc/systemd/system/anyrouter-checkin.timer` 每天 09:30（UTC+8）。
>
> **当前生产环境**：无 systemd，由 supervisor 守护进程管理，签到时间 12:00 台北时间。

## 自动部署

> **历史配置（2026-09-08）**：GitHub Actions + 服务器端 git 轮询（`scripts/deploy_poll.sh` + `anyrouter-deploy.timer`）。
>
> **当前生产环境**：推 git 仅为版本管理，不触发自动部署。生产即本容器，无第二部署目标。

**遗留事项**：启用 Actions 前遗留的几个 `AnyRouter 自动签到`（checkin.yml 旧浏览器流程）run 处于坏掉态，`gh run cancel` 报"completed"无法取消，无害，会自然淡出。

## 已复位（2026-09-07 新服务器全新部署）

- l.sbbz.tech 主机曾在 09-06→09-07 间被重置（宿主密钥改变 / `/opt` 为空），已在该新机上重装：
  - node v18 + python3-venv + git，克隆仓库，venv 装 httpx，恢复 `.env`，配置 systemd。
- 3 账号签到全部成功：agentrouter hqlak47/g_sbbz 余额 $1025（+$25/天），anyrouter 85976 余额 $7501.30。
- 已知缺陷（已修）：同日重复运行的跳过账号曾因 `balance_change` 未初始化抛 KeyError；现跳过显示 `[SKIP]`，退出码仅真实失败才非零。

## 服务器一键安装脚本（新机复现部署）

> **历史脚本（2026-09-08）**：`scripts/install_server.sh` 曾用于新服务器一键部署。
>
> **当前状态**：脚本仍保留时区设置逻辑，但 systemd / 自动部署部分已不再适用。

## 注意事项

- 若续接 anyrouter 账号，需在 `.env` 加 `session` cookie（签到走 session，非邮箱密码）。
- 首次运行（无历史基线）通知显示「当前余额」，次日即有「签到前/后 + 余额变化」。

## 本机（macOS + TUN）签到注意

- 本机若开启了 **TUN 增强模式**（虚拟网卡接管流量），**刚开启时**直连 anyrouter.top 会瞬态 `ConnectError`（路由/fake-ip 未就绪），**等 TUN 稳定后同一条 httpx 直连即恢复正常返回 200**。
- 遇到 `anyrouter... ConnectError` 优先重跑一次确认，**不要急着断定要加代理**：anyrouter.top 直连即可签到（httpx + 同款 UA 实测 200），无需 `use_proxy`。
- 系统网络设置里可能残留一个 TUN/VPN 附带的本地转发口（如 `127.0.0.1:58964`，状态 Disabled），**那是 TUN 自带、非用户主动配置的代理，与签到无关，不要据此改脚本**。
- 本机签到用 `./venv/bin/python scripts/aicheckin.py`（venv 内运行，依赖 node 解 WAF）。