# 网络/IO 抖动超时兜底:redis socket 超时 / MySQL 建连超时 / tick 上限

- 日期:2026-08-24
- 涉及模块:service-core(组装)、service 框架(bootstrap/config/SystemContext/JobRunner)、foundation(db)

## 背景与动机

排查「db/redis 操作对网络抖动是否有超时兜底」发现:两个客户端构建点均未带任何 socket 级超时——

- `service/bootstrap.build_redis_client`:`redis.asyncio.from_url(url, decode_responses=False)`,redis-py 7.1.0 默认 `socket_timeout=None`/`socket_connect_timeout=None`(实测确认)→ TCP 半开/黑洞时任何一次 redis 命令 await **永久挂起**;
- `build_db_handler` → `MySQLHandler` 未传 connect_args,aiomysql 0.2.0 `connect_timeout=None`(实测确认)→ 建连/查询读均无上界。

不改的后果(按严重度):

1. **后台 sweeper 静默死亡**:`JobRunner._safe_tick` 直接 `await on_tick()` 无上限,挂起不抛异常(`except Exception` 捕不到),`_run_forever` 循环永久停摆、无日志;单副本下到期会话不再 evict(额度泄漏)、空 Pod 不转 idle、死 Pod 不清。
2. 请求路径最坏挂满请求级 deadline 70s(`OPENJIUWEN_SERVICE_REQUEST_TIMEOUT_SECONDS` 经 router `asyncio.timeout` 硬包,这是既有兜底,本次未动)。
3. MySQL:resolve 热路径缓存 miss 读 DB、config_sync 写链黑洞时挂到 70s。
4. 启动 readiness ping 无界:fail-fast 变 fail-hang,靠 K8s 探针失败重启兜底。

## 方案

定案要点:

1. **Redis 构建点注入**(一处改动覆盖全模块):`socket_connect_timeout=3` / `socket_timeout=5` / `health_check_interval=30` / `Retry(ExponentialBackoff, 3)` on `ConnectionError|TimeoutError`。全部 env 可调,**0=关闭该项**(恢复 redis-py 原生行为,逃生通道)。**socket_timeout=5 安全的论证**:本项目无 BLPOP 等长阻塞命令(等待全走 pubsub `get_message(timeout=...)` 有界轮询),Lua 均 O(小 key 集)、SCAN count=200、ZRANGEBYSCORE limit=1000,全在 ms 级。
2. **MySQL 建连超时**:dialect 感知注入 `connect_args["connect_timeout"]=5`(`RUNTIME_DB_CONNECT_TIMEOUT`/`DB_CONNECT_TIMEOUT` 可调,调用方显式值优先);`init_database` 的临时引擎同样补上。asyncpg 自带 60s 默认,不注入。查询读超时 aiomysql 无参数——由请求级 deadline 兜底,不另造轮子。
3. **tick 超时**:`JobRunner` 增 `tick_timeout_sec`(默认 None 维持旧行为),`wait_for` 包 `_invoke_on_tick`,超时取消本拍记 warning、下一拍重试;finally 放锁不变。agent_runtime 按 job 最重路径配值(`TICK_TIMEOUTS`):autoscale 370s(盖住 ready_timeout 300 + DEPLOY_LOCK_TTL 360,防误杀合法 deploy)。
4. **启动探活上限**:`SystemContext.start()` 的 ping/SELECT 1 包 `wait_for(10s)`,超时抛 TimeoutError(fail-fast 不 fail-hang);`readiness()` 同款,超时记 False。

被否/未做:

- 不给 redis 命令逐个包 `wait_for` —— 构建点 socket 超时已全局覆盖,逐调用包裹是噪声。
- 不引入 MySQL 查询级超时(aiomysql 无参数;MySQL 侧 `max_execution_time` 只管 SELECT,收益小)。
- kubernetes_asyncio 调用未加显式超时 —— aiohttp 默认 ClientTimeout(total=300s)已有上界,tick 超时再兜一层。

## 实现

| 文件 | 改动 |
|---|---|
| `service/.../service/config.py` | +4 字段(redis socket/connect/health_check/retry_attempts)+ env 表 + 校验(非负) |
| `service/.../service/bootstrap.py` | `build_redis_client` 注入超时/重试 kwargs |
| `foundation/.../db/engine_options.py` | `DEFAULT_CONNECT_TIMEOUT_SECONDS=5` + `get_connect_timeout()` |
| `foundation/.../db/sqlalchemy_handler.py` | mysql 系 dialect `connect_args.setdefault("connect_timeout", ...)` |
| `foundation/.../db/mysql_handler.py` | `init_database` 临时引擎带 connect_timeout |
| `service/.../periodic/runner.py` | `tick_timeout_sec` 参数 + `_safe_tick` wait_for 包裹(TimeoutError 先于 Exception 捕获;日志加 timed_out 字段) |
| `service/.../periodic/factory.py`、`system_context.py` | `create_single_leader_job` 透传 `tick_timeout_sec` |
| `service/.../context/system_context.py` | `_startup_check`(wait_for 10s)包 start() 五处探活;readiness() 同款 |
| `applications/agent_runtime/src/agent_runtime/main.py` | `TICK_TIMEOUTS` 常量表 + 5 个 job 传 `tick_timeout_sec` |
| `service/tests/unit_tests/test_network_timeouts.py` | 新增 10 用例 |

红线核对:tick 超时取消 autoscale 中途 deploy 的场景与既有 job stop cancel 路径同构(deploying 占位由闸门 deadline 兜底),不新增风险;错误路径双清(follower/deploy token)逻辑未动。

## 验证

- 单测:service `tests/unit_tests` **201 passed, 5 skipped**(新增 `test_network_timeouts.py` 10 例:默认注入/0 关闭/env 解析/负值拒绝、mysql 注入/显式优先/非 mysql 不注入、tick 超时中止挂死 tick 且放锁/None 不限制/正常 tick 不误杀);agent_runtime 全量 **114 passed**(行为无回归)。
- redis-py 7.1.0 / aiomysql 0.2.0 默认值均 `.venv` 实测确认(`socket_timeout=None`、`connect_timeout=None`);socket 超时抛 `redis.exceptions.TimeoutError`(重试类选型依据)。
- 真环境冒烟未跑(本改动影响连接建立与超时路径,集成冒烟建议随下次部署回归:`./scripts/integration_smoke.sh`)。

## 影响面

- 文档同步:`docs/spec/service-core.md`(jobs 表加 tick 超时列 + 「网络/IO 抖动超时兜底」节);框架 `service/config.py` env 表内嵌更新。
- 配置兼容:全部新 env 有默认值,不配即生效;local 模式(fakeredis/SQLite)不经此路径。同仓库其他走 `build_redis_client`/`MySQLHandler` 的应用一并获得兜底(行为变化:原先无限挂起的 IO 现在会抛超时异常)。
- 遗留:MySQL 查询读超时依赖请求级 deadline(后台任务无 DB 读,不受影响);K8s API 显式超时可作后续小改进。
