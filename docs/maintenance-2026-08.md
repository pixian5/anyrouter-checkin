# 2026-08 维护记录

- 通知只比较完整账号集合的 `quota` 余额；累计消耗变化不会单独触发通知。
- 余额查询不完整时不更新全局基线，避免部分失败造成下次运行误报。
- 每个账号的当日状态使用 provider 加账号身份生成的稳定键，并兼容旧版 `account_N` 状态。
- 邮箱登录验证使用 provider 配置的 `console_path` 和 `user_info_path`。
- 已删除的服务器签到任务与 GitHub 自动签到 workflow 不再作为本项目部署步骤；GitHub 保留 PR 质量检查。
- 2026-08-13：签到与 AnyRouter VMess 客户端已从 `uk.sbbz.tech` 迁至 `sf.sbbz.tech`；首次创建完整余额基线不会单独触发通知。
- 2026-08-15：移除签到程序内的代理读取、浏览器代理参数和 Mihomo 启停脚本；sf 由 sing-box TUN 按 `anyrouter.top` 域名透明接管，签到程序不参与路由。
- 2026-08-15：每日状态切换日期时会清空上一天的账号和 provider 标记，避免前一天成功标记导致新一天失败账号被误跳过。
- 2026-08-15：sf 不再固定使用失效的 `jp4`，改由 `anyrouter-auto` 选择可用节点；真实 HTTPS 验证选择 `jp2` 并返回 HTTP 200。
- 2026-08-15：修复 cookies 合并顺序；保留用户 session，但同名 WAF cookies 始终使用本次浏览器获取的新值，避免旧 `acw_tc` 覆盖新值后收到 HTML 响应。
