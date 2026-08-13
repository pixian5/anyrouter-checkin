# 2026-08 维护记录

- 通知只比较完整账号集合的 `quota` 余额；累计消耗变化不会单独触发通知。
- 余额查询不完整时不更新全局基线，避免部分失败造成下次运行误报。
- 每个账号的当日状态使用 provider 加账号身份生成的稳定键，并兼容旧版 `account_N` 状态。
- 邮箱登录验证使用 provider 配置的 `console_path` 和 `user_info_path`。
- 已删除的服务器签到任务与 GitHub 自动签到 workflow 不再作为本项目部署步骤；GitHub 保留 PR 质量检查。
- 2026-08-13：签到与 AnyRouter VMess 客户端已从 `uk.sbbz.tech` 迁至 `sf.sbbz.tech`；首次创建完整余额基线不会单独触发通知。
- 2026-08-14：签到客户端优先读取 `CHECKIN_PROXY_URL`，并兼容旧的 `ANYROUTER_PROXY`；sf 的 systemd 服务设置前者以确保 AnyRouter 请求走本地 VMess 代理。
- 2026-08-14：内置 provider 的 `PROVIDERS` 部分覆盖会继承默认域名和接口配置，允许仅设置 `{"anyrouter":{"use_proxy":true}}`，避免因缺少 `domain` 静默回退为直连。
- 2026-08-14：验证订阅节点到 AnyRouter 的真实链路后，sf 当前固定使用 `jp4`；AnyRouter 新 session 可读取但 `/api/user/self` 明确返回 401，需重新获取有效 session 或 access-token cookie。
