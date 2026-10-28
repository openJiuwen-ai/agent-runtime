#!/usr/bin/env bash
[ -n "${_DEPLOY_GLOBAL_VARS_SH:-}" ] && return 0
_DEPLOY_GLOBAL_VARS_SH=1

# global_vars.sh —— 全局变量与默认配置
# SCRIPT_DIR 由入口 deploy.sh 定义；独立 source 时自行推导
SCRIPT_DIR="${SCRIPT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
CONF_DIR="$SCRIPT_DIR/conf"
TEMPLATE_DIR="$SCRIPT_DIR/templates"
CUSTOM_ENV_FILE="$SCRIPT_DIR/.env.custom"

# ---------- 组件清单 ----------

# 全部组件的规范依赖序（up 按此序排列；down 倒序）。
# secret 必须在最前：基础服务与产品组件的密码统一经 jiuwenswarm-secret-configmap 注入
ALL_COMPONENTS=(secret mariadb mysql postgresql minio redis runtime agentserver gateway web identity manager-server manager-web)

UP_TARGETS=(secret redis agentserver gateway web)         # up 默认目标（规范依赖序）
MANAGER_TARGETS=(identity manager-server manager-web)     # manager 组

# 镜像名变量清单，与 templates/ 下 <<XXX_IMAGE>> 占位符一一对应
IMAGE_VARS=(AGENTSERVER_IMAGE GATEWAY_IMAGE WEB_IMAGE IDENTITY_IMAGE MANAGER_SERVER_IMAGE MANAGER_WEB_IMAGE MARIADB_IMAGE MYSQL_IMAGE POSTGRESQL_IMAGE REDIS_IMAGE MINIO_IMAGE)

# 副本数变量清单，与 templates/ 下 <<XXX_REPLICAS>> 占位符一一对应
REPLICAS_VARS=(AGENT_SERVER_REPLICAS GATEWAY_REPLICAS WEB_REPLICAS IDENTITY_REPLICAS MANAGER_SERVER_REPLICAS MANAGER_WEB_REPLICAS AGENT_RUNTIME_REPLICAS)

# 允许留空的变量（render_component_files 消费：留空时占位符独占行整行删除）
# - StorageClass 未配置时使用集群默认动态供给
# - OBS_PUBLIC_BASE_URL/OBS_REGION/OBS_SECURE：外部 OBS 选填项（内置 MinIO 时强制派生/忽略）
# - REDIS_USER：留空 = 外挂 Redis 使用 default 用户（内置模式由 resolve_redis_vars 派生 jiuwenswarm）
# - IDENTITY_SSO_* / IDENTITY_FEDERATION_CONFIG_FILE：企业认证配置可留空
# - GATEWAY_AGENT_CLIENT_MODE / GATEWAY_RUNTIME_MANAGER_URL / AGENTSERVER_POD_DNS_LIST：
#   三模式派生变量（routing 注入，off/full 留空 → 对应 env 行删除）
# 格式校验与"允许留空"解耦（用户名/URL 不该按 DNS subdomain 误校验）：
# StorageClass 见 STORAGE_CLASS_VARS（check_storage_class_vars）；其余选填项见 check_optional_vars
OPTIONAL_VARS=(
    DATA_STORAGE_CLASS WORKSPACE_STORAGE_CLASS OBS_PUBLIC_BASE_URL OBS_REGION OBS_SECURE REDIS_USER
    IDENTITY_SSO_SERVER_URL IDENTITY_SSO_LOGIN_CLIENT_ID IDENTITY_SSO_TOKEN_AUDIENCE
    IDENTITY_FEDERATION_CONFIG_FILE
    GATEWAY_AGENT_CLIENT_MODE GATEWAY_RUNTIME_MANAGER_URL AGENTSERVER_POD_DNS_LIST
)

# StorageClass 变量：check_storage_class_vars 做 DNS subdomain 规范校验（留空走集群默认）
STORAGE_CLASS_VARS=(DATA_STORAGE_CLASS WORKSPACE_STORAGE_CLASS)

# 运行依赖清单（check_dependency 按 CLUSTER_TYPE 选用；kubectl --raw 需 ≥1.20）
DEPS_OPENSHIFT=(curl yq oc)
DEPS_K8S=(yq kubectl)

# ---------- 部署变量默认值 ----------
# DEPLOY_VARS 提供通用默认值；可被 .env.custom 覆盖
declare -A DEPLOY_VARS=(
    # ----- 通用（双形态） -----
    ["NAMESPACE"]="rapheale-dev"
    ["RENDER_ONLY"]="false"
    ["CLUSTER_TYPE"]="openshift"
    ["CLUSTER_API_SERVER"]=""            # openshift 形态必填（http(s) URL），留空自动探测（oc/kubeconfig）；k8s 形态走 kubeconfig，留空
    ["BASE_SVC_NS"]=""                   # 基础服务所在 ns
     ["DATA_STORAGE_CLASS"]=""
    ["WORKSPACE_STORAGE_CLASS"]=""

    ["MARIADB_ROOT_PASSWORD"]="Root@123456"
    ["MYSQL_ROOT_PASSWORD"]="Root@123456"
    ["POSTGRES_PASSWORD"]="Root@123456"
    ["MINIO_ROOT_USER"]="minioadmin"
    ["MINIO_ROOT_PASSWORD"]="Root@123456"

    ["DB_TYPE"]="mariadb"                # 数据库类型：mariadb | mysql | postgresql
    ["DB_HOST"]=""
    ["DB_PORT"]=""                       # 留空按 DB_TYPE 取默认（3306/5432）；外部数据库填写对应端口
    ["DB_USER"]=""                       # 留空按 DB_TYPE 取默认（root/postgres）；外部数据库填写对应用户
    ["DB_PASSWORD"]=""                   # 外部数据库统一密码（语义对齐 jiuwenswarm-ee）；留空时内置模式按 DB_TYPE 取 ROOT_PASSWORD，外置模式两者均空报错
    ["ENABLE_EXTERNAL_DB"]="false"

    ["REDIS_USER"]=""                    # 留空 = default 用户（外挂 Redis 场景）；内置 Redis 由 resolve_redis_vars 派生 jiuwenswarm
    ["REDIS_PASSWORD"]="Root@123456"
    # Redis 部署形态与连接：REDIS_HOST 空=内置 Redis（地址自动派生）；填写=外部 Redis
    ["REDIS_MODE"]="standalone"          # standalone | cluster（cluster 仅影响外挂连接 URL 前缀 redis+cluster://）
    ["REDIS_HOST"]=""                    # 外部 Redis 服务的连接地址（内置 Redis 时自动 redis.<NAMESPACE>）
    ["REDIS_PORT"]=""                    # 留空按形态取默认（6379）；外部 Redis 填写对应端口
    ["REDIS_KEY_PREFIX"]="jiuwenswarm:"   # gateway/agentserver/manager-server 的 Redis 键统一前缀
    ["ENABLE_EXTERNAL_REDIS"]="false"

    # 对象存储：是否内置 MinIO 仅由 OBS_URL 是否为空唯一决定，与其余变量无关
    # 内置（OBS_URL 空）：地址/SECURE/REGION 强制派生，AK/SK 强制取 MinIO root（其他变量填了也会被忽略）
    # 外部（OBS_URL 填写）：地址原样使用，BUCKET/AK/SK 必填，SECURE/REGION 按需填写
    ["OBS_URL"]=""                       # 空=内置 MinIO；填写=外部 OBS/S3 兼容服务接入地址
    ["OBS_BUCKET"]="jiuwenswarm"          # 业务存储桶名称
    ["OBS_PUBLIC_BASE_URL"]=""           # 对象存储公网预览域名（可选，浏览器直读场景）
    ["OBS_ACCESS_KEY"]=""                # 内置：忽略（自动取 MINIO_ROOT_USER）；外部：AccessKey（必填）
    ["OBS_SECRET_KEY"]=""                # 内置：忽略（自动取 MINIO_ROOT_PASSWORD）；外部：SecretKey（必填）
    ["OBS_SECURE"]=""                    # 内置：忽略（强制 false）；外部：true/false（留空默认 false）
    ["OBS_REGION"]=""                    # 内置：忽略（强制 default）；外部：区域标识（留空则由 SDK 决定）
    ["ENABLE_EXTERNAL_OBS"]="false"

    ["AGENT_SERVER_REPLICAS"]="1"
    ["GATEWAY_REPLICAS"]="1"
    ["WEB_REPLICAS"]="1"
    ["IDENTITY_REPLICAS"]="1"
    ["MANAGER_SERVER_REPLICAS"]="1"
    ["MANAGER_WEB_REPLICAS"]="1"
    ["AGENT_RUNTIME_REPLICAS"]="1"

    # agent-runtime 三模式部署形态（routing | off；full 待开放）
    ["AGENT_RUNTIME_DEPLOY_MODE"]="routing"
    # 禁 EVAL 兼容模式（on 与历史行为一致；客户 Redis 禁用 EVAL 时设 off）
    ["AGENT_RUNTIME_REDIS_LUA"]="off"
    ["SANDBOX_TYPE"]="off"

    ["MARIADB_IMAGE"]="mariadb:11.8"
    ["MYSQL_IMAGE"]="mysql:8.0"
    ["POSTGRESQL_IMAGE"]="postgres:16"
    ["REDIS_IMAGE"]="redis:7-alpine"
    ["MINIO_IMAGE"]="quay.io/minio/minio:RELEASE.2025-04-22T22-12-26Z"


    # ----- 仅 k8s 形态（CLUSTER_TYPE=k8s，openshift 下不生效） -----
    # NFS 直连挂载：PVC 卷改写为 NFS 直连挂载
    # NFS_SERVER_ADDR 空=使用内置 NFS server；非空=外部 NFS server
    ["NFS_SERVER_ADDR"]=""               # NFS 服务器地址（可选）
    ["NFS_SHARE_PATH"]="/"               # 共享根路径（须绝对路径），数据落 <该路径>/<NAMESPACE>/<组件>
    ["ENABLE_EXTERNAL_NFS"]="false"
    # 内置 NFS server Pod 定位（NFS_SERVER_ADDR 留空时使用）
    ["NFS_POD_PATH"]="/exports"          # 内置 NFS Pod 内的导出根（hostPath 挂载点）
    ["NFS_POD_NAMESPACE"]="default"      # 内置 NFS Pod 所在 namespace
    ["NFS_POD_LABEL"]="app=nfs-server"   # 内置 NFS Pod 的 label 选择器
    # manager-web NodePort Service：外部经 http://<节点IP>:<nodePort> 访问
    ["MANAGER_WEB_NODE_PORT"]=""         # 留空=从下方范围自动分配；.env.custom 可固定端口
    ["NODE_PORT_RANGE_START"]="30000"    # 自动分配范围下界
    ["NODE_PORT_RANGE_END"]="32767"      # 自动分配范围上界
    ["MINIO_API_NODE_PORT"]=""           # k8s 形态 minio S3 API NodePort，留空自动分配
    ["MINIO_CONSOLE_NODE_PORT"]=""       # k8s 形态 minio Console NodePort，留空自动分配
    ["MARIADB_NODE_PORT"]=""             # k8s 形态 mariadb NodePort，留空自动分配
    ["MYSQL_NODE_PORT"]=""               # k8s 形态 mysql NodePort，留空自动分配
    ["POSTGRESQL_NODE_PORT"]=""          # k8s 形态 postgresql NodePort，留空自动分配
    # 企业统一认证
    ["IDENTITY_SSO_SERVER_URL"]=""
    ["IDENTITY_SSO_LOGIN_CLIENT_ID"]=""
    ["IDENTITY_SSO_TOKEN_AUDIENCE"]=""
    ["IDENTITY_FEDERATION_CONFIG_FILE"]=""
    # dev 模式：hostPath 挂载宿主机代码调试（MODE=dev 仅 k8s 形态支持；Pod 固定到代码所在节点）
    ["MODE"]="product"                   # product | dev
    ["SWARM_CODE_PATH"]=""                # jiuwenswarm 仓库路径（挂载到 agentserver/gateway/web）
    ["RUNTIME_CODE_PATH"]=""             # agent-runtime 仓库路径（挂载到 identity/manager-server/manager-web）
    ["CORE_CODE_PATH"]=""                # agent-core 仓库路径（openjiuwen 包挂载到 agentserver/gateway）
    ["IS_MOUNT_WEB_CODE"]="false"        # dev 模式下 web 是否挂载代码
    ["IS_MOUNT_MANAGER_WEB_CODE"]="false" # dev 模式下 manager-web 是否挂载代码
    ["CURRENT_NODE_NAME"]=""             # 代码所在节点；留空=按本机 hostname/IP 自动识别
    # Pod 侧代码路径（按镜像内布局，一般无需修改）
    ["SWARM_POD_CODE_PATH"]="/app/jiuwenswarm"
    ["RUNTIME_POD_CODE_PATH"]="/app/agent-runtime"
    ["RUNTIME_POD_PKG_PATH"]="/usr/local/lib/python3.11/site-packages/openjiuwen_runtime"
    ["CORE_POD_PKG_PATH"]="/usr/local/lib/python3.11/site-packages/openjiuwen"
)

