# 对既有代码的改动清单

原则:**不改业务逻辑**。所有改动要么是"新增一种可选实现"(env 门控,默认行为
逐字节不变),要么是"配置规避"(零代码改动)。轻量版自有代码
(`jiuwenswarm_ee_lite` 包)不在本清单。

## agent_runtime(applications/agent_runtime,5 处 + 1 新文件)

| # | 文件 | 改动 | 默认行为影响 |
|---|---|---|---|
| 1 | `src/agent_runtime/config.py` | `_env_deploy_mode` 白名单 `full\|routing` → `full\|routing\|subprocess`(非法值仍启动 raise) | 无(env 不设仍 full) |
| 2 | `src/agent_runtime/resource_manager/models.py` | `PodDeployInfo` 新增 `published_port: int = 0`(subprocess 模式回传动态分配的实际 SSE 口;K8s 恒 0) | 无(带默认值) |
| 3 | `src/agent_runtime/resource_manager/orchestrator.py` | `_deploy_and_register` 拼 `pod_sse_url` 的 sse_port 改为 `info.published_port or main_sse_port(main)` | 无(K8s 模式 published_port 恒 0,回退原推导) |
| 4 | `src/agent_runtime/main.py` | `build_resources` local/server 两分支:`deploy_mode=subprocess` 时构造 `SubprocessPodClient`;`start()` 加 subprocess×mTLS-enforce fail-fast | 无(仅显式选 subprocess 时生效) |
| 5 | `src/agent_runtime/resource_manager/subprocess_client.py` | **新文件**:`SubprocessPodClient` 实现 K8sPodClient 契约(pod_spec→子进程,端口动态分配,每 Pod 独立工作区,进程树终止) | 无(纯新增) |
| 6 | `docs/spec/service-core.md`、`docs/spec/resource-manager.md`、`docs/feature/2026-11-subprocess-deploy-mode.md`(新)+ README 索引 | 文档同步义务 | 无 |

回归:全量 `uv run pytest -q` 658 通过 / 2 失败 / 9 跳过;2 个失败
(`test_link_material_sync`、`test_link_profile_runtime`)经 `git stash` 对照
确认为**存量环境失败**(OpenSSL 证书 AKI 校验,develop 原样即失败),本改动零回归。

## jiuwenclaw(E:\code\cyz95\jiuwenclaw,1 处)

| # | 文件 | 改动 | 默认行为影响 |
|---|---|---|---|
| 1 | `gateway/storage_assembly/setup.py` | `_create_ephemeral_factory` 企业版分支前插一段:显式 `GATEWAY_EPHEMERAL_BACKEND=memory` 时用内存后端(单机无 Redis);否则原样 Redis | 无(env 不设仍强制 Redis) |

其余全部**配置规避**(轻量版 launcher 注入,零代码改动):
- `GATEWAY_DB_HOST=127.0.0.1` — cron 工厂硬性检查非空,sqlite 下无语义
- `JIUWENSWARM_LINK_MTLS_MODE=off` — 本机回环明文(默认即 off,显式落字)
- `GATEWAY_CONFIG_POLL_ENABLED=false` — 单机无共享库可轮询
- `JIUWENSWARM_CLI_PORTS=1` — 防工作区 .env override 抢端口 env
- `GATEWAY_EPHEMERAL_BACKEND=memory` — 见上

## manager / identity_center:零改动

SQLite 双库、`REDIS_URL` 空(单机免锁心跳)、identity 播种账号,全是既有的
单机支持,launcher 注入 env 即可。

## e2e 前置知识(配置规避的原因实录)

- manager 建实例探活:gateway `GET /api/health`(企业版 WEB_PORT 统一应用上
  由 `register_manager_config_routes` 注册)、runtime `GET /healthz`、
  user_web `GET /`(→ 轻量版自带 userweb 占位进程应答)
- manager WS 8766 不存在(README 遗留描述),REST 8765 即全部
- gateway 首启后"可选通道配置"后台任务阻塞事件循环约 3 分钟,期间 HTTP
  全超时——属既有行为,launcher/e2e 以探活重试容忍
