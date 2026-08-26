# PostgreSQL 接入服务框架 + asyncpg 超时兜底

- 日期:2026-08-25
- 涉及模块:框架层(foundation/db + service/config + service/bootstrap)/ 测试 / 文档

## 背景与动机

上一轮网络/IO 抖动兜底只覆盖了 MySQL(aiomysql 建连 5s)。审阅发现:foundation 早有
`PostgreSQLHandler`(asyncpg 驱动,含 init_database/建库建 schema),但**服务框架走不到**——
`build_db_handler` 只接 `mysql|sqlite|none`,`ServiceConfig` 的 db_type 校验也不含 postgresql。
同时 asyncpg 默认建连超时 60s、`command_timeout` **无限制**(与 aiomysql「建连无限制」同级别的
挂死隐患,慢查询可永久占住请求)。

## 方案

- 接入:`ServiceConfig` 放行 `postgresql`(必填校验同 mysql:host/name/user;端口默认随类型
  mysql 3306 / postgresql 5432,显式设置优先);`build_db_handler` 加分支;`PostgreSQLHandler`
  补进 `foundation.db` 导出表。
- 超时兜底(与 MySQL 对齐):`sqlalchemy_handler.connect()` 对 `asyncpg` 驱动注入
  `timeout=RUNTIME_DB_CONNECT_TIMEOUT`(默认 5s,复用 MySQL 旋钮)与
  `command_timeout=RUNTIME_DB_COMMAND_TIMEOUT`(新旋钮,默认 30s——远大于业务合法查询、
  低于 70s 请求级 deadline);调用方显式 connect_args 优先(setdefault)。
  `init_database` 的临时引擎(CREATE DATABASE/SCHEMA,启动路径无请求级 deadline)同款注入。
- 驱动判定用 `get_driver_name()=="asyncpg"` 而非 backend 名——不影响 gaussdb+async_gaussdb
  (其 connect 签名未验证,不冒进)。

## 验证

- 框架单测 207 passed 5 skipped(新增:engine kwargs 注入/显式优先/sqlite 不注入/
  build_db_handler 构造/必填校验/默认端口;原「PG 不注入」断言更新为新语义);
  应用测试 129 passed(存量零改动)。
- **真环境(集群 PG 16)**:server extra 补 asyncpg 后构建 `agent-runtime:pg-20260825`
  部署,`AGENT_RUNTIME_DB_TYPE=postgresql` 全链路就绪(readiness db:true、框架自动
  建表 service_config_template/routing_rule);集成冒烟阶段 1–9(种子/route/场景
  A–L 前段)全部通过。阶段 10 起中断系集群存储故障(nfs-server Evicted → PG WAL
  fdatasync PANIC),与代码无关,存储恢复后复跑即可。
- 冒烟脚本同步支持 PG:`--db-type postgresql`(psql 落库校验 + clean_previous
  清种子行——PG 唯一约束下重跑必须清表,MySQL 靠 TRUNCATE 的既有语义补齐 PG 侧)。

## 影响面

- spec service-core.md 网络/IO 节与部署红线同步;main.py fail-fast 文案补 postgresql。
- agent-runtime 切 PG:K8s 部署改 `deploy/agent_runtime.env` 的 `AGENT_RUNTIME_DB_TYPE=postgresql`
  (连接参数同组 `AGENT_RUNTIME_DB_*`,端口 5432 可省)后重新渲染;宿主机模式改
  `OPENJIUWEN_SERVICE_DB_TYPE=postgresql` + DB_HOST/NAME/USER/PASSWORD,旋钮与 MySQL 共用。
