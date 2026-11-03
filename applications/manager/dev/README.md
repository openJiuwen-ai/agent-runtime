# Manager 本地开发

使用独立 Python 环境、SQLite 数据库及本机回环地址。身份服务使用 18770，Manager API 使用 18765，Vite 使用 5273，避开 macOS 的 8770 端口。前端支持 HMR，两个 Python 服务支持源码重载。

在仓库根目录安装：

```sh
uv venv .venv-manager --python 3.11
uv pip install --python .venv-manager/bin/python -e foundation -e service -e applications/manager/identity_center -e applications/manager/manager_server
npm --prefix applications/manager/manager_web ci
```

macOS 后台启动（推荐用于持续调试）：

```sh
python3 applications/manager/dev/background.py start --offline
python3 applications/manager/dev/background.py status
```

由当前用户登录会话中的 `launchd` 管理，不依赖终端或临时工具会话。某个子服务退出时，启动器会停止其余进程并交由 `launchd` 重启整套服务。只监听本机回环地址，沿用现有数据库，不添加系统级或开机登录自启项；退出 macOS 登录会话后需重新启动。启动日志为 `.manager-dev/local-service.log`。

```sh
python3 applications/manager/dev/background.py restart --offline
python3 applications/manager/dev/background.py stop
```

`start` 不会重复启动已加载服务；改为真实联调需显式执行 `restart --live`。不要同时运行前台与后台两套服务。

前台启动（保持终端打开，Ctrl-C 停止全部服务）：

```sh
bash applications/manager/dev/local.sh
```

访问 http://127.0.0.1:5273/manager 。本地账号为 `admin/admin` 和 `user/user`。启动脚本会通过 `dev/accounts.py` 在固定的 `.manager-dev/identity.db` 中初始化并重置这两个开发账号；不会修改生产默认账号或其他数据库。已有 user1 保留以避免破坏既有数据。数据库和日志均不提交 Git。


此环境提供真实身份认证和 Manager API，初始管理数据为空。未启动 JiuwenSwarm Gateway、Agent Runtime、用户端和 Kubernetes：Agent 实际执行、配置下发及 Pod 重建需要后续专用联调环境。不要导入生产实例连接信息进行日常 UI 开发。

### 离线调试集群配置

本机无法解析向导自动生成的 Kubernetes Service 地址，例如 `jiuwenswarm-gateway.default`。
仅调试前端时，先 Ctrl-C 停止已有本地服务，再启动：

```sh
bash applications/manager/dev/local.sh --offline
```

该参数设置 `MANAGER_DEV_OFFLINE=1`，仅允许与 `MANAGER_LOCAL_DEV=1`、回环监听地址和 SQLite 一起使用，默认关闭。通过启动日志或 `GET http://127.0.0.1:18765/api/dev-mode` 查看 `offline` 状态。

- 集群创建、地址修改跳过 Gateway、Runtime、用户面的健康检查；新建组件保持 `pending`，后台和手动巡检也不会伪造在线状态。
- 集群准入、Agent 绑定、运行规格绑定及应用配置仍经过 Manager 的业务校验，并保存到 `.manager-dev/manager.db`。需要下发的 Gateway/Runtime 请求返回标有 `simulated` 的开发回执，日志记录 `[LocalOffline]`。
- 必填项、地址唯一性、模板引用等校验照常执行。创建第二个测试集群时，请换一个命名空间（例如 `local-ui-2`），或填写不同的服务地址。
- 这只验证配置流程，不启动实际容器，不验证模型调用、真实运行或对话。外部服务的实时信息可能为空；资源导入仍需提供正确文件/配置，离线模式不会自动补齐。

调试结束后 Ctrl-C 停止，再恢复真实联调：

```sh
bash applications/manager/dev/local.sh --live
```

`--live` 显式设置 `MANAGER_DEV_OFFLINE=0`，恢复真实健康检查、周期巡检和配置下发，无需撤销代码。
数据库会保留；本地测试集群仍会显示，但需将地址改为实际可达服务并完成联调，才能运行和对话。
恢复后，对可达实例的巡检可能触发配置全量下发，请只在对应测试集群进行联调。

服务仍会读取项目现有的 `applications/manager/.env`；脚本覆盖端口、数据库路径和身份验证地址。若自行添加该文件，应只放本地配置，不要复用生产 Redis、SSO 或实例设置。

验证：

```sh
curl -f http://127.0.0.1:18765/api/health
curl -f http://127.0.0.1:5273/idp/v1/auth/public_key
npm --prefix applications/manager/manager_web run build
npm --prefix applications/manager/manager_web test
```

### 开发依赖缓存

Vite 开发服务使用 `manager_web/node_modules/.vite-manager-dev`，与前端 SSR 测试使用的默认 `.vite` 缓存隔离，避免运行测试后出现 `504 Outdated Optimize Dep`。修改 Vite 配置后如自动重启未完成，可停止并重新执行本地启动命令，再刷新浏览器。

前端仅展示 Manager API 返回的数据；已移除内置演示集群、演示专家目录和演示模板生成脚本。离线模式仍可通过正常配置流程保存测试数据。
