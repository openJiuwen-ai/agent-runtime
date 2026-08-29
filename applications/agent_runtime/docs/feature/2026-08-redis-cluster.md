# Redis Cluster 兼容:键前缀 hash tag 化 + 集群客户端接入

- 日期:2026-08-29
- 里程碑:M0–M8 后维护期
- 涉及模块:session_manager / resource_manager / service-core(service 框架) / 测试 / e2e 脚本 / 文档

## 背景与动机

企业客户生产环境使用 **Redis Cluster**(jcl-dev 环境先行接入)。2026-08-28 jcl-dev 日志
(`pod_logs_jcl-dev_20260828_173625.zip`,66 秒窗口)实录两类故障,**服务完全不可用**:

1. `RedisClusterException: EVAL - all keys must map to the same key slot` ×207 次——
   选主抽签 Lua 双键(`winner:{epoch}` / `candidates:{epoch}`)CRC16 落不同槽,
   redis-py 集群客户端**在客户端侧直接拒绝**。5 个后台任务(sm_sweep / rm_reclaim /
   rm_autoscale / rm_watch / rm_reconcile)**每一拍全灭**:会话清扫、资源回收、
   死亡探测、扩缩容全部停摆。
2. `ResponseError: Script attempted to access a non local key` ×6 次——SM/RM 全部
   Lua 以 `numkeys=0` 调用(键名在脚本内由 `ARGV[1]` prefix 拼出),集群客户端把
   EVAL 路由到随机节点,脚本摸到非归属节点的键即被服务端拒绝。
   `POST /api/session/route` **6/6 全部 500**,路由面 100% 失败。

根因:`session_manager/lua_scripts.py` 文件头自述的设计假设——"脚本不传 KEYS
(键在脚本内由 prefix 动态拼出;**单实例 Redis 无 cluster 限制**)"。整个状态层的
原子性建立在"所有键在同一个 Redis 进程里"之上。

## 方案

定案要点(与需求方确认过:企业生产即 cluster,必须原生支持;自带单实例 Redis 的
workaround 被否——多一个客户环境要审批/扫描/运维的组件,且把不支持变成永久约束):

1. **键前缀 hash tag 化**:`SM_KEY_PREFIX = "{session_manager}"`、
   `RM_KEY_PREFIX = "{resource_manager}"`(config.py + 两模块 state.KEY_PREFIX)。
   `{}` 在 cluster 下参与槽位计算 → 模块**全部键(跨 scope/跨 Pod/全局 ZSET)同槽**,
   多键 Lua 原子性保持;单实例/哨兵/fakeredis 下 `{}` 无语义,**同一套键名兼容两种部署**。
   代价:模块键域钉在单分片——cluster 只提供 HA 不提供横向扩展(控制面数据量小,可接受)。
2. **选主键框架级同槽**:`SingleLeaderCoordinator` 的 candidates/winner 键改为
   `{lock_key}:candidates/{epoch}` 形态(框架修复,任意调用方受益);执行锁键
   `agent_runtime:job:<job>` 保持原样(单键操作无需同槽)。
3. **`state.eval` 声明路由锚**:`eval(script, 1, prefix, prefix, *args)`——KEYS[1]
   声明 prefix 使集群客户端把 EVAL 路由到 tag 归属节点(Lua 本体零改动,脚本内仍取
   ARGV[1])。**关键实测**:只做 1 不做 3 不够——`numkeys=0` 随机路由,30 次实验
   20 次报 "non local key"(与生产日志同款);声明 KEYS[1] 后全过。
4. **集群客户端构造**:`OPENJIUWEN_SERVICE_REDIS_URL` 用 `redis+cluster://`
   (TLS:`rediss+cluster://`)scheme → `RedisCluster.from_url`(scheme 归一化;
   一种子节点即可,拓扑自发现)。cluster 只有 db 0,**URL 带库号在构建点快速失败**。
   吃掉 jcl-dev 镜像里那份未进仓库的 cluster 客户端私改补丁。
5. **SCAN 游标兼容**:集群客户端 SCAN 默认扫全部主节点,游标返回 `{节点: 游标}`
   dict(单实例/fakeredis 返回 int)——`ResourceState.known_scope_ids` 兼容两种
   形态(旧代码 `to_int(cursor)==0` 对 dict 恒真,会在大 scope 数下提前截断)。
6. **标识符防注入**:`util.key_unsafe`——scope_id/session_id 等进键名的标识符禁含
   `{`/`}`(否则截断第一对花括号的 tag 定槽,该标识符的键被甩到别的 slot)。
   入口:orchestrator.route/touch 拒绝(InvalidParams);config_store 行解析按坏行
   跳过(写路径本有 `^[0-9A-Za-z._-]{1,128}$` 白名单,此处防手改 DB)。

被否掉的备选:① 重写 Lua 为单键命令(闸门/first-fit/四处同删的原子性全丢,贵一个
数量级);② 前面挂 proxy(twemproxy/predixy 同样按槽路由,不解决跨槽语义);
③ 运行时按"是否 cluster"切换键名(同一部署切形态时存量数据不可见,迁移噩梦)。

## 实现

- `applications/agent_runtime/src/agent_runtime/config.py`:两前缀常量加 tag
- `session_manager/state.py` / `resource_manager/state.py`:KEY_PREFIX、模块 docstring、
  `eval()` 路由锚、`known_scope_ids` 游标兼容
- `session_manager/lua_scripts.py` / `resource_manager/lua_scripts.py`:头注释改述
  (删除"单实例无 cluster 限制"的错误假设)
- `session_manager/routing.py` docstring 键名示例
- `service/openjiuwen_runtime/service/bootstrap.py`:`build_redis_client` scheme 分支
  + `_build_redis_cluster_client`
- `service/openjiuwen_runtime/service/context/periodic/coordinator/single_leader.py`:
  candidates/winner 键构造加 tag
- `session_manager/orchestrator.py`(route/touch 校验)、`config_store.py`(行解析)、
  `util.py`(`key_unsafe`)
- 测试:`test_multi_replica`/`test_route_flow` 字面键名改引常量;`_dual_harness`
  选主采样 pattern 适配;新增 3 个用例(key_unsafe / route+touch 拒花括号 /
  坏 scope 行跳过)
- e2e:`e2e_lib.py`(`SM_PREFIX`/`RM_PREFIX` 共享常量、OWN_PREFIXES 白名单、
  选主采样 pattern 与 job 解析)、`e2e_hld_acceptance.py` / `e2e_multi_replica.py`
  键名引用改常量
- 新增 `scripts/verify_redis_cluster.py`(真 cluster 验证脚本,见下)

## 验证

- **fakeredis 全量**:295 passed(原 293 + 新增 2 个文件级用例;route/touch 花括号
  用例扩展在既有用例内)。
- **真 Redis Cluster**(docker 3 主,16384 槽全覆盖,redis 7.2):
  `scripts/verify_redis_cluster.py` **11/11 通过,两种子节点复跑一致**——
  环境自证(旧式双键 EVAL 必炸,防假绿)/ bootstrap 构造+库号拒绝 / 选主 try_claim /
  SM route_place(need_acquire→placed)/ touch / evict / 等待队列 / RM acquire /
  known_scope_ids(dict 游标)。
- 路由锚实验:numkeys=0 随机路由 30 次 20 错("non local key",生产同款);
  KEYS[1] 声明后 0 错。
- 上线待办:jcl-dev / 企业环境接真 cluster 跑 `integration_smoke.sh` 全量(需真
  AgentServer 镜像三件套契约参数);**键前缀变更,存量 Redis 数据不兼容,切换时
  必须清库(会话亲和/暖池状态归零重建)**。

## 部署注意

- URL 换 `redis+cluster://host:port`(**不能带 `/N` 库号**);密码仍走 Secret
  envFrom(`REDIS_PASSWORD`)注入。
- 同槽=单分片:共用 cluster 时该分片承载全部 SM/RM 控制面键(数据量小、QPS 低,
  常规无碍,可向 cluster 方知会)。
- PUBLISH 在 cluster 为全节点广播,`scope:{scope}:free` 等待-唤醒功能不变。
