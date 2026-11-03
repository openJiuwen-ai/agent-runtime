# jiuwenswarm OpenShift 部署指导

本目录提供在 OpenShift 集群上部署 / 卸载 jiuwenswarm 产品组件与基础服务的脚本和
清单模板。适用于任意 OpenShift 4.x 集群：准备一个具有目标项目 admin 权限的账号，
按本文步骤操作即可。

说明：部署统一使用本目录脚本提交资源，部分环境直连 `kubectl apply` / `oc apply`
会因大体积 schema 传输失败。文中命令统一以 `<namespace>` 指代你的目标命名空间，
使用时替换为你自己的集群地址与命名空间。

## 1. 准备与登录

先从集群管理员处获取以下信息（"示例"列取自某试用集群，仅作格式参考）：

| 项 | 说明 | 示例 |
|---|---|---|
| Console 地址 | OpenShift Web 控制台 URL | `https://console-openshift-console.apps.rm1.0a51.p1.openshiftapps.com/` |
| API Server 地址 | `oc login --server=` 使用的 URL | `https://api.rm1.0a51.p1.openshiftapps.com:6443` |
| 登录账号 | 具有目标项目 admin 权限的账号| `<账号>` |
| 命名空间 | 部署目标项目名，对应 `.env.custom` 的 `NAMESPACE` | `<namespace>` |

在可访问集群的机器上安装 oc 客户端（内含 kubectl）：

```bash
curl -LO https://mirror.openshift.com/pub/openshift-v4/clients/ocp/stable/oc-linux.tar.gz
tar -xzf oc-linux.tar.gz && mv oc /usr/local/bin/ && oc version
```

获取登录命令：登录 Console → 右上角用户名菜单 → **Copy login command** →
**Display Token**，复制 `oc login` 命令执行（token 有效期一般 24 小时，过期后重新复制）：

```bash
oc login --token=sha256~xxxxxxxx --server=https://api.rm1.0a51.p1.openshiftapps.com:6443
oc project <namespace>
kubectl get pods   # 确认登录成功
```

## 2. 配置（.env.custom）

可配置变量的含义与默认值详见同目录 `.env.example`。


## 3. 部署 / 卸载

**依赖要求**：`oc`、`curl`、`yq`；

### 3.1 deploy.sh —— 组件一键部署

```bash
./deploy.sh up                    # 拉起产品组件
./deploy.sh up manager            # 拉起管理套件
./deploy.sh up mariadb            # 只部署指定组件（可多选: up mariadb minio）
./deploy.sh down                  # 卸载产品组件（倒序删除；secret / redis 有人用则自动保留）
./deploy.sh down manager          # 卸载管理套件（倒序）
./deploy.sh up mariadb --render-only   # 只渲染指定组件清单到 conf/，不连集群
./deploy.sh -n other-ns up --render-only   # 指定 namespace 渲染
```

可指定的组件：基础服务 `mariadb / mysql / postgresql / minio`，共享依赖
`secret / redis`，产品组件 `agentserver / gateway / web`，管理套件
`identity / manager-server / manager-web`。

行为说明：

- **namespace**：全部组件的 namespace 由 `.env.custom` 的 `NAMESPACE` 统一控制，
  也可 `-n <ns>` 临时指定；数据库建库的库名后缀同样取自 `NAMESPACE`
  （库名形如 `<业务>_<NAMESPACE>`，更换 namespace 即使用对应后缀的库）；
- **版本管理**：组件镜像统一由 `.env.custom` 的 `*_IMAGE` 变量控制。升级 / 回滚 /
  换架构只需改镜像名后重跑 `up`，存量 Pod 会自动滚动更新；
- **顺序即依赖**：agentserver 先于 gateway 部署、runtime 先于 agentserver（注册接收方），
  脚本已保证；
- **依赖管理**：共享依赖 secret / redis 缺失时随 `up` 自动拉起，已存在则复用；
  基础服务（mariadb / minio）缺失时终止并提示，`./deploy.sh up mariadb minio` 拉起即可；
- **卸载保护**：`down` 时组件若仍被集群中存活的组件依赖（如 manager-server 还在时卸载
  产品组），secret / redis 会自动保留，不会被误删；
- **幂等**：可重复执行，已存在的资源自动跳过；副本数被人为调整过会自动恢复；
- **就绪等待**：`up` 每个组件部署后轮询等待 Pod 就绪、`down` 等待 Pod 删净
  （默认预算 300s，可 `WAIT_TIMEOUT_SECS=600 ./deploy.sh up` 调整，首次拉取镜像建议调大）；
- **render-only**：`--render-only` 只渲染本次目标组件的清单到 `conf/`，不连接集群，
  用于核对 / 交付部署清单；
- **副本数可配置**：见第 2 节 `*_REPLICAS` 变量；
- gateway 为 distributed 多副本部署，等待全部副本 Ready 后才继续。

完整部署顺序（从零开始）：

```bash
./deploy.sh up mariadb    # 数据库（三选一: mariadb / mysql / postgresql，默认用 MariaDB）
./deploy.sh up minio      # 对象存储
./deploy.sh up            # 产品组件（按 AGENT_RUNTIME_DEPLOY_MODE 派生，见 .env.example）:
                          #   off:     secret → redis → agentserver → gateway → web
                          #   routing: secret → redis → runtime → agentserver → gateway → web
                          #   full:    secret → redis → runtime → gateway → web
                          #          （AgentServer 由 runtime 按需创建，自动注入 Pod 操作 RBAC）
./deploy.sh up manager   # 管理套件: identity → manager-server → manager-web
```

数据库说明：`templates/` 下同时提供 mysql / postgresql 模板，供需要替换数据库的场景
选用，部署命令相同（如 `./deploy.sh up mysql`）。产品栈默认对接 MariaDB，换库时需
同步调整组件清单中的数据库连接配置（`*_DB_TYPE` / `*_DB_HOST` 等）后重新 `up`。

完整卸载顺序（与部署相反）：

```bash
./deploy.sh down manager   # 卸载管理套件（倒序: manager-web → manager-server → identity）
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

### 3.2 kubectl.sh —— 底层资源提交工具

`kubectl -f` 等价工具，适合提交单份 yaml、批量清理等场景；日常组件部署请用 deploy.sh。

```bash
./kubectl.sh -f xxx.yaml                 # 默认 apply：已存在自动跳过
./kubectl.sh create -f xxx.yaml          # 严格创建：已存在时报 AlreadyExists，退出码 1
./kubectl.sh replace -f xxx.yaml         # 整对象替换更新（Service 等含不可变字段的资源不支持，需删除后重建）
./kubectl.sh delete -f xxx.yaml          # 删除资源（404 时报错，加 --ignore-not-found 可忽略）
./kubectl.sh -n <ns> -f xxx.yaml         # yaml 未显式写 namespace 时的兜底
```


## 4. 访问服务

### 4.1 管理面 / 用户面访问说明

部署时，部署工具自动为 manager-web 创建 Route（见模板尾部的 Route 文档），部署完成
后 `kubectl get route` 可查看实际域名（`<name>-<namespace>.<集群ingress域名>`）。
**无需 port-forward，浏览器从任何网络直接访问：**

```text
https://jiuwenswarm-manager-web-<ns>.<ingress域名>
```

这是唯一需要记住的入口：从管理面登录后点击进入用户面，**不要直接访问 web 组件的
地址**（用户面的鉴权代理在 manager-web 的 nginx 上）。

### 4.2 数据库 / MinIO 访问说明

mariadb / mysql / postgresql **仅提供集群内访问**（试用集群无公网 NodePort /
LoadBalancer）。不通过 Route 暴露数据库的原因：Route 对 TCP 流量只有 passthrough
模式，要求客户端发起连接的第一个字节就是 TLS（Router 靠 SNI 分流）；而 MySQL/
PostgreSQL 协议是"明文握手 + 协议内协商升级 TLS"，与 passthrough 在协议层面互斥
（实测两种连接方式均失败）。

MinIO 的 Console / S3 API 是 HTTP 服务，协议上可以走 Route，但当前凭据为部署默认值
且未做来源限制，为避免暴露公网**同样仅提供集群内访问**。

临时排障访问可通过port-forward走**本地端口映射**：kubectl 把
本机端口转发到集群内 Service。
```bash
kubectl port-forward --address 0.0.0.0 -n <namespace> svc/mysql-headless 30306:3306
kubectl port-forward --address 0.0.0.0 -n <namespace> svc/mariadb-headless 30307:3306
kubectl port-forward --address 0.0.0.0 -n <namespace> svc/postgresql-headless 30433:5432
kubectl port-forward --address 0.0.0.0 -n <namespace> svc/redis 30379:6379
kubectl port-forward --address 0.0.0.0 -n <namespace> svc/minio 30901:9001    # MinIO Console，浏览器开 http://<部署机IP>:30901
```

提示：`--address 0.0.0.0` 会把端口暴露到部署机所在网络，且凭据为部署默认值，
**用完即关**。

## 5. 模型接入

产品通过 OpenAI 兼容接口接入模型服务，在管理界面（或对应配置项）中填写：

```text
MODEL_PROVIDER = OpenAI          # 服务协议类型
MODEL_NAME     = <模型名>        # 以模型服务方提供的名称为准
API_BASE       = <服务地址>/v1   # OpenAI 兼容接口地址
API_KEY        = <访问凭证>      # 以模型服务方发放的为准，注意有效期
LLM_SSL_VERIFY = False           # 自签证书环境关闭证书校验
```

连通性验证（可选，在集群内任一 Pod 中执行）：

```bash
curl -sk "$API_BASE/chat/completions" \
  -H "Authorization: Bearer $API_KEY" -H "Content-Type: application/json" \
  -d '{"model":"'"$MODEL_NAME"'","messages":[{"role":"user","content":"回复两个字：收到"}],"max_tokens":32}'
```

