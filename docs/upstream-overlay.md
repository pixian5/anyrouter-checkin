# 上游外挂维护约定

本项目以 `millylee/anyrouter-check-in` 的 `upstream/main` 为上游，并在本地集成分支中保留完整上游 Git 历史。上游文件可以在本项目分支中直接修改；同步时通过 Git 合并显式处理冲突。签到奖励校验、状态与审计等项目定制仍优先放在 `pixian_overlay/`、独立部署文件和测试中，以减少与上游改动的冲突面。

## 运行方式

根目录 `checkin.py` 与 `utils/` 以 `upstream/main` 为基线；需要直接修改时，应在集成分支上提交，并补充对应回归测试。现有服务器定制主体位于 `pixian_overlay.app` 和 `pixian_overlay.utils`；`pixian_overlay.runner` 安装独立运行时补丁后调用外挂主体。服务器在自己的 clone 中执行 `deploy/install-local-systemd.sh`，安装器按当前目录生成 systemd unit，因此运行入口不绑定特定服务器或固定路径。

当前外挂 `actual_checkin` 负责验证手动签到是否真正到账，`pixian_overlay.app` 负责跨运行验证自动签到 provider：

- 接口明确返回“今日已签到”时接受结果。
- 接口返回“签到成功”时，必须观察到正向奖励，计算公式为：`签到后余额 - 签到前余额 + 签到后累计消耗 - 签到前累计消耗`。
- 即使签到期间恰好消费了 $25、签到也增加 $25，余额净变化为零时仍能由累计消耗增量确认奖励。
- 首次余额没有变化时，间隔两秒最多只读复核三次。
- 仍无奖励时返回失败，不写入当日成功状态，后续定时任务会继续尝试。
- AgentRouter 之类“读取资料即自动签到”的 provider 使用上一轮完整 `quota + used` 快照证明奖励；失败运行不会覆盖奖励证据基线，因此延迟到账仍可在后续 timer 中确认。
- 累计消耗计数器重置时，只采信正向余额增量；单独的计数器归零不构成奖励。

状态文件由外挂原子写入；损坏状态、非有限数值、重复账号键、未知 provider 和重叠进程都会在发送账号请求前被阻止。通知余额比较仍只使用 `quota`，快照中的 `used` 与 `evidence_*` 只服务于奖励证明，不会因累计消耗变化单独推送。

外挂还维护项目目录中的 SQLite 数据库 `checkin_history.sqlite3`：每次运行的一条摘要写入 `checkin_runs`，各账号签到前后余额/消耗、奖励、跳过和错误原因写入 `checkin_account_records`。即使接口响应成功但奖励未被余额证据确认，也会保存实际观察到的余额和消耗，并标记为失败；不得记录 Cookie、密码、WAF Cookie 或完整接口响应。数据库写入采用单事务，且在账号请求前就验证可写。`scripts/show_checkin_history.py` 是只读查询入口。

## 上游更新

本地仓库最初以源码快照单独初始化，没有与 GitHub 上游共享提交祖先。首次接入在隔离分支中使用一次 `git merge --allow-unrelated-histories upstream/main`，保留两边历史；之后可按常规方式同步上游：

1. 在集成分支确认工作区干净，执行 `git fetch upstream`。
2. 查看 `git log --oneline HEAD..upstream/main` 与 `git diff --stat HEAD...upstream/main`。
3. 执行 `git merge --no-ff upstream/main`，逐项解决冲突，保留所需的上游改动和本地定制。
4. 运行项目测试及 `tests/test_actual_checkin_overlay.py`，并执行 `scripts/verify_upstream_merged.sh`。
5. 需要安装 systemd 服务时，在目标服务器的项目目录运行 `deploy/install-local-systemd.sh`，确认 `WorkingDirectory` 和 `ExecStart` 都指向该目录。

`verify_upstream_merged.sh` 检查当前分支是否包含指定上游引用的提交历史，不再要求上游文件逐字节相同；这允许本地直接修改上游代码。上游原版当前存在自身的 MyPy 类型错误，因此 MyPy 仍只对 `pixian_overlay` 与定制测试指定路径运行。

GitHub 仓库的 Actions 权限在仓库设置中关闭；workflow 的存在或本地修改不会自行启用远程签到，是否运行仍由仓库级 Actions 设置决定。
