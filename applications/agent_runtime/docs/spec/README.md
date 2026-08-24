# agent-runtime 模块规格(spec)索引

- 读者:AI / 维护工程师。本目录回答「**代码在哪、怎么协作、改哪里**」。
- 设计论证与 Lua 全文在 `../design/`;每次改动的记录在 `../feature/`(改动时新建一份,规范见 `../feature/README.md`)。
- 语义冲突时以 `../design/Agent-Runtime-HLD.md` 为准。

## 文档地图

| 文档 | 内容 | 何时读 |
|---|---|---|
| 本文件 | 架构一页纸 + 键前缀总览 + 测试/部署入口 | 先读 |
| [service-core.md](service-core.md) | 组装(main)/CLI/配置(`AGENT_RUNTIME_*`)/错误码契约/字段分类/工具/部署 | 改装配、配置、错误契约、部署时 |
| [session-manager.md](session-manager.md) | SM:route/touch/config_sync/cleanup 编排、7 个 Lua、SM 键表 | 改会话编排/配置层时 |
| [resource-manager.md](resource-manager.md) | RM:acquire/后台任务/K8s 适配、6 个 Lua、RM 键表 | 改 Pod 池/扩缩容/清理时 |
| [e2e-test-cases.md](e2e-test-cases.md) | 全部 e2e 用例的场景/输入/预期输出 | 写或跑 e2e 时 |
| `../design/Agent-Runtime-HLD.md` | 架构总览/接口契约/场景 A–N/Redis 键表(语义权威) | 语义不确定时 |
| `../design/session-manager-design.md` | SM 详细设计(7 个 Lua 全文) | 深挖 SM 设计动机 |
| `../design/resource-manager-design.md` | RM 详细设计(6 个 Lua 全文) | 深挖 RM 设计动机 |

## 一页纸架构

一个进程、一个 App(`/api/session`,端口 8091)、两个模块:

```
gateway ──route/touch──► agent-runtime(uvicorn)
claw mgr ──config_sync──►   ├─ session_manager   持 App,4 个 HTTP handler
运维    ──cleanup──────►    └─ resource_manager  无 App,纯 Facade + 后台任务
                            两模块共享同一 Redis(前缀隔离)+ 同一 DB,互调只走进程内 Facade
数据面(本服务全程旁路):gateway ◄──SSE──► AgentServer Pod(route 返回 pod_sse_url)
```

- 状态分层:**编排态在 Redis**(键前缀见下)、**配置在 DB**(`service_config_template` / `routing_rule` 表)、**Pod 物理态以 K8s 为唯一真相源**。
- 多副本无状态;后台任务经 Redis 选主锁(`agent_runtime:job:*`)全局单副本执行写操作。
- 所有编排态变更走 Lua(EVAL 原子);脚本不传 KEYS,`ARGV[1]` 为键前缀,键名在脚本内拼——调用统一经各模块 `state.py` 的 `eval()`。

## Redis 键前缀总览(逐键明细见各模块 spec)

```
session_manager:…    SM 编排态(会话四处/scope 闸门/等待队列/候选集/注册表)
resource_manager:…   RM 编排态(per-scope Pod 池/idle 暖池/deploy 占位/follower 等待室/选主锁)
agent_runtime:job:…  后台任务选主锁(main.py:_build_jobs 注册)
```

## 测试与部署(速查)

```bash
cd applications/agent_runtime
uv sync --extra local && uv run pytest   # 114 用例(fakeredis+SQLite+FakeK8s)
./scripts/integration_smoke.sh           # 真环境冒烟(场景 A–L;FLUSHDB 目标库,有防误刷)
./scripts/deploy_replicas.sh 2 .env.production.local 8091   # 宿主机双进程
./deploy/render_and_apply.sh deploy/agent_runtime.env --nodeport  # K8s 生产形态
```

用例分层表、多副本 e2e、压测入口与全部前置红线见 `e2e-test-cases.md` 与 `service-core.md` §部署。
