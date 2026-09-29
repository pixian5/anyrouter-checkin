# 上游外挂维护约定

本项目以 `millylee/anyrouter-check-in` 的 `upstream/main` 为上游，并保留完整上游 Git 历史。除 README 中按全局工程要求增加的进度/计划区块外，所有上游已有文件都必须保持与 `upstream/main` 相同的内容和文件模式。签到奖励校验、状态、审计、部署忽略规则和镜像下载等本地定制只能放在 `pixian_overlay/`、`deploy/`、独立文档或新增测试中。

## 运行方式

根目录 `checkin.py`、`utils/`、`.env.example`、`.gitignore`、`pyproject.toml`、`uv.lock`、上游 workflow 和 `scripts/setup_mihomo_proxy.sh` 均保持上游原文。现有服务器定制主体位于 `pixian_overlay.app` 和 `pixian_overlay.utils`；`pixian_overlay.runner` 安装独立运行时补丁后调用外挂主体。服务器在自己的 clone 中执行 `deploy/install-local-systemd.sh`，安装器按当前目录生成 systemd unit，并把本地运行产物规则写入 `.git/info/exclude`，因此无需修改上游 `.gitignore`。

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

本地快照历史已通过一次 `git merge --allow-unrelated-histories upstream/main` 接入，当前分支已有共同祖先；之后按常规方式同步：

1. 在集成分支确认工作区干净，执行 `git fetch upstream`。
2. 查看 `git log --oneline HEAD..upstream/main` 与 `git diff --stat HEAD...upstream/main`。
3. 执行 `git merge --no-ff upstream/main`。若有冲突，把需要保留的本地行为迁入 `pixian_overlay/`、`deploy/` 或独立测试，不直接改上游文件。
4. 运行项目测试，并执行 `scripts/verify_upstream_merged.sh` 与 `scripts/verify_upstream_unchanged.sh`。
5. 需要安装 systemd 服务时，在目标服务器的项目目录运行 `deploy/install-local-systemd.sh`，确认 `WorkingDirectory` 和 `ExecStart` 都指向该目录。

`verify_upstream_merged.sh` 检查上游历史已合入；`verify_upstream_unchanged.sh` 逐项检查上游文件内容和模式。后者只允许 README 中必需的开发进度/计划区块，其余 README 内容也必须与上游一致。上游原版当前存在自身的 MyPy 类型错误，因此 MyPy 只对 `pixian_overlay` 与定制测试指定路径运行。

本地非上游模板位于 `deploy/workflows/pixian-checkin.yml`，不会被 GitHub Actions 自动加载；上游 workflow 保留原文。GitHub 仓库的 Actions 权限在仓库设置中关闭，是否运行仍由仓库级配置决定。
