# config_refresh 前置日落闸门(连续刷新 409 串行化)

- 日期:2026-09-12
- 涉及模块:session_manager / resource_manager / 测试(load_test)/ 文档

## 背景与动机

2026-09-11 wangchang 联调环境实测事故:测试侧 9 分钟内连续 4 次配置变更(SC=400/PC=200
→ max_pods=2),每次触发 gateway `config_refresh`,代次 gen 2→3→4→5 连续日落。
被日落的老代 Pod 在 reclaim 回收前(忙 Pod idle 时钟从日落转 idle 起算满 pod_ttl=180s)
始终计入 max_pods,多代日落堆积把仅有的 2 个槽位焊死——18:38:26–18:40:51 全量
route 503 `NO_POD_AVAILABLE`(≈2.5 分钟),正在进行的对话流被 `RetryableRouteError`
打断,报错经 gateway 落盘 `chat.error` 直达前端。

既有防线失明的根因:config_sync 的日落中间态守卫(`_sunset_pending_pods`)按
**deploy_ver** 判定,而 config_refresh 不改配置值、只 bump generation——上一轮
refresh 日落的老代 Pod 对该守卫不可见(此不可见是**钉死的设计**,
`test_config_sync_not_blocked_by_refresh_sunset_pods`:配置面不得因刷新残留长时间
409)。于是 refresh→refresh 方向完全无闸门,12 秒内连刷两次也畅通无阻。

## 方案

- **refresh 前置同款 pending 检查,判据改 generation**:`_config_refresh_locked`
  在任何 bump 之前逐 scope 调 `rm_facade.sunset_pending_pods(sid)`——scope 注册
  Pod(`scope:pods` = in_use ∪ idle)中 `generation ≠ 当前配置代次` 者(忙排空中
  与 idle 待回收一并计入,包容面与 sync 守卫一致;info 缺失的幽灵不计;两侧同为
  缺省视为一致——从未 refresh 过的 scope 零行为变化)。任一 scope 非空 → 409
  `CONFIG_SYNC_BUSY`,拒绝时 DB/Redis 零副作用。
- **判据必须是代次而非版本**(与 sync 守卫的分歧是设计使然):sync 换版本才需要
  排空旧版 Pod;refresh 每次都日落全部代次,只有代次能识别它的残留。
- **不会永久 409**:回收由 reclaim 的代次感知保证收敛(老代 idle 恒判 excess →
  aged ≥ pod_ttl → 回收);忙 Pod 由会话自然到期/reconcile 转 idle 后同径回收。
  被钉住会话的流量(持续 route/touch 续期)会拉长排空期——这正是"旧的 Pod 完成
  日落之前不允许新的下发"的串行语义本体。
- **被否掉的备选**:① 日落 Pod 跳过 pod_ttl 即时回收(老代已不接新流量)——单独
  施救不了忙 Pod,且改变 pod_ttl 语义;② max_pods 滚动期临时 +1 surge 槽——
  违背物理封顶语义(HLD 运营注意已钉死"不通过改 max_pods 口径排除老代");
  ③ refresh 内部等待排空完成再返回——会把共用的 `lock:config_sync` 持有数分钟,
  阻塞 config_sync 且撞锁看门狗上限。

## 实现

- `resource_manager/orchestrator.py`:新增 `sunset_pending_pods(scope_id)`——
  读 `scope:config.generation` 与逐 Pod `pod:info.generation` 比对(纯读,无新键
  无 Lua,Redis Cluster 兼容纪律不受影响)。
- `resource_manager/facade.py`:`sunset_pending_pods` 薄封装 + 模块头契约行。
- `session_manager/config_store.py`:`SunsetPendingPods` 回调类型 + 构造参数;
  `_config_refresh_locked` 重构为「悬挂 scope 过滤 → 前置闸门 → bump/push/ZREM」
  三段,闸门拒绝零副作用。
- `main.py` / `tests/conftest.py`:接线 `sunset_pending_pods=rm_facade.sunset_pending_pods`。
- `scripts/load_test.py`:refresh_controller 对 409 CONFIG_SYNC_BUSY 按**串行化背压**
  处理(warn 观测 + 可视化断言"代次冻结"不变式),不再判败。流量钉住会话时
  refresh/mixed 场景整个 run 可能仅首轮成功——多轮滚动生命周期由 R5 覆盖。

## 验证

- 单测:510 通过(508 → +2)。新增
  `test_config_refresh_rejects_when_sunset_pending`(闸门 409 + 零副作用四断言:
  代次冻结/候选集维持日落空态/锁未遗留/gen_bumps 仅一次)与
  `test_R5_refresh_serialized_until_sunset_done`(真实生命周期:立即再刷 409 →
  真等过 pod_ttl 自然回收 → 放行 gen 2)。R1–R4 零改动通过(R2 本就按
  "回收后再刷"的串行节奏构造)。
- 真环境:待发版后冒烟 + load_test refresh/mixed 场景回归(409 背压 warn 化后
  应 0 fail;多代堆积 NO_POD 白名单保留覆盖单代滚动窗口瞬态)。

## 影响面

- 文档:HLD §接口契约 config_refresh 行 + §场景 M-R 处理流程/守卫交互/运营注意;
  spec/session-manager.md config_refresh 编排伪码(⓪ 步);spec/e2e-test-cases.md
  §5.3 增 R5(4→5 用例);load_test 模块 docstring 与参数 help。
- 兼容性:无 schema/键/Lua 变更,无存量库升级步骤;行为变化仅"连续 refresh 在
  老代回收完成前得到 409"(此前会二次日落)——gateway/运维侧对此码已有处理
  (与 config_sync 共用锁的 409 同码)。
- 遗留:gateway 对 409 的自动退避重试策略(外层 jiuuwenclaw 仓库)不在本仓范围;
  单次 refresh 自身的滚动窗口(sunset + 冷启动占槽)仍存在,只是不再叠加放大。
