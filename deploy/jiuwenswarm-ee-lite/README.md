# jiuwenswarm-ee-lite

jiuwenswarm 企业版**轻量单机部署**：无 Docker、无 K8s、无外部 Redis/MySQL，
四个服务全部以**本机进程**拉起，AgentServer 由 runtime 按 Pod 语义**按需起子进程**。

```
                       ┌───────────────────────────────┐
  管理面(admin) ──────▶│ manager-server :8765 (sqlite) │──── 配置推送(HTTP) ────┐
                       └──────┬────────────────────────┘                        │
                              │ JWT 验签(公钥)                                   ▼
                       ┌──────▼──────────┐                            ┌──────────────────┐
                       │ identity :8770  │                            │ gateway :19000   │
                       └─────────────────┘                            │ (enterprise,ws+http)│
                                                                      └───────┬──────────┘
        用户面消息(WS /ws) ────────────────────────────────────────────────────┤
                                                                              ▼
                                                              ┌──────────────────────────┐
                                                              │ agent-runtime :8091      │
                                                              │ mode=local, deploy=      │
                                                              │ subprocess (fakeredis+   │
                                                              │ sqlite)                  │
                                                              └───────┬──────────────────┘
                                                                      │ deploy(pod_spec)→进程
                                                              ┌───────▼──────────┐
                                                              │ agentserver 子进程│
                                                              │ (每 Pod 独立端口/ │
                                                              │  独立工作区)      │
                                                              └──────────────────┘
```

## 快速开始

```bash
# 1) 依赖安装(uv,Python 3.11–3.13)
uv sync                     # 本地 editable 源(四个 git 依赖指到本地仓库)
# 按纯 git 地址安装: uv sync --no-sources

# 2) 启动全栈(identity → manager → runtime → gateway 按序就绪)
uv run jiuwenswarm-lite start

# 3) 查看状态 / 停止
uv run jiuwenswarm-lite status                # 单次:常驻服务表(含前端进程) + agentserver 进程表
uv run jiuwenswarm-lite status --watch        # 持续:每秒重新查询并原地重绘(清理上次输出),Ctrl+C 退出
uv run jiuwenswarm-lite stop

# 4) e2e(管理面配置下发 + 用户面聊天两条链路)
uv run pytest tests/e2e -v

# 5) 前端(可选):管理面前端 :19003 + 用户面前端 :19012
#    需 Node.js(≥18);管理面前端用外层仓源码原地构建,用户面前端首次构建时
#    自动 git clone jiuwenclaw 到 resources/jiuwenswarm(地址/分支见配置参考)
#    start=每次重新构建→拉起
uv run jiuwenswarm-lite frontend start
uv run jiuwenswarm-lite frontend stop     # 停止前端进程
#    分步: frontend build [--force] / frontend status
```

### 前端(`jiuwenswarm-lite frontend`)

源码位置原地构建,不做目录复制、不做缓存布局:

| 前端 | 源码位置(原地构建) | 端口 | 生产入口(与 K8s 镜像同语义,免 nginx) |
|---|---|---|---|
| 管理面 manager-web | `<外层仓>/applications/manager/manager_web` | 19003 | `python -m manager_server.manager_web`:/api→manager /idp→identity /chat→用户面前端 /gateway-api,/ws→gateway |
| 用户面 user-web | `<本仓>/resources/jiuwenswarm/jiuwenswarm/channels/web/frontend`(jiuwenclaw 克隆,**build 时自动 git clone**,见下) | 19012 | `python -m jiuwenswarm.channels.web.app_web`:/api,/ws→gateway(语义对齐 docker/web.nginx.conf.template) |

- 用户面前端克隆:`frontend build` 发现 `resources/jiuwenswarm` 缺失时自动执行
  `git clone --depth 1 -b <分支> <地址>`,地址/分支由 `LITE_JIUWENCLAW_URL` /
  `LITE_JIUWENCLAW_BRANCH` 配置(默认 cyz95 fork 的 develop,可写在项目根 `.env`);
  克隆注入 `GIT_LFS_SKIP_SMUDGE=1`(jiuwenclaw 远端缺视频 LFS 对象,正常克隆会失败,
  跳过后视频留指针文件不影响前端);已存在时跳过,不自动 pull——更新删目录重跑。
- 构建直接发生在源码目录,`node_modules/`、`dist/` 留在源码处;`frontend build` 缺省 dist 已存在跳过,`--force` 重建。
- 用户面前端的 vite 配置加载期解析包内 `../../../scripts` 兄弟目录(即 `resources/jiuwenswarm/jiuwenswarm/scripts`),克隆天然包含,缺失时 build 前有预检报错。
- 用户面前端进程工作区独立:`data/user_web_workspace/`(防与 gateway 工作区互踩)。
- 进程簿记独立:`data/frontend.pid.json`——核心 `start/stop/status/env` 五命令行为完全不变。
- 核心五服务未启动时先 `uv run jiuwenswarm-lite start` 再 `frontend start`。

## 服务拓扑与端口

| 服务 | 模块 | 默认端口 | 数据 | 职责 |
|---|---|---|---|---|
| identity-center | `applications/manager/identity_center` | 8770 | `data/identity.db` | RS256 JWT 签发；播种 admin/user1 |
| user-web 占位 | 本仓 `jiuwenswarm_ee_lite/userweb.py` | 19002 | 无 | 轻量版不含用户面前端;仅满足 manager 建实例的 `GET /` 探活 |
| manager-server | `applications/manager/manager_server` | 8765 | `data/manager.db` | 管理面 API；配置推送(gateway/runtime) |
| agent-runtime | `applications/agent_runtime` | 8091 | `data/agent_runtime.db` | 会话编排；`mode=local` + `deploy_mode=subprocess` |
| gateway | jiuwenclaw `gateway/` | 19000 (Web) / 19001 (TUI) | `data/gateway.db` + 工作区 | 用户消息入口(WS `/ws`)；企业版配置落库 |
| agentserver ×N | jiuwenclaw `jiuwenswarm/server/` | 动态分配 | `data/agentserver_pods/<pod>/` | runtime 子进程拉起，每 Pod 独立端口+工作区 |

> **agentserver 进程是瞬态的,不在 launcher 管辖**:它由 runtime 按聊天路由按需拉起(pod 语义),空闲约 60s 后被 runtime sweeper 回收(`min_idle_pods=0` 时必回收),工作区目录保留作排障——进程没了不代表没拉起过。`jiuwenswarm-lite status` 底部第二张表实时感知这些子进程(进程数/pod 数/端口/启动时刻);感知原则:cmdline `-m jiuwenswarm.server.app_agentserver` + env `JIUWENSWARM_DATA_DIR=<pods_root>/pods/<pod_id>` 双重匹配(SubprocessPodClient 注入的确定性标记,精确归属到 pod,零误报),见 `src/jiuwenswarm_ee_lite/pods.py`。
>
> **同 pod 两行(role 列 `main`/`shim`)是 Windows venv 正常形态,不是两个 agentserver**:`.venv\Scripts\python.exe` 是启动器壳(venvlauncher,拉起真正的解释器后原地等待),真实解释器的 `exe()` 是基础 Python 路径——据此标注,`main` 行才是干活的进程。另注:架构上每个 agentserver 还是 front/worker 双进程(front 监听端口跑控制面,worker 经本地 IPC 承载 OpenJiuwen runtime,见 `jiuwenswarm/server/app_agentserver.py`);表里匹配的是 front 层。

## 与 K8s 版(deploy/jiuwenswarm-ee)的差异

| 维度 | K8s 版 | 轻量版 |
|---|---|---|
| AgentServer 供给 | Pod(镜像) | 本机子进程(`AGENT_RUNTIME_DEPLOY_MODE=subprocess`) |
| Redis | 真 Redis | runtime=fakeredis；manager/gateway 免 Redis 内存态 |
| MySQL/PG | MySQL/PG | SQLite(`IDENTITY_/MANAGER_/GATEWAY_SQLITE_PATH`) |
| 服务进程 | 容器编排 | `jiuwenswarm-lite` 进程管理(pid 簿记+就绪探针) |
| mTLS 链路 | observe/enforce | off(本机回环，`JIUWENSWARM_LINK_MTLS_MODE=off`) |
| 配置下发 | manager→gateway HTTP(同) | 同;`GATEWAY_CONFIG_POLL_ENABLED=false`(无共享库可轮询) |

业务代码零改动即可单机跑：runtime 侧仅新增 `subprocess` 部署模式
(`applications/agent_runtime/src/agent_runtime/resource_manager/subprocess_client.py`),
pod_spec 容器规范形翻译为子进程命令+env，对上层(orchestrator/sweeper)
完整实现 `K8sPodClient` 契约。jiuwenclaw 侧唯一一处 env 门控分支：
`gateway/storage_assembly/setup.py` 认 `GATEWAY_EPHEMERAL_BACKEND=memory`
(企业版 ephemeral 存储默认仍走 Redis,显式指 memory 才降级,默认行为不变)。
其余均为**配置规避**:`GATEWAY_DB_HOST=127.0.0.1`(cron 工厂硬性检查,sqlite
下无语义)、`JIUWENSWARM_LINK_MTLS_MODE=off`(本机回环明文)、
`GATEWAY_CONFIG_POLL_ENABLED=false`(无共享库可轮询)。

## 配置参考

轻量版自身的配置(均有缺省)：

| 环境变量 | 默认 | 说明 |
|---|---|---|
| `JIUWENSWARM_EE_LITE_HOME` | `./data` | 数据根(DB/日志/工作区) |
| `LITE_IDENTITY_PORT` | 8770 | 身份中心端口 |
| `LITE_MANAGER_PORT` | 8765 | 管理面 REST 端口 |
| `LITE_RUNTIME_PORT` | 8091 | runtime 编排端口 |
| `LITE_GATEWAY_WEB_PORT` | 19000 | gateway Web 端口 |
| `LITE_GATEWAY_TUI_PORT` | 19001 | gateway TUI/ACP 端口 |
| `LITE_USER_WEB_PORT` | 19002 | 用户面占位页端口 |
| `LITE_ADMIN_PASSWORD` | admin123 | 播种管理员 admin 密码 |
| `LITE_USER1_PASSWORD` | user1pass | 播种用户 user1 密码 |
| `LITE_JIUWENCLAW_URL` | `https://gitcode.com/cyz95/jiuwenclaw.git` | 用户面前端 jiuwenclaw 克隆地址(frontend build 自动克隆用) |
| `LITE_JIUWENCLAW_BRANCH` | `develop` | jiuwenclaw 克隆分支 |

以上变量均可写在**项目根 `.env`**（复制 `.env.example` 改名即可,已被
gitignore;真实环境变量优先于 `.env`）。

各服务 env 注入表(排障用)：`uv run jiuwenswarm-lite env`。

### 实例种子(resources/lite-service*.json)与 `${VAR}` 路径占位

种子文件经管理面前端导入生成容器/服务模板,**不写任何本机绝对路径**(可移植,
换机器/换目录直接可用)。随部署位置变化的值用 `${VAR}` 引用,deploy 期由
runtime 的 SubprocessPodClient 展开为其进程 env 的真实值(语义对齐 K8s
`$(VAR)`,仅 env 值;解析不到保留原样并记 WARNING):

| 模板 env 写法 | 展开源(launcher 注入 runtime 进程 env) | 展开结果 |
|---|---|---|
| `GATEWAY_SQLITE_PATH=${GATEWAY_SQLITE_PATH}` | `GATEWAY_SQLITE_PATH=<home>/gateway.db` | pod 与 gateway 共用同一 sqlite |

- `<home>` 即 `JIUWENSWARM_EE_LITE_HOME`(默认 `./data`),launcher 启动时计算,
  是该路径的唯一真源;种子文件里只写占位。
- 改种子后需让配置重新下发:管理面重新导入,或 PATCH 容器模板 env 后再 PATCH
  一次服务模板(触发 config_sync 再推送);存量 pod 瞬态,新 pod 自动带新 env。
- 已导入的种子在管理面存的是占位原文——**不重启 runtime 也能推**,但展开逻辑
  在 runtime 进程里,升级 agent_runtime 代码后需 `jiuwenswarm-lite stop runtime
  && start runtime` 一次。

日志:`data/logs/<服务名>.log`;agentserver 子进程日志:
`data/agentserver_pods/pods/<pod_id>/agent.log`。

## 目录结构

```
src/jiuwenswarm_ee_lite/
  config.py     # 拓扑配置(端口/目录/密码,env 驱动)
  spec.py       # 每服务启动命令+env+就绪探针规格
  launcher.py   # 进程编排(按序启动/就绪等待/逆序停止/状态)
  cli.py        # jiuwenswarm-lite start|stop|status|env
tests/
  helpers/      # API 客户端 + mock LLM(OpenAI 兼容)
  e2e/          # ① 管理面配置下发 ② 用户面聊天消息
```
