#!/usr/bin/env bash
[ -n "${_DEPLOY_VAR_HANDLER_SH:-}" ] && return 0
_DEPLOY_VAR_HANDLER_SH=1

# var_handler.sh —— 部署变量校验与渲染前派生

# 集群形态：openshift | k8s
check_cluster_type_var() {
  local val="${DEPLOY_VARS["CLUSTER_TYPE"]}"
  [[ "$val" =~ ^(openshift|k8s)$ ]] \
    || error "CLUSTER_TYPE 非法（openshift | k8s）: $val"
}

# agent-runtime 三模式：枚举校验 + 模式派生
# - routing/full：gateway 启用 runtime 路由客户端（GATEWAY_AGENT_CLIENT_MODE=runtime）并注入路由地址
# - off：gateway 两变量留空（占位符行删除，gateway 走 AGENT_SERVER_URL 直连）；
#   AgentServer 固定单副本，配置多副本视为矛盾
resolve_deploy_mode_vars() {
  local mode="${DEPLOY_VARS["AGENT_RUNTIME_DEPLOY_MODE"]:-}"
  # openshift 集群暂不支持 full 模式：受限 SCC 环境无法授予 Pod 操作权限，
  # 且 RS 配额等受限（见 .env.example 模式说明）；k8s 形态不受限。
  # 本函数在 check_vars 主链执行，error 直接终止部署
  if [ "$mode" = full ] && ! is_k8s_cluster; then
    error "AGENT_RUNTIME_DEPLOY_MODE=full 暂不支持 openshift 集群（CLUSTER_TYPE=openshift）：受限环境无法授予 Pod 操作权限，请改用 routing/off，或改用 CLUSTER_TYPE=k8s 形态部署"
  fi
  case "$mode" in
    routing|full)
      DEPLOY_VARS["GATEWAY_AGENT_CLIENT_MODE"]="runtime"
      DEPLOY_VARS["GATEWAY_RUNTIME_MANAGER_URL"]="http://jiuwenswarm-agent-runtime:8091"
      # routing：manager 据此清单注册外部 AgentServer Pod（与 StatefulSet
      # 副本数同源：jiuwenswarm-agentserver-{0..N-1} 的 headless DNS，Pod 重建自愈）
      if [ "$mode" = routing ]; then
        local dns="" i
        for ((i = 0; i < DEPLOY_VARS["AGENT_SERVER_REPLICAS"]; i++)); do
          [ -n "$dns" ] && dns+=","
          dns+="jiuwenswarm-agentserver-${i}.jiuwenswarm-agentserver-headless.${DEPLOY_VARS["NAMESPACE"]}.svc.cluster.local"
        done
        DEPLOY_VARS["AGENTSERVER_POD_DNS_LIST"]="$dns"
      else
        DEPLOY_VARS["AGENTSERVER_POD_DNS_LIST"]=""
      fi
      # runtime 镜像不在 IMAGE_VARS（off 模式无需配置，不做全局必填）；
      # 必填与格式白名单在 routing/full 分支补齐（与 check_image_vars 同规则，
      # 防渲染占位符替换的 awk gsub 特殊字符问题）
      local img="${DEPLOY_VARS["AGENT_RUNTIME_IMAGE"]:-}"
      [ -n "$img" ] \
        || error "AGENT_RUNTIME_IMAGE 未设置（$mode 模式必填，检查 $CUSTOM_ENV_FILE）"
      [[ "$img" =~ ^[A-Za-z0-9._:@/-]+$ ]] \
        || error "AGENT_RUNTIME_IMAGE 含非法字符（仅允许字母数字._:@/-）: $img";;
    off)
      DEPLOY_VARS["GATEWAY_AGENT_CLIENT_MODE"]=""
      DEPLOY_VARS["GATEWAY_RUNTIME_MANAGER_URL"]=""
      [ "${DEPLOY_VARS["AGENT_SERVER_REPLICAS"]}" = 1 ] \
        || error "AGENT_RUNTIME_DEPLOY_MODE=off 时 AgentServer 固定单副本（无粘性路由需求），AGENT_SERVER_REPLICAS 须为 1（多副本请用 routing/full）";;
    *)
      error "AGENT_RUNTIME_DEPLOY_MODE 非法（full | routing | off）: $mode";;
  esac
}

# namespace：k8s DNS label 规范
check_namespace_var() {
  local val="${DEPLOY_VARS["NAMESPACE"]:-}"
  [ -n "$val" ] || error "NAMESPACE 未设置（检查 $CUSTOM_ENV_FILE）"
  [[ "$val" =~ ^[a-z0-9]([-a-z0-9]*[a-z0-9])?$ ]] \
    || error "NAMESPACE 非法（须为 DNS label：小写字母数字与 -）: $val"
}

# 镜像名（仅由 .env.custom 提供，无内置默认）：校验非空；格式覆盖
# registry:port/repo:tag 与 @sha256 摘要形态
check_image_vars() {
  local v val
  for v in "${IMAGE_VARS[@]}"; do
    val="${DEPLOY_VARS[$v]:-}"
    [ -n "$val" ] || error "$v 未设置（检查 $CUSTOM_ENV_FILE）"
    [[ "$val" =~ ^[A-Za-z0-9._:@/-]+$ ]] \
      || error "$v 含非法字符（仅允许字母数字._:@/-）: $val"
  done
}

# 副本数：正整数
check_replicas_vars() {
  local v val
  for v in "${REPLICAS_VARS[@]}"; do
    val="${DEPLOY_VARS[$v]:-}"
    [[ "$val" =~ ^[1-9][0-9]*$ ]] || error "$v 非法（须为正整数）: $val"
  done
}

# StorageClass：DNS subdomain 规范；留空使用集群默认（STORAGE_CLASS_VARS）
check_storage_class_vars() {
  local v val
  for v in "${STORAGE_CLASS_VARS[@]}"; do
    val="${DEPLOY_VARS[$v]:-}"
    [ -z "$val" ] || [[ "$val" =~ ^[a-z0-9]([-a-z0-9.]*[a-z0-9])?$ ]] \
      || error "$v 非法（须为 DNS subdomain：小写字母数字与 -.）: $val"
  done
}

# 其余选填项（OPTIONAL_VARS 中除 StorageClass 外）按各自语义校验（均允许留空）。
# 各白名单字符集均排除 & 与 \：渲染经 awk gsub 替换，替换串中这两个字符特殊（见 replace_placeholder）
check_optional_vars() {
  local val
  val="${DEPLOY_VARS["OBS_PUBLIC_BASE_URL"]:-}"
  [ -z "$val" ] || [[ "$val" =~ ^(https?://)?[A-Za-z0-9._:/-]+$ ]] \
    || error "OBS_PUBLIC_BASE_URL 非法（须为 http(s) URL 或域名）: $val"
  val="${DEPLOY_VARS["OBS_SECURE"]:-}"
  [ -z "$val" ] || [[ "$val" =~ ^(true|false)$ ]] \
    || error "OBS_SECURE 非法（须为 true 或 false）: $val"
  val="${DEPLOY_VARS["OBS_REGION"]:-}"
  [ -z "$val" ] || [[ "$val" =~ ^[A-Za-z0-9._-]+$ ]] \
    || error "OBS_REGION 非法（仅允许字母数字与 ._-）: $val"
  val="${DEPLOY_VARS["REDIS_USER"]:-}"
  [ -z "$val" ] || [[ "$val" =~ ^[A-Za-z0-9._-]+$ ]] \
    || error "REDIS_USER 非法（仅允许字母数字与 ._-）: $val"
}

check_identity_sso_vars() {
  # 仅 up 且目标含 identity 时校验
  if [ "${CMD:-}" != up ] || ! in_array identity "${TARGETS[@]}"; then
    return 0
  fi
  [ -z "${DEPLOY_VARS[IDENTITY_FEDERATION_CONFIG_FILE]:-}" ] || return 0
  local name value
  local -a fields=(IDENTITY_SSO_SERVER_URL IDENTITY_SSO_LOGIN_CLIENT_ID IDENTITY_SSO_TOKEN_AUDIENCE)
  local -a missing=()
  for name in "${fields[@]}"; do
    value="${DEPLOY_VARS[$name]:-}"
    [ -n "${value//[[:space:]]/}" ] || missing+=("$name")
  done
  if [ "${#missing[@]}" -gt 0 ] && [ "${#missing[@]}" -lt "${#fields[@]}" ]; then
    error "Enterprise SSO requires all three settings; missing: ${missing[*]}"
  fi
}


# 集群 API Server 解析与校验（openshift 形态必填；k8s 形态走 kubeconfig 认证，可留空）：
# .env.custom 已设置 → 校验格式后直接使用；
# 留空 → 自动探测回填 DEPLOY_VARS（优先 oc 登录态，其次 kubeconfig 当前上下文），均失败才报错
resolve_api_server_var() {
  is_k8s_cluster && return 0
  local val="${DEPLOY_VARS["CLUSTER_API_SERVER"]:-}"

  # 留空时自动探测：优先 oc 登录态，其次 kubeconfig 当前上下文
  if [ -z "$val" ]; then
    if command -v oc >/dev/null 2>&1; then
      val="$(oc whoami --show-server 2>/dev/null)"
    fi
    if [ -z "$val" ] && command -v kubectl >/dev/null 2>&1; then
      val="$(kubectl config view --minify -o jsonpath='{.clusters[0].cluster.server}' 2>/dev/null)"
    fi
    if [ -n "$val" ]; then
      DEPLOY_VARS["CLUSTER_API_SERVER"]="$val"
      info "CLUSTER_API_SERVER 未设置，已自动探测: $val"
    fi
  fi

  [ -n "$val" ] \
    || error "CLUSTER_API_SERVER 未设置且自动探测失败（检查 $CUSTOM_ENV_FILE，或先 oc login）"
  [[ "$val" =~ ^https?://[A-Za-z0-9._:/-]+$ ]] \
    || error "CLUSTER_API_SERVER 非法（须为 http(s) URL）: $val"
  return 0
}

# MANAGER_WEB_RESOLVER 为空 → 自动解析 manager-web nginx 可用的 DNS resolver；设置有值 → 直接使用：
#   openshift 形态 → OCP CoreDNS 服务名（平台固定）
#   k8s 形态       → kubectl 查 kube-dns/coredns Service ClusterIP
#                    → 本机 /etc/resolv.conf 私网 nameserver（RFC1918 校验，防 render-only 写入公网 DNS）
#                    → kube-dns 服务名兜底
resolve_manager_web_resolver() {
  [ -n "${DEPLOY_VARS["MANAGER_WEB_RESOLVER"]:-}" ] && return 0
  if ! is_k8s_cluster; then
    DEPLOY_VARS["MANAGER_WEB_RESOLVER"]="dns-default.openshift-dns.svc.cluster.local"
    return 0
  fi
  local dns_ip=""
  dns_ip=$(kubectl get svc -n kube-system -l k8s-app=kube-dns \
    -o jsonpath='{.items[0].spec.clusterIP}' 2>/dev/null || true)
  [ -n "$dns_ip" ] || dns_ip=$(kubectl get svc -n kube-system kube-dns \
    -o jsonpath='{.spec.clusterIP}' 2>/dev/null || true)
  [ -n "$dns_ip" ] || dns_ip=$(kubectl get svc -n kube-system coredns \
    -o jsonpath='{.spec.clusterIP}' 2>/dev/null || true)
  if [ -z "$dns_ip" ] && [ -r /etc/resolv.conf ]; then
    local ns; ns=$(awk '/^nameserver[[:space:]]+/ { print $2; exit }' /etc/resolv.conf 2>/dev/null || true)
    if [[ "$ns" =~ ^10\.([0-9]{1,3}\.){2}[0-9]{1,3}$ ]] \
      || [[ "$ns" =~ ^192\.168\.([0-9]{1,3}\.)[0-9]{1,3}$ ]] \
      || [[ "$ns" =~ ^172\.(1[6-9]|2[0-9]|3[0-1])\.([0-9]{1,3}\.)[0-9]{1,3}$ ]]; then
      dns_ip="$ns"
    fi
  fi
  if [ -n "$dns_ip" ]; then
    DEPLOY_VARS["MANAGER_WEB_RESOLVER"]="$dns_ip"
    info "MANAGER_WEB_RESOLVER 自动解析 → $dns_ip"
  else
    DEPLOY_VARS["MANAGER_WEB_RESOLVER"]="kube-dns.kube-system.svc.cluster.local"
    warning "MANAGER_WEB_RESOLVER 解析失败，兜底使用 kube-dns.kube-system.svc.cluster.local"
  fi
  return 0
}

# ---------- 基础服务：内置/外置模式 ----------
# 判据唯一：对应接入地址（DB_HOST / REDIS_HOST）是否为空，与其他变量无关；
# 派生出的 ENABLE_EXTERNAL_DB / ENABLE_EXTERNAL_REDIS 供渲染与依赖预检消费。
# NFS 同类派生在 k8s_handler.sh resolve_nfs_vars（k8s 形态专属）。
#
# 基础服务（mariadb/mysql/postgresql/minio）所在命名空间 BASE_SVC_NS：
#   k8s 形态       → default（集群共享一套公用基础服务，部署工具不在业务 ns 部署这四类组件，
#                     内置 DB/MinIO 地址派生与存在性探测均指向 default）
#   openshift 形态 → 本命名空间（试用集群仅有单 ns 权限，行为与历史一致）
# 注意：Redis 不在共享范围（各 ns 内置自己的 redis，见 resolve_redis_vars）。
resolve_base_svc_ns() {
  if is_k8s_cluster; then
    DEPLOY_VARS["BASE_SVC_NS"]="default"
  else
    DEPLOY_VARS["BASE_SVC_NS"]="${DEPLOY_VARS["NAMESPACE"]}"
  fi
  return 0
}

# 数据库连接检查与派生（DB_HOST 空=内置：headless 服务名按 DB_TYPE+BASE_SVC_NS 派生
# （k8s 形态即复用 default 命名空间的共享实例）；填写=外部数据库，地址原样使用）。
# 端口/用户留空按 DB_TYPE 取默认，
# 密码优先级（语义对齐 jiuwenswarm-ee 的全局统一账号）：
#   外部数据库：DB_PASSWORD > DB_TYPE 对应凭证变量（MARIADB/MYSQL/POSTGRES_ROOT_PASSWORD），均空报错
#   内置数据库：按 DB_TYPE 从对应凭证变量映射到 DB_ROOT_PASSWORD（忽略 DB_PASSWORD）
#               （k8s 形态"内置"= default 共享实例，凭据即共享实例部署时的 root 密码）
resolve_db_vars() {
  local db_type="${DEPLOY_VARS["DB_TYPE"]:-mariadb}"
  [[ "$db_type" =~ ^(mariadb|mysql|postgresql)$ ]] \
    || error "DB_TYPE 非法（mariadb | mysql | postgresql）: $db_type"

  # 内置/外置模式：唯一判据为 DB_HOST 是否为空
  if [ -n "${DEPLOY_VARS["DB_HOST"]:-}" ]; then
    DEPLOY_VARS["ENABLE_EXTERNAL_DB"]="true"     # 外部数据库
  else
    DEPLOY_VARS["ENABLE_EXTERNAL_DB"]="false"    # 内置数据库
    DEPLOY_VARS["DB_HOST"]="${db_type}-headless.${DEPLOY_VARS["BASE_SVC_NS"]}"   # 内置：headless Service DNS（k8s 形态指向 default 共享实例）
  fi

  # 端口 / 用户 / 密码：留空按 DB_TYPE 取默认
  case "$db_type" in
    mariadb)
      [ -n "${DEPLOY_VARS["DB_PORT"]:-}" ] || DEPLOY_VARS["DB_PORT"]="3306"
      [ -n "${DEPLOY_VARS["DB_USER"]:-}" ] || DEPLOY_VARS["DB_USER"]="root"
      DEPLOY_VARS["DB_ROOT_PASSWORD"]="${DEPLOY_VARS["MARIADB_ROOT_PASSWORD"]:-}"
      ;;
    mysql)
      [ -n "${DEPLOY_VARS["DB_PORT"]:-}" ] || DEPLOY_VARS["DB_PORT"]="3306"
      [ -n "${DEPLOY_VARS["DB_USER"]:-}" ] || DEPLOY_VARS["DB_USER"]="root"
      DEPLOY_VARS["DB_ROOT_PASSWORD"]="${DEPLOY_VARS["MYSQL_ROOT_PASSWORD"]:-}"
      ;;
    postgresql)
      [ -n "${DEPLOY_VARS["DB_PORT"]:-}" ] || DEPLOY_VARS["DB_PORT"]="5432"
      [ -n "${DEPLOY_VARS["DB_USER"]:-}" ] || DEPLOY_VARS["DB_USER"]="postgres"
      DEPLOY_VARS["DB_ROOT_PASSWORD"]="${DEPLOY_VARS["POSTGRES_PASSWORD"]:-}"
      ;;
  esac

  # 外部数据库：DB_PASSWORD 为全局统一密码，优先于内置 root 密码变量
  if [ "${DEPLOY_VARS["ENABLE_EXTERNAL_DB"]}" = "true" ]; then
    [ -n "${DEPLOY_VARS["DB_PASSWORD"]:-}" ] \
      && DEPLOY_VARS["DB_ROOT_PASSWORD"]="${DEPLOY_VARS["DB_PASSWORD"]}"
    [ -n "${DEPLOY_VARS["DB_ROOT_PASSWORD"]:-}" ] || error "外部数据库未配置密码：请在 $CUSTOM_ENV_FILE 设置 DB_PASSWORD（或 DB_TYPE 对应的 MARIADB_ROOT_PASSWORD/MYSQL_ROOT_PASSWORD/POSTGRES_PASSWORD）"
  fi
  return 0
}

# Redis 连接检查与派生（REDIS_HOST 空=内置：地址 redis.<NAMESPACE>、强制 standalone；
# 填写=外部 Redis，地址原样使用，cluster 模式走 redis+cluster:// 前缀）
resolve_redis_vars() {
  local redis_mode="${DEPLOY_VARS["REDIS_MODE"]}"
  [[ "$redis_mode" =~ ^(standalone|cluster)$ ]] \
    || error "REDIS_MODE 非法（standalone | cluster）: $redis_mode"

  # 内置/外置模式：唯一判据为 REDIS_HOST 是否为空
  if [ -n "${DEPLOY_VARS["REDIS_HOST"]:-}" ]; then
    DEPLOY_VARS["ENABLE_EXTERNAL_REDIS"]="true"  # 外部 Redis
    # REDIS_USER 留空 = default 用户（华为云 DCS 等托管实例通常只有 default 用户）；
    # 用户显式设置了用户名则原样使用
  else
    # 内置 Redis 是单机实例：强制 standalone（cluster 前缀仅用于外挂集群连接）
    redis_mode="standalone"
    DEPLOY_VARS["REDIS_MODE"]="standalone"
    DEPLOY_VARS["ENABLE_EXTERNAL_REDIS"]="false"
    DEPLOY_VARS["REDIS_HOST"]="jiuwenswarm-redis.${DEPLOY_VARS["NAMESPACE"]}"
    # 内置 Redis 启动参数按 <<REDIS_USER>> 创建 ACL 用户，留空会渲染出空串参数：
    # 未显式设置时派生默认用户名（与 redis.template.yaml 启动参数一致）
    [ -n "${DEPLOY_VARS["REDIS_USER"]:-}" ] || DEPLOY_VARS["REDIS_USER"]="jiuwenswarm"
  fi
  [ -n "${DEPLOY_VARS["REDIS_PORT"]:-}" ] || DEPLOY_VARS["REDIS_PORT"]="6379"

  if [ "$redis_mode" = "cluster" ]; then
    DEPLOY_VARS["REDIS_URL"]="redis+cluster://${DEPLOY_VARS["REDIS_HOST"]}:${DEPLOY_VARS["REDIS_PORT"]}"
  else
    DEPLOY_VARS["REDIS_URL"]="redis://${DEPLOY_VARS["REDIS_HOST"]}:${DEPLOY_VARS["REDIS_PORT"]}"
  fi
  return 0
}


# ---------- 渲染前派生（写入 DEPLOY_VARS，供渲染占位符替换） ----------

# 对象存储解析：OBS_URL 空=内置 MinIO（地址/凭证自动派生自 MinIO root）；
# 填写=外部 OBS/S3 兼容服务（bucket 与 AK/SK 必填）
resolve_obs_vars() {
  if [ -z "${DEPLOY_VARS["OBS_URL"]:-}" ]; then
    # 内置 MinIO：地址/凭证全部强制派生（忽略用户残留的外部 AK/SK，避免凭证错配）
    # 地址指向 BASE_SVC_NS 的 minio-headless（StatefulSet 治理服务，headless DNS 解析到 Pod IP；
    # k8s 形态即 default 命名空间的共享 MinIO，见 resolve_base_svc_ns）
    DEPLOY_VARS["ENABLE_EXTERNAL_OBS"]="false"   # 内置 MinIO
    DEPLOY_VARS["OBS_URL"]="minio-headless.${DEPLOY_VARS["BASE_SVC_NS"]}:9000"
    DEPLOY_VARS["OBS_SECURE"]="false"
    DEPLOY_VARS["OBS_REGION"]="default"
    [ -n "${DEPLOY_VARS["OBS_BUCKET"]:-}" ] || DEPLOY_VARS["OBS_BUCKET"]="jiuwenswarm"
    DEPLOY_VARS["OBS_ACCESS_KEY"]="${DEPLOY_VARS["MINIO_ROOT_USER"]}"
    DEPLOY_VARS["OBS_SECRET_KEY"]="${DEPLOY_VARS["MINIO_ROOT_PASSWORD"]}"
    return 0
  fi
  # 外部 OBS：BUCKET/AK/SK 必填；SECURE/REGION 留空由 SDK 按接入端点自行决定
  DEPLOY_VARS["ENABLE_EXTERNAL_OBS"]="true"      # 外部 OBS
  [ -n "${DEPLOY_VARS["OBS_BUCKET"]:-}" ] || error "OBS_URL 已配置（外部 OBS）：OBS_BUCKET 必填"
  [ -n "${DEPLOY_VARS["OBS_ACCESS_KEY"]:-}" ] || error "OBS_URL 已配置（外部 OBS）：OBS_ACCESS_KEY 必填"
  [ -n "${DEPLOY_VARS["OBS_SECRET_KEY"]:-}" ] || error "OBS_URL 已配置（外部 OBS）：OBS_SECRET_KEY 必填"
  return 0
}

# check_vars 统一入口（deploy.sh main 调用）；kubectl.sh 的 kctl_check_vars 复用部分校验。
# 基础组件（NFS / 数据库 / Redis）的内置/外置派生集中在「基础服务模式」节；
# NFS 相关函数在 k8s_handler.sh（直连挂载仅 k8s 形态使用），MODE=dev 相关在 k8s_handler.sh。
check_vars() {
  check_cluster_type_var
  check_namespace_var
  resolve_base_svc_ns               # 基础服务所在 ns：k8s→default（共享），openshift→本 ns；须先于 DB/OBS 派生
  resolve_api_server_var           # 校验 + 留空时自动探测回填（oc/kubeconfig）
  resolve_nfs_vars                 # 函数在 k8s_handler.sh（k8s 形态专属，含外挂模式派生）
  resolve_db_vars
  resolve_redis_vars
  resolve_obs_vars                 # OBS_URL 空=内置 MinIO 自动派生；外部校验必填项
  resolve_manager_web_resolver     # MANAGER_WEB_RESOLVER 为空时按形态/环境自动解析
  check_image_vars
  check_replicas_vars
  resolve_deploy_mode_vars         # 三模式派生：gateway 条件 env + off 单副本校验
  check_storage_class_vars
  check_optional_vars
  check_identity_sso_vars          # 企业 SSO 三元组"全空或全配"校验（up + identity 时生效，门禁在函数内）
  resolve_dev_mode_vars            # 函数在 k8s_handler.sh（MODE=dev 校验与派生）
}

