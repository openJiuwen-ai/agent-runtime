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
| 2026-08 | [容器表拆分 + config_sync 三段式契约(K8s 原生形态)](2026-08-container-table-split.md) | 容器规格归一 `service_config_container`(模板持引用)+ volumes/volumeMounts 分离 + **envFrom(secretRef/configMapRef)支持**;同值必同 deploy_ver(394 用例),读兼容+重放迁移 |
| 2026-08 | [诊断端点更名 /debug → /visualization](2026-08-visualization-endpoints-rename.md) | 对外名称去敏感化,行为零变化;模块/标识符/路由/测试/文档全量同步,306 用例通过 |
| 2026-08 | [Template 扩展 pod 落位字段 + PVC 同 claim 去重](2026-08-pod-placing-fields.md) | node_name/run_as_user/group(A 类,默认 None 指纹零漂移;联调决策 2026-08-29 确认,与 fs_group 回退不冲突)+ pvc_seen 跨容器共享卷;收尾补校验(≥0/hostname/空串归一)与 9 用例 |
| 2026-08 | [e2e 全量真实规格阶段(--with-mounts)](2026-08-e2e-full-mounts-stage.md) | 三挂载/PVC「已 Bound 复用缺失供给」/特权四件套逐字段断言;抓到真镜像 uid=1000 写 root 属主 PVC 被拒(OPEN)+ volumeName 不可变撞环境预置(已修);双真镜像门禁 104/105 |
| 2026-08 | [全量审计 → e2e 实锤 → 修复:16 项缺陷闭环](2026-08-27-audit-e2e-repro-fixes.md) | 五维审计 40 假设→16 条实锤用例(零回拨零改键)→12 组机制修复:等待/占位 deadline 化、暖池版本感知、409 日落按版本判定、探测参数随 Pod 烘焙、失败路径清孤儿、门禁契约参数 |
| 2026-08 | [Template 扩展 sidecars + _build_pod_body 多容器](2026-08-sidecar-containers.md) | 通用 sidecar JSON 列(首个用户 jiuwenbox)、默认 None+归一的指纹抹平(存量 deploy_ver 零变化)、SM fail-fast 校验/RM DeployFailed 冲突兜底、存量库先 ALTER 后发版 |
| 2026-08 | [routing_rules 改布尔表达式字符串](2026-08-routing-rules-expression-string.md) | 条件间任意 and/or+括号(固定「规则 OR·表达式 AND」作废)、递归下降解析、空串=通配 |
| 2026-08 | [scope 重构:config_sync 全量下发 + 规则化路由匹配 + 无请求预热](2026-08-scope-based-routing-config-sync.md) | scope 改下发制(index first-fit/规则 OR·表达式 AND/user_id 维度)、路由快照单键、config_sync 即预热 min_idle(规则格式已被表达式串取代) |
| 2026-08 | [生产可观测性:日志体系 + /visualization 诊断端点](2026-08-production-observability.md) | LOG_LEVEL 起效、请求关联、每请求一行汇总、框架降噪(6行/秒→0)、7 个只读诊断端点+脱敏 |
| 2026-08 | [网络/IO 抖动超时兜底](2026-08-network-io-timeout-hardening.md) | redis socket 5s/建连 3s+重试、MySQL 建连 5s、sweeper tick 上限,挂死循环不再静默 |
| 2026-08 | [M8 deploy 锁输家改 follower 等待室](2026-08-M8-deploy-lock-follower-waitroom.md) | 跨副本冷竞争零多余 Pod,冷启动尾延迟 30.5s→10.2s |
| 2026-08 | [Redis Cluster 兼容](2026-08-redis-cluster.md) | 键前缀 hash tag 同槽 + `redis+cluster://` 客户端 + EVAL 路由锚;真 cluster 11/11 验证 |
