# jiuwenclaw OpenShift 部署指导

本指导说明如何登录 OpenShift 集群，并使用本目录提供的脚本完成 jiuwenclaw
产品组件及相关基础服务的部署。文中 `rapheale-dev` 均为**示例命名空间**——实际以
`.env.custom` 的 `NAMESPACE`（或 `-n` 参数）为准。

说明：该集群 API 链路对大体积响应较为敏感，直接使用 `kubectl apply` / `oc apply` 容易
传输失败。日常部署请使用本目录提供的脚本。

| 项 | 值 |
|---|---|
| Console | https://console-openshift-console.apps.rm1.0a51.p1.openshiftapps.com/k8s/ns/rapheale-dev/core~v1~Pod |
| API Server | https://api.rm1.0a51.p1.openshiftapps.com:6443 |
| 命名空间 | 示例 `rapheale-dev`；实际由 `.env.custom` 的 `NAMESPACE`（或 `-n` 参数）控制 |
| 登录账号 | rapheale / Panfei@1234567 |
| 有效期 | 集群试用 30 天；登录 token 有效期 24 小时 |

## 1. 获取登录命令（token）

1. 浏览器打开 Console（上表链接），使用账号登录；
2. 右上角用户名菜单 → **Copy login command** → **Display Token**；
3. 复制如下形式的 `oc login` 命令（token 以页面实际展示为准，有效期 24 小时，过期后重新复制）：

```bash
oc login --token=sha256~xxxxxxxx --server=https://api.rm1.0a51.p1.openshiftapps.com:6443
```

## 2. 安装客户端并登录

在一台可以访问外网的机器上安装 oc 客户端（内含 kubectl）：

```bash
# Linux 示例（官方稳定版）
curl -LO https://mirror.openshift.com/pub/openshift-v4/clients/ocp/stable/oc-linux.tar.gz
tar -xzf oc-linux.tar.gz && mv oc /usr/local/bin/ && oc version
```

登录并将 context 切换到集群：

```bash
oc login --token=sha256~xxxxxxxx --server=https://api.rm1.0a51.p1.openshiftapps.com:6443
oc config use-context /api-rm1-0a51-p1-openshiftapps-com:6443/rapheale
```

权限说明：该账号在 `rapheale-dev` / `sandbox-shared-models` 两个项目内具备 admin 权限，
项目外资源不可访问。将默认命名空间切换为 `rapheale-dev`：

```bash
oc project rapheale-dev
kubectl config set-context --current --namespace=rapheale-dev
```

确认环境就绪：

```bash
kubectl get pods
```

## 3. 部署

### 3.1 kubectl.sh —— 底层资源提交工具

底层 `kubectl -f` 等价工具。日常组件部署请用 3.2 的 `deploy.sh`；kubectl.sh 适合
提交单份 yaml、批量清理等场景。用法：

```bash
./kubectl.sh -f xxx.yaml                 # 默认 apply：已存在自动跳过
./kubectl.sh create -f xxx.yaml          # 严格创建：已存在时报 AlreadyExists，退出码 1
./kubectl.sh replace -f xxx.yaml         # 整对象替换更新（Service 等含不可变字段的资源不支持，需删除后重建）
./kubectl.sh delete -f xxx.yaml          # 删除资源（404 时报错，加 --ignore-not-found 可忽略）
./kubectl.sh -n <ns> -f xxx.yaml         # yaml 未显式写 namespace 时的兜底
```

多文档 yaml 可整体 `-f` 提交；`-f` 可重复；支持通配符（含未被 shell 展开的 `**.yaml`）。

注意：templates/ 下的模板文件含占位符，kubectl.sh **不做渲染**——需要渲染产物请用
`deploy.sh --render-only` 或直接 `deploy.sh` 部署。

### 3.2 deploy.sh —— 组件一键部署（组 / 按组件 / 渲染）

```bash
./deploy.sh up                    # 拉起产品组件
./deploy.sh manager up            # 拉起管理套件
./deploy.sh up mariadb            # 只部署指定组件（可多选: up mariadb minio）
./deploy.sh down                  # 卸载产品组件（倒序删除；secret / redis 有人用则自动保留）
./deploy.sh manager down          # 卸载管理套件（倒序）
./deploy.sh up mariadb --render-only   # 只渲染指定组件清单到 conf/，不连集群
./deploy.sh -n other-ns up --render-only   # 指定 namespace 渲染
```

可指定的组件：基础服务 `mariadb / mysql / postgresql / minio`，共享依赖
`secret / redis`，产品组件 `agentserver / gateway / web`，管理套件
`identity / manager-server / manager-web`。

行为说明：

- **namespace**：全部组件的 namespace 由 `.env.custom` 的 `NAMESPACE` 统一控制，
  也可 `-n <ns>` 临时指定；mariadb 已建库的库名后缀同样取自 `NAMESPACE`
  （库名形如 `<业务>_<NAMESPACE>`，更换 namespace 即使用对应后缀的库）；
- **版本管理**：组件镜像统一由 `.env.custom` 的 `*_IMAGE` 变量控制（完整镜像名，
  含 amd64/arm64 架构段）。升级 / 回滚 / 换架构只需改镜像名后重跑 `up`，
  存量 Pod 会自动滚动更新；
- **顺序即依赖**：agentserver 先于 gateway 部署，脚本已保证；
- **依赖管理**：共享依赖 secret / redis 缺失时随 `up` 自动拉起，已存在则复用；
  基础服务（mariadb / minio）缺失时终止并提示，`./deploy.sh up mariadb minio` 拉起即可；
- **卸载保护**：`down` 时组件若仍被集群中存活的组件依赖（如 manager-server 还在时卸载
  产品组），secret / redis 会自动保留，不会被误删；
- **幂等**：可重复执行，已存在的资源自动跳过；副本数被人为调整过会自动恢复；
- **就绪等待**：`up` 每个组件部署后轮询等待 Pod 就绪、`down` 等待 Pod 删净
  （默认预算 300s，可 `WAIT_TIMEOUT_SECS=600 ./deploy.sh up` 调整，首次拉取镜像建议调大）；
- **render-only**：`--render-only` 只渲染本次目标组件的清单到 `conf/`，不连接集群，
  用于核对 / 交付部署清单；
- gateway 为 distributed 多副本部署，等待全部副本 Ready 后才继续。

完整部署顺序（从零开始）：

```bash
./deploy.sh mariadb up    # 拉起数据库服务，数据库三选一（产品栈默认使用 MariaDB）
./deploy.sh minio up      # 拉起minio服务
./deploy.sh up            # 拉起产品组件: secret → redis → agentserver → gateway → web
./deploy.sh manager up    # 拉起管理套件: identity → manager-server → manager-web
```

数据库说明：`templates/` 下同时提供 mysql.template.yaml / postgresql.template.yaml，供需要
替换数据库的场景选用（如客户环境指定 MySQL / PostgreSQL），部署命令同款：
`./deploy.sh up mysql`。本集群产品栈默认对接 MariaDB，换库时需同步调整组件清单中的
数据库连接配置（`*_DB_TYPE` / `*_DB_HOST` 等）后重新 `up`。

完整卸载顺序（与部署相反）：

```bash
./deploy.sh manager down   # 卸载管理套件（倒序: manager-web → manager-server → identity）
./deploy.sh down           # 卸载产品组件（倒序: web → gateway → agentserver → redis → secret）
./deploy.sh down minio     # 卸载minio服务
./deploy.sh down mariadb   # 卸载数据库
```

卸载说明：

- **数据不可恢复**：PVC 随组件一并删除，需要保留数据请先导出；
- **共享依赖自动保留**：secret / redis 卸载时若仍被集群中存活的组件依赖会跳过删除——
  如先执行 `down` 时管理套件还在，secret / redis 会被保留，待 `manager down` 后
  再补一次 `./deploy.sh down` 即可清干净；
- **等 Pod 删净**：每个组件删除后脚本会等其 Pod 全部消失，再继续下一个组件。

### 4.1 访问管理界面

NodePort 仅集群 VPC 内可达；外部网络建议通过 port-forward 隧道访问（经 API server 转发，
不产生公网暴露）。以下命令中的 `-n rapheale-dev` 为示例，按实际 `NAMESPACE` 调整。

在这台机器上执行：

```bash
kubectl port-forward --address 0.0.0.0 -n rapheale-dev svc/jiuwenclaw-manager-web-nodeport 30273:5273
```

浏览器打开 `http://<这台机器的IP>:30273` 即可访问管理界面。注意：该终端需保持运行；
token 过期后需重新登录并重启 port-forward。

### 4.2 其他服务临时访问

```bash
kubectl port-forward --address 0.0.0.0 -n rapheale-dev svc/mysql-nodeport 3306:3306       # 本机 127.0.0.1:3306
kubectl port-forward --address 0.0.0.0 -n rapheale-dev svc/mariadb-nodeport 3307:3306     # 本机 127.0.0.1:3307
kubectl port-forward --address 0.0.0.0 -n rapheale-dev svc/postgresql-nodeport 5433:5432  # 本机 127.0.0.1:5433
```

## 5. 模型接入（集群内共享模型）

集群提供共享推理模型（vLLM 后端，仅集群内部可访问），产品配置方式：

```bash
MODEL_PROVIDER="OpenAI"
MODEL_NAME="isvc-qwen3-8b-fp8"     # 可选：isvc-granite-31-8b-fp8 / isvc-nemotron-nano-9b-v2-fp8
API_BASE="https://isvc-qwen3-8b-fp8-predictor.sandbox-shared-models.svc.cluster.local:8443/v1"
API_KEY="<oc whoami -t 的 token>"
LLM_SSL_VERIFY="False"             # 平台使用自签证书，对应关闭证书校验
```

- 服务端口为 **8443**（TLS），其他端口未开放；
- `API_KEY` 为 OpenShift 用户 token，**有效期 24 小时**，过期后模型调用会返回 401，
  需重新获取并更新配置；
- qwen3-8b 回复中带 `<think>` 思考过程属正常行为；
- 验证方法（在集群内 Pod 中执行）：

```bash
TOKEN=$(oc whoami -t)
kubectl exec -i -n rapheale-dev deploy/jiuwenclaw-agentserver -- python3 - "$TOKEN" <<'PYEOF'
import json, ssl, sys, urllib.request
ctx = ssl.create_default_context(); ctx.check_hostname=False; ctx.verify_mode=ssl.CERT_NONE
base = "https://isvc-qwen3-8b-fp8-predictor.sandbox-shared-models.svc.cluster.local:8443"
r = urllib.request.Request(base+"/v1/chat/completions",
    headers={"Authorization":"Bearer "+sys.argv[1],"Content-Type":"application/json"},
    data=json.dumps({"model":"isvc-qwen3-8b-fp8","messages":[{"role":"user","content":"回复两个字：收到"}],"max_tokens":32}).encode())
print(urllib.request.urlopen(r, context=ctx, timeout=60).read().decode()[:300])
PYEOF
```
