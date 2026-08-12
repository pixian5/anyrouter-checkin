# 2026-08 维护记录

- 通知只比较完整账号集合的 `quota` 余额；累计消耗变化不会单独触发通知。
- 余额查询不完整时不更新全局基线，避免部分失败造成下次运行误报。
- 每个账号的当日状态使用 provider 加账号身份生成的稳定键，并兼容旧版 `account_N` 状态。
- 邮箱登录验证使用 provider 配置的 `console_path` 和 `user_info_path`。
- 已删除的服务器签到任务与 GitHub 自动签到 workflow 不再作为本项目部署步骤；GitHub 保留 PR 质量检查。
