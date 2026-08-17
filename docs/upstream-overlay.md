# 上游外挂维护约定

本项目以 `millylee/anyrouter-check-in` 的 `upstream/main` 为上游。所有上游已存在文件都必须保持原文；pixian 定制只能放入 `pixian_overlay/`、独立部署文件、独立文档或新增测试。

## 运行方式

根目录 `checkin.py` 与 `utils/` 保持 `upstream/main` 原版。现有服务器定制主体位于 `pixian_overlay.app` 和 `pixian_overlay.utils`；`pixian_overlay.runner` 安装独立运行时补丁后调用外挂主体。服务器在自己的 clone 中执行 `deploy/install-local-systemd.sh`，安装器按当前目录生成 systemd unit，因此上游入口不承载服务器定制，也不绑定特定服务器或固定路径。

当前外挂 `actual_checkin` 负责验证手动签到是否真正到账，`pixian_overlay.app` 负责跨运行验证自动签到 provider：

- 接口明确返回“今日已签到”时接受结果。
- 接口返回“签到成功”时，必须观察到正向奖励，计算公式为：`签到后余额 - 签到前余额 + 签到后累计消耗 - 签到前累计消耗`。
- 即使签到期间恰好消费了 $25、签到也增加 $25，余额净变化为零时仍能由累计消耗增量确认奖励。
- 首次余额没有变化时，间隔两秒最多只读复核三次。
- 仍无奖励时返回失败，不写入当日成功状态，后续定时任务会继续尝试。
- AgentRouter 之类“读取资料即自动签到”的 provider 使用上一轮完整 `quota + used` 快照证明奖励；失败运行不会覆盖奖励证据基线，因此延迟到账仍可在后续 timer 中确认。
- 累计消耗计数器重置时，只采信正向余额增量；单独的计数器归零不构成奖励。

状态文件由外挂原子写入；损坏状态、非有限数值、重复账号键、未知 provider 和重叠进程都会在发送账号请求前被阻止。通知余额比较仍只使用 `quota`，快照中的 `used` 与 `evidence_*` 只服务于奖励证明，不会因累计消耗变化单独推送。

## 上游更新

1. `git fetch upstream`，先查看 `upstream/main...main` 差异。
2. 更新上游代码时直接同步上游原文，不把外挂逻辑重新揉进任何上游文件。
3. 运行上游测试与 `tests/test_actual_checkin_overlay.py`。
4. 需要安装 systemd 服务时，在目标服务器的项目目录运行 `deploy/install-local-systemd.sh`，确认 `WorkingDirectory` 和 `ExecStart` 都指向该目录。

每次提交和部署前运行 `scripts/verify_upstream_unchanged.sh`，逐个比较 `upstream/main` 已存在文件的内容和文件模式。上游原版当前存在自身的 MyPy 类型错误，因此 MyPy 只对 `pixian_overlay` 与定制测试指定路径运行；不能通过修改上游文件或上游配置消除这些错误。

上游 workflow 同样保持原文。GitHub 仓库的 Actions 权限在仓库设置中关闭，所以这些 workflow 不会执行；不要靠删除或改写 workflow 实现停用。
