# 上游外挂维护约定

本项目以 `millylee/anyrouter-check-in` 的 `upstream/main` 为上游。新增的 pixian 定制逻辑必须优先放入 `pixian_overlay/`、独立部署文件或新增测试，不再继续修改上游核心签到函数。

## 运行方式

根目录 `checkin.py` 与 `utils/` 保持 `upstream/main` 原版。现有服务器定制主体位于 `pixian_overlay.app` 和 `pixian_overlay.utils`；`pixian_overlay.runner` 安装独立运行时补丁后调用外挂主体。服务器通过 `deploy/systemd/anyrouter-checkin-overlay.conf` 覆盖 `ExecStart`，因此上游入口不承载服务器定制。

当前外挂 `actual_checkin` 负责验证手动签到是否真正到账：

- 接口明确返回“今日已签到”时接受结果。
- 接口返回“签到成功”时，必须观察到正向奖励，计算公式为：`签到后余额 - 签到前余额 + 签到后累计消耗 - 签到前累计消耗`。
- 即使签到期间恰好消费了 $25、签到也增加 $25，余额净变化为零时仍能由累计消耗增量确认奖励。
- 首次余额没有变化时，间隔两秒最多只读复核三次。
- 仍无奖励时返回失败，不写入当日成功状态，后续定时任务会继续尝试。

## 上游更新

1. `git fetch upstream`，先查看 `upstream/main...main` 差异。
2. 更新上游代码时直接更新根目录主体，不把外挂逻辑重新揉进上游函数。
3. 运行上游测试与 `tests/test_actual_checkin_overlay.py`。
4. 部署后确认 systemd 的实际 `ExecStart` 指向 `pixian_overlay.runner`。

每次提交前使用 `git diff --exit-code upstream/main -- checkin.py utils/` 检查上游主体没有混入外挂改动。
上游原版当前存在自身的 MyPy 类型错误，因此项目 MyPy 配置排除根目录上游主体，只严格检查 `pixian_overlay` 与定制测试；上游行为由其原版 Pytest 覆盖。
