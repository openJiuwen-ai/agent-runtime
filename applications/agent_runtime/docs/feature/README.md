# feature —— 每次改动一份文档

本目录是**项目记忆**:**较大改动**(新功能 / 行为变化 / 重构 / 部署形态变化 / 里程碑)新建一个文档,记录背景、方案定案、验证证据;**小的修复(局部 bugfix、注释/文案)不用建**。git commit 是事实源,这里的文档提供可读叙事与验收数据,回答「当时为什么这么改、怎么验证的」——这些恰恰是 commit message 和代码里读不出来的。

## 写作规范

- **时机**:改动定稿(合并/提交)时写,随改动一起提交;文档头登记 commit hash。
- **命名**:`YYYY-MM-<短横线-slug>.md`;有里程碑编号的带编号,如 `2026-08-M8-deploy-lock-follower-waitroom.md`。
- **结构**:按 [_TEMPLATE.md](_TEMPLATE.md);写完在下方索引表加一行。
- **内容纪律**:
  - 「与需求方确认的决策」逐条列出——这是防止后人重新踩已否决方案的关键。
  - 验证写实测数据(pytest 计数、e2e 结果、延迟数字),不写「已验证」三个字。
  - 被否方案与理由值得记,accepted-but-superseded 的决策标注演进关系。
- **颗粒度**:一次连贯的改动一份(一个 milestone / 一个有分量的 feature / 一次重构或文档重组),不求与 commit 一一对应。拿不准要不要建时,判据:半年后还有没有人需要知道「当时为什么这么改」。

## 索引

| 日期 | 文档 | 一句话 |
|---|---|---|
| 2026-08 | [生产可观测性:日志体系 + /debug 诊断端点](2026-08-production-observability.md) | LOG_LEVEL 起效、请求关联、每请求一行汇总、框架降噪(6行/秒→0)、7 个只读诊断端点+脱敏 |
| 2026-08 | [网络/IO 抖动超时兜底](2026-08-network-io-timeout-hardening.md) | redis socket 5s/建连 3s+重试、MySQL 建连 5s、sweeper tick 上限,挂死循环不再静默 |
| 2026-08 | [M8 deploy 锁输家改 follower 等待室](2026-08-M8-deploy-lock-follower-waitroom.md) | 跨副本冷竞争零多余 Pod,冷启动尾延迟 30.5s→10.2s |
