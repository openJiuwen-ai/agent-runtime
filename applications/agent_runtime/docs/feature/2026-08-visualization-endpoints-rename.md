# 诊断端点更名:/debug → /visualization(对外名称去敏感化)

- 日期:2026-08-29
- 涉及模块:service-core(visualization_api)/ 框架层注释 / 测试 / 文档
- 前作:[生产可观测性:日志体系 + /visualization 诊断端点](2026-08-production-observability.md)(端点的诞生地)

## 背景与动机

7 个只读诊断端点(`/debug/*`)对外暴露的 URL 前缀叫 debug 偏敏感:安全审阅/合规扫描
容易把「生产服务开着 debug 接口」当成风险项,而其实质是**只读的状态可视化数据源**
(实例总览/会话与 scope 池快照/配置/请求统计,全 GET、不写任何状态、输出脱敏)。
名称与实质不符,改名为体现「可视化」的前缀。

## 方案

**与需求方确认的决策**:

- 前缀选定 **`/visualization`**(「可视化」直译;候选 `/viz`/`/observability`/`/inspect`
  被否——长一点但语义直白优先)。
- **行为零变化**:路由实现、响应结构、错误面(503/400/404)、脱敏逻辑一律不动,
  仅换名字。
- **不留 /debug 兼容别名**:端点默认开放(靠网络边界,Service ClusterIP 仅集群内可达)、
  无外部调用方(部署/e2e/冒烟脚本均未引用),留别名则敏感名仍留在 OpenAPI 面上,
  违背改名初衷。

## 实现

- `debug_api.py` → **`visualization_api.py`**(git mv 保留历史);标识符全量跟进:
  `register_visualization_api`、`_visualization_endpoint`(包装器,仍不用 functools.wraps)、
  `_VisualizationNotFound`/`_VisualizationBadRequest`、logger
  `agent_runtime.debug` → `agent_runtime.visualization`(无按名配置引用,安全)。
- 7 条路由 `/debug/{overview,session,scope,scopes,config,stats,recent_errors}` →
  `/visualization/...`;OpenAPI summary 文案 `diagnostics: ...` 保持不变(描述功能,非敏感名)。
- 注释级引用同步:`main.py`(import/注册/两处注释)、`metrics.py` 模块头、
  `resource_manager/state.py` `health_fails` docstring、框架层
  `service/.../periodic/runner.py` 计数器注释。
- 测试:`tests/integration/test_debug_api.py` → `test_visualization_api.py`
  (14 处路径 + 用例名 `test_visualization_*`);`test_logsetup.py` 的 `redact` import 改路径。
- 历史 feature 文档中的路径引用同步改写(指向现行端点,免得照抄旧路径 404);
  新旧映射以本文件为准。

## 验证

- pytest:**306 通过**(用例数与改名前一致,仅文件/用例名变化,断言零改动)。
- 无 Redis 键 / Lua / env / 配置变化,不涉及 `verify_redis_cluster.py`。

## 影响面

- spec 同步:`service-core.md`(可观测性节标题、文件名、端点表 7 行、包装器名)、
  `session-manager.md`(诊断只读方法用途标注)、`resource-manager.md`(`health_fails` 用途标注)。
- **调用方注意**:若有脚本/看板引用旧 `/debug/*` 路径,须改为 `/visualization/*`
  (本仓库内无此类调用方)。
- 遗留:无。
