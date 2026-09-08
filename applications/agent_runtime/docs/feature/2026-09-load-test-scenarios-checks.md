# 压测脚本重设计:6 场景 + 可视化/ERROR 日志判定层

- 日期:2026-09-08
- 涉及模块:测试(scripts/load_test.py;不改任何服务代码)

## 背景与动机

原 load_test.py 只有 3 个场景(route/route_touch/queued)、3 类判定(延迟分位数/错误码直方图/传输层错误率退出码),两个缺口:①配置面行为(热更新 config_sync、强制刷新 config_refresh)完全没有压力覆盖;②判定全在客户端视角,服务侧可视化接口(/visualization/*)与 ERROR 日志未被利用——问题要到人翻日志才发现。用户要求:补齐配置面场景,判定至少要能"感知 ERROR 日志"。

## 方案

定案要点:

1. **场景 3→6**:保留 route/route_touch/queued(CLI 兼容);新增 `config_churn`(流量中周期 config_sync 翻转 B 类参数 sc/pod_ttl 两态)、`config_refresh`(流量中周期强制刷新:代次单调+1、存量会话亲和、新代暖 Pod 重建收敛、冷/暖启动探测)、`mixed`(route_touch + churn + refresh 同场,两控制器共享本地 `config_plane_lock`——服务端 config 面本就串行锁,并发即 409,工具内单发射者不自造 409)。
2. **判定层与场景正交**:CheckRecorder 三级(fail 计退出码/warn 仅报告/skip 前置不满足);场景内断言 + 收尾巡检(`post_run_sweep`:静息 deploying 收敛、recent_errors 多实例聚合无白名单外码、stats 端点错误码交叉验证(warn)、自评估无 critical findings(warn))全走只读可视化端点。
3. **ERROR 日志感知**:run 开始打快照(文件字节偏移 / kubectl `--since-time`)、结束扫增量,按 `" - ERROR - "` 行计数,超 `--log-error-max`(默认 0)即退出码 1。依据:业务失败(503/409)只打 INFO 汇总,ERROR 行=真异常,默认阈值 0 作硬门安全。日志源 `--log-source auto|file|kubectl|none`:auto=--log-file > `.replicas.json` 反查 `logs/replica-<port>.log`(宿主机 deploy_replicas 形态,精确)> kubectl(K8s 形态,尽力——Pod 重启丢日志)。**只读 kubectl logs 是工具唯一的非 HTTP 例外**,红线其余不变(不碰 Redis/DB 写面、无 FLUSHDB、不动 cleanup 端点)。
4. **容量语义不判缺陷**:mixed 白名单 `503/NO_POD_AVAILABLE`、冷启动探测 503 记 warn——实测确认连续 refresh 的多代 Pod 在 pod_ttl 回收前堆积,叠加存量会话重放置会撞 max_pods=⌈sc/pc⌉ 硬闸门(recent_errors detail:"reached max_pods=N"),这是容量保护而非缺陷;出现记 WARN 观测占比,其它错误码仍 fail。
5. **版本容错**:`/visualization/scope` 的 `sm.capacity` 字段(2026-09 观测补齐)在旧 runtime 上不存在——探测到缺失时一次性 warn 并回退 `/visualization/config` 模板参数(DB 视角)断言传播,不静默。

被否方案:

- **主动亲和探测**(刷新后专门 route 一组探针会话):零增益——worker 持续 route 固定会话集已是天然探针,被动记录 `route` 响应里的 pod_id 即可,不额外造流量。
- **kubectl `-p`(previous 容器)补重启前日志**:与 `--since-time` 组合语义脆弱且多 Pod 下不可靠,不补;重启丢日志以文档明示,kubectl 源定位为尽力源。
- **收尾巡检查 waiters 归零**:2026-09 快失败改造后等待队列已拆,vis 响应无 waiters 字段——改为 deploying 收敛。
- **stats/recent_errors 做硬门**:两者是实例视角(LB 后单次 GET 只见一个副本)+ stats 有 5s 批量 flush 滞后,做不成精确门;多采样按 instance_id 聚合后只给 warn 交叉验证,硬门放在客户端精确计数(unexpected_errors)与 ERROR 日志上。
- **import e2e_lib 复用 Client/kubectl**:e2e_lib 模块级 import redis+agent_runtime,破坏"单文件零依赖可分发"定位;自抄 8 行 kubectl/wait_until。
- **jitter 窗口罩传播轮询期(+8s 尾)**:sync 全部动作在响应前完成(锁内写库+推 RM),长尾巴会把整个 run 摊进窗口、基线恒空;窗口收窄为请求本身(−1s/+2s)。

## 实现

`scripts/load_test.py` 单文件重写(~700 行,httpx+标准库),分节:参数/信封载荷/HTTP 基础/统计(Stats 加 ts+endpoints 平行数组→`snapshot_window`/`snapshot_complement`/`unexpected_errors`;AffinityTracker 索引 checkpoint)/限速/VisClient(get+poll+recent_errors 多实例聚合)/CheckRecorder/日志(FileLogSource 字节偏移增量、文件变小从头扫并标注;KubectlLogSource 只读)/churn+refresh 控制器(共享 asyncio.Lock)/worker(成功 route 喂 affinity;`_pick_msg_type` 抽出)/收尾巡检/主流程。

场景播种差异化:queued=小容量(sc/pc 可调);route/route_touch=50/10;config_churn=40/10、session_ttl=600(防长 run 会话 TTL 过期造成亲和误报);config_refresh/mixed=40/**4**(max_pods=10,降低连续刷新时无意义撞顶)+min_idle_pods=1(否则新代暖 Pod 只被新会话触发,重建收敛断言不可靠)。`--json` 输出扩 checks/error_logs/config_events/affinity_violations/cold_start 段;退出码 0/1(任一 fail 或传输层错误率)/2(环境不可用)。

配套:`--namespace` 默认值 `agent-runtime-e2e` → `agent-runtime-e2e-wmq`(对齐 e2e 环境真实 ns;显式传参不受影响)。

## 验证

- `python -m py_compile` 通过;`--help` 新参数齐全;对不可达目标干跑干净退出码 2(无异常栈)。
- K8s e2e 环境(NodePort 30091,双副本 LB,runtime 镜像 agent-runtime:scopefull-20260904)实测:
  - `config_churn` 45s:**23 pass / 0 fail**,4 次 sync(44–90ms)全 200+affected 覆盖,传播断言经回退路径 30s 内确认 sc/pod_ttl 新值,快照 ver 每次递增,流量 p50=5.7ms p99=10.7ms(17.5k 请求),巡检+ERROR 日志(0 条)全过。
  - `config_refresh` 120s:**20 pass / 0 fail**,2 次刷新代次 0→1→2 单调(响应与 vis 双源一致),重建收敛 ≤120s,暖探测 200,冷启动 4/4 成功(p50=3.3s max=8.2s),**存量会话亲和 0 违规**,5.4 万请求 p99=10.1ms,ERROR 日志 0。
  - `mixed` 180s(churn 12s + refresh 60s + route_touch,groups=2):**77 pass / 0 fail / 1 warn**;14 次 sync(43–129ms)+ 2 次 refresh(9–13ms)全程**零 409**(本地单发射者锁生效,同时实测了"刷新排空期内 B 类 sync 放行");代次 0→1→2 单调、重建收敛、暖探测 200、冷启动 4/4 成功(6ms–10s);11.6 万请求 p50=5.3ms p99=9.4ms;9 次 503/NO_POD_AVAILABLE(0.01%,max_pods 容量语义)按 WARN 观测;ERROR 日志 0。`affinity_violations` 全量记录 1 次换 pod(rebuild 等待期老 Pod 被 pod_ttl 回收后会话重放置,检查窗口外,属正常回收行为——JSON 全量字段提供可见性)。
  - `queued` 25s 回归:**6 pass / 0 fail / 2 warn**;552rps 下 82% 请求被快失败(SCOPE_FULL 55.9% + NO_POD_AVAILABLE 26.5%——后者为快失败改造后的超限粗化码,白名单补充),p99=70.5ms;ERROR 日志 0。
- 发现的环境事实:e2e 部署镜像落后一个特性(b2b92604,无 559d62db 的 sm.capacity/history/evaluation 字段)→ 触发回退路径并被 warn 提示;自评估巡检在旧镜像上 404→skip(设计内)。

## 影响面

- 文档:本篇 + `docs/feature/README.md` 索引 + 模块 CLAUDE.md 多副本节压测注释行;`docs/api/config-plane-api.md` 无需改(未动接口)。
- 兼容性:旧场景 CLI 全兼容;`--namespace`/`--log-namespace` 默认值变更(前者对齐 e2e-wmq,后者=runtime 服务 ns agent-runtime-e2e——AgentServer Pod 的 ns 与 runtime 服务 ns 是两个东西)。
- 开放问题:①e2e 部署镜像待更新到当前 develop 以启用 capacity/history/evaluation 断言 canonical 路径(需向另一节点分发镜像);②连续刷新多代 Pod 堆积撞 max_pods 的窗口期(≈pod_ttl)内新放置 503 是否需要产品侧缓解(如 max_pods 计数排除待回收老代),已作为容量语义观测,留产品决策。
