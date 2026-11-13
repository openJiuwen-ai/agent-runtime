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

# 沙箱类型：枚举校验（off | jiuwenbox | kata），注入逻辑见 k8s_handler.sh
# apply_sandbox_type_extras / apply_jiuwenbox_extras（渲染后处理）
# - off：保持原样，不注入沙箱运行时
# - kata：AgentServer 沙箱 Pod 注入 runtimeClassName: kata（集群须预装 Kata
#   Containers 并创建 RuntimeClass kata，否则 Pod 无法启动）
# - jiuwenbox：AgentServer Pod 注入 jiuwenbox 沙箱 sidecar（须配 JIUWENBOX_IMAGE/
#   JIUWENBOX_PORT；仅 k8s 形态，openshift SCC 拒绝特权容器；sidecar 需 root +
#   SYS_ADMIN/NET_ADMIN + seccomp/AppArmor Unconfined）
resolve_sandbox_type_vars() {
  local val="${DEPLOY_VARS["SANDBOX_TYPE"]:-}"
  case "$val" in
    off|kata) ;;
    jiuwenbox)
      # 沙箱需 root + SYS_ADMIN/NET_ADMIN + seccomp/AppArmor Unconfined 运行，
      # OCP 默认 restricted-v2 全部拒绝，需客户手工绑 privileged SCC——暂不支持
      if [ "${DEPLOY_VARS["CLUSTER_TYPE"]}" = openshift ]; then
        error "CLUSTER_TYPE=openshift 暂不支持 SANDBOX_TYPE=jiuwenbox（沙箱需特权容器能力，待开放）"
      fi
      local img="${DEPLOY_VARS["JIUWENBOX_IMAGE"]:-}"
      [ -n "$img" ] \
        || error "SANDBOX_TYPE=jiuwenbox 需要 JIUWENBOX_IMAGE（检查 $CUSTOM_ENV_FILE）"
      [[ "$img" =~ ^[A-Za-z0-9._:@/-]+$ ]] \
        || error "JIUWENBOX_IMAGE 含非法字符（仅允许字母数字._:@/-）: $img"
      # 端口：1-65535 正整数（sidecar containerPort 与 JIUWENBOX_URL 同源引用）；
      # 留空沿用内置默认 8321
      local port="${DEPLOY_VARS["JIUWENBOX_PORT"]:-8321}"
      [[ "$port" =~ ^[1-9][0-9]*$ ]] && [ "$port" -le 65535 ] 2>/dev/null \
        || error "JIUWENBOX_PORT 非法（须为 1-65535 的正整数）: $port";;
    *)
      error "SANDBOX_TYPE 非法（off | jiuwenbox | kata）: $val";;
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

check_optional_vars() {
  local val
  val="${DEPLOY_VARS["OBS_PUBLIC_BASE_URL"]:-}"
  [ -z "$val" ] || [[ "$val" =~ ^(https?://)?[A-Za-z0-9._:/-]+$ ]] \
    || error "OBS_PUBLIC_BASE_URL 非法（须为 http(s) URL 或域名）: $val"
  val="${DEPLOY_VARS["TEAM_SKILLS_HUB_BASE_URL"]:-}"
  [ -z "$val" ] || [[ "$val" =~ ^(https?://)?[A-Za-z0-9._:/-]+$ ]] \
    || error "TEAM_SKILLS_HUB_BASE_URL 非法（须为 http(s) URL 或域名）: $val"
  val="${DEPLOY_VARS["OBS_SECURE"]:-}"
  [ -z "$val" ] || [[ "$val" =~ ^(true|false)$ ]] \
    || error "OBS_SECURE 非法（须为 true 或 false）: $val"
  val="${DEPLOY_VARS["OBS_REGION"]:-}"
  [ -z "$val" ] || [[ "$val" =~ ^[A-Za-z0-9._-]+$ ]] \
    || error "OBS_REGION 非法（仅允许字母数字与 ._-）: $val"
  val="${DEPLOY_VARS["REDIS_USER"]:-}"
  [ -z "$val" ] || [[ "$val" =~ ^[A-Za-z0-9._-]+$ ]] \
    || error "REDIS_USER 非法（仅允许字母数字与 ._-）: $val"
  # 下载域名白名单：逗号分隔 host 规则（支持 * 通配、.suffix）
  for _host_var in IMPORT_LOCAL_ALLOWED_DOWNLOAD_HOSTS TEAM_SKILLS_HUB_ALLOWED_DOWNLOAD_HOSTS; do
    val="${DEPLOY_VARS[$_host_var]:-}"
    [ -z "$val" ] || [[ "$val" =~ ^[A-Za-z0-9.*,_-]+$ ]] \
      || error "${_host_var} 非法（逗号分隔主机规则，仅允许字母数字与 .*,_-）: $val"
  done
  val="${DEPLOY_VARS["AGENT_SERVER_REACT_MAX_ITER"]:-}"
  [ -z "$val" ] || [[ "$val" =~ ^[1-9][0-9]*$ ]] \
    || error "AGENT_SERVER_REACT_MAX_ITER 非法（留空表示不限制，或填写正整数）: $val"
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
  # down 不渲染清单，解析 resolver 无意义，静默跳过
  [ "${CMD:-}" = down ] && return 0
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
# 基础服务（mariadb/mysql/postgresql/minio）所在命名空间 BASE_SVC_NS——集群内未命中
# 任何实例时的部署预期位置（k8s=default 共享 / openshift=本 ns）。
# 内置实例实际消费位置由 resolve_builtin_svc_locations 在 up 前动态定位（本 ns → default）。
# Redis 不在共享范围（各 ns 内置自己的 redis，见 resolve_redis_vars）。
resolve_base_svc_ns() {
  if is_k8s_cluster; then
    DEPLOY_VARS["BASE_SVC_NS"]="default"
  else
    DEPLOY_VARS["BASE_SVC_NS"]="${DEPLOY_VARS["NAMESPACE"]}"
  fi
  return 0
}

# 内置基础服务实例定位：本次部署目标内的组件直接定位本 ns（实例由本次部署创建），
# 其余按 本 ns → default 顺序探测，命中即改写 DB_HOST/OBS_URL 指向实际实例；
# 均未命中维持 BASE_SVC_NS 静态派生值（由部署/preflight 接手）。
# 外挂模式（DB_HOST/OBS_URL 已配置）地址原样使用，不经本函数。
# 须在 kctl_check_vars 之后调用（探测走集群通道）；--render-only 不经过本函数
resolve_builtin_svc_locations() {
  local svc ns host
  if [ "${DEPLOY_VARS["ENABLE_EXTERNAL_DB"]}" != "true" ]; then
    svc="${DEPLOY_VARS["DB_TYPE"]}"
    ns=$(component_ns "$svc")
    host="${svc}-headless.${ns}"
    if [ "$host" != "${DEPLOY_VARS["DB_HOST"]}" ]; then
      info "内置 ${svc} 实例定位: ${DEPLOY_VARS["DB_HOST"]} → ${host}"
      DEPLOY_VARS["DB_HOST"]="$host"
    fi
  fi
  if [ "${DEPLOY_VARS["ENABLE_EXTERNAL_OBS"]}" != "true" ]; then
    ns=$(component_ns "minio")
    host="minio-headless.${ns}:9000"
    if [ "$host" != "${DEPLOY_VARS["OBS_URL"]}" ]; then
      info "内置 minio 实例定位: ${DEPLOY_VARS["OBS_URL"]} → ${host}"
      DEPLOY_VARS["OBS_URL"]="$host"
    fi
  fi
  return 0
}
# openshift 形态 StorageClass 解析（须在集群可达后执行，调用点见 deploy.sh up 分支）：
# DATA_STORAGE_CLASS/WORKSPACE_STORAGE_CLASS 留空时自动指向内置 nfs-sc 供给组件生成的 StorageClass
# （NFS_SC_NAME，默认 jiuwenswarm-storage）；该供给组件未部署时直接报错退出——
# 避免 PVC 静默悬空（SC 为 null 且集群无默认 StorageClass）导致 Pod 卡 Pending。
# 显式配置的值视为外挂 StorageClass，不覆盖（见集群部署指南 2.1.3）。
# 本次目标含 nfs-sc 时 check_nfs_component_vars 已先行回填（SC 由本次创建，无需查集群），
# 此函数仅兜底业务组件单独部署（targets 不含 nfs-sc）的场景。
# k8s 形态数据卷走 NFS 直连挂载、不渲染 PVC，无需解析，直接跳过。
# 存储类派生（MOUNT_TYPE=sc / openshift 形态）：DATA/WORKSPACE_STORAGE_CLASS 留空时，
# 若 nfs-sc 供给组件在本次部署目标中、或已存在于集群（本 ns → default 定位），自动指向
# 其生成的 StorageClass（NFS_SC_NAME）；用户已配置视为外挂 StorageClass，不覆盖。
# 自动填充后校验 StorageClass 真实存在（404 → 指引部署 nfs-sc 或显式配置）。
# --render-only 不经过本函数（不连集群，无法探测；nfs-sc 在目标中时由
# check_nfs_component_vars 在 check_vars 阶段填充）。
# 须在 kctl_check_vars 之后调用（探测走集群通道）。
resolve_storage_class_vars() {
  # 存储类解析（sc 模式 / openshift 形态）：为 DATA/WORKSPACE_STORAGE_CLASS 解析出
  # 确定可用的 StorageClass，渲染的 PVC 全部显式指定，杜绝 Pod 因 PVC 未绑定卡 Pending：
  #   1. 已配置 → 校验集群中存在，不存在报错退出；
  #   2. 留空且集群中存在 NFS 存储供给组件（本 ns → default 定位）→ 指向其 StorageClass（NFS_SC_NAME）；
  #   3. 留空且无供给组件 → 使用集群默认 StorageClass（is-default-class 注解），无默认报错退出。
  # nfs 直连模式不消费存储类，直接跳过。--render-only 不经过本函数（不连集群）。
  # 须在 kctl_check_vars 之后调用（探测走集群通道）。
  is_k8s_cluster && [ "${DEPLOY_VARS["MOUNT_TYPE"]:-sc}" != sc ] && return 0
  local out code
  out="$(kctl_tmpdir)/sc-resolve.json"
  local v val
  # 本次目标含 nfs-sc：指向 NFS_SC_NAME 的值由本次创建，集群中尚不存在，
  # 跳过存在性校验（用户配置的其他值仍须已存在）
  local sc_pending=0
  in_array nfs-sc "${TARGETS[@]:-}" && sc_pending=1
  for v in "${STORAGE_CLASS_VARS[@]}"; do
    val="${DEPLOY_VARS[$v]:-}"
    [ -n "$val" ] || continue
    [ "$sc_pending" = 1 ] && [ "$val" = "${DEPLOY_VARS["NFS_SC_NAME"]}" ] && continue
    code=$(kctl_http GET "$(kctl_kind_path StorageClass)/$val" "$out")
    [ "$code" = 200 ] \
      || error "StorageClass $val 不存在（HTTP $code）：$v 配置有误，请修正，或部署 NFS 存储供给组件（./deploy.sh up nfs）"
    info "$v → $val（已确认集群中存在）"
  done
  local need_resolve=0
  for v in "${STORAGE_CLASS_VARS[@]}"; do
    [ -z "${DEPLOY_VARS[$v]:-}" ] && need_resolve=1
  done
  [ "$need_resolve" = 1 ] || return 0
  # 留空的变量：优先指向 NFS 存储供给组件的 StorageClass
  local prov_exists=0
  in_array nfs-sc "${TARGETS[@]:-}" && prov_exists=1
  if [ "$prov_exists" = 0 ] && component_probe nfs-sc >/dev/null 2>&1; then
    [ "$(component_probe_code nfs-sc)" = 200 ] && prov_exists=1
  fi
  if [ "$prov_exists" = 1 ]; then
    for v in "${STORAGE_CLASS_VARS[@]}"; do
      [ -n "${DEPLOY_VARS[$v]:-}" ] && continue
      DEPLOY_VARS[$v]="${DEPLOY_VARS["NFS_SC_NAME"]}"
      code=$(kctl_http GET "$(kctl_kind_path StorageClass)/${DEPLOY_VARS[$v]}" "$out")
      [ "$code" = 200 ] \
        || error "StorageClass ${DEPLOY_VARS[$v]} 不存在（HTTP $code）：供给组件在但 StorageClass 缺失，请检查 nfs-sc 部署状态"
      info "$v → ${DEPLOY_VARS[$v]}（NFS 存储供给组件）"
    done
    return 0
  fi
  # 无供给组件：使用集群默认 StorageClass（is-default-class 注解）
  code=$(kctl_http GET "/apis/storage.k8s.io/v1/storageclasses" "$out")
  [ "$code" = 200 ] || error "无法列举集群 StorageClass（HTTP $code）：请检查集群连接与凭证"
  local default_sc
  default_sc=$(yq -r '([.items[] | select(.metadata.annotations."storageclass.kubernetes.io/is-default-class" == "true")][0].metadata.name) // ""' "$out" 2>/dev/null)
  [ -n "$default_sc" ] \
    || error "集群无默认 StorageClass，且 NFS 存储供给组件未部署：PVC 将无法绑定（Pod 卡 Pending）。
请任选其一：./deploy.sh up nfs 部署 NFS 存储供给组件（自动解析为 nfs-sc）；或在 .env.custom 配置 DATA_STORAGE_CLASS / WORKSPACE_STORAGE_CLASS；或为集群中某个 StorageClass 添加默认注解"
  for v in "${STORAGE_CLASS_VARS[@]}"; do
    [ -n "${DEPLOY_VARS[$v]:-}" ] && continue
    DEPLOY_VARS[$v]="$default_sc"
    info "$v → 集群默认 StorageClass $default_sc"
  done
}

# 数据库连接检查与派生。DB_HOST 空=内置：headless 服务名按 DB_TYPE+BASE_SVC_NS 静态派生
# （up 时由 resolve_builtin_svc_locations 按实况改写）；填写=外部数据库，地址原样使用。
# 端口/用户留空按 DB_TYPE 取默认，
# 密码优先级：
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

  resolve_module_db_vars
  return 0
}

# 分库配置派生：
#   <MODULE>_DB_NAME：mysql/mariadb → <module>_<NAMESPACE>（按 db 隔离实例）；
#                     postgresql → <module>（按 db 隔离实例）；用户已设则原样
#   <MODULE>_PG_SCHEMA：仅 postgresql 生效，未设取 NAMESPACE（按 schema 隔离实例）；
#                     mysql/mariadb 下留空，占位符行经 OPTIONAL_VARS 删除
#   <MODULE>_DB_USER / <MODULE>_DB_PASSWORD：模块专属优先，未设继承全局统一账号
#                     （DB_USER / DB_ROOT_PASSWORD），最终值必非空
# 模块集：GATEWAY WEB IDENTITY MANAGER RUNTIME；AGENTSERVER 密码跟随 GATEWAY
# （agentserver 连接 gateway 库，secret 中 AGENTSERVER_DB_PASSWORD 渲染 GATEWAY 值）
resolve_module_db_vars() {
  local module lmodule var
  for module in GATEWAY WEB IDENTITY MANAGER RUNTIME; do
    lmodule="${module,,}"
    var="${module}_DB_NAME"
    if [ -z "${DEPLOY_VARS[$var]:-}" ]; then
      if [ "${DEPLOY_VARS["DB_TYPE"]}" = "postgresql" ]; then
        DEPLOY_VARS[$var]="$lmodule"
      else
        DEPLOY_VARS[$var]="${lmodule}_${DEPLOY_VARS["NAMESPACE"]}"
      fi
    fi
    var="${module}_PG_SCHEMA"
    if [ -z "${DEPLOY_VARS[$var]:-}" ] && [ "${DEPLOY_VARS["DB_TYPE"]}" = "postgresql" ]; then
      DEPLOY_VARS[$var]="${DEPLOY_VARS["NAMESPACE"]}"
    fi
    var="${module}_DB_USER"
    if [ -z "${DEPLOY_VARS[$var]:-}" ]; then
      DEPLOY_VARS[$var]="${DEPLOY_VARS["DB_USER"]}"
    fi
    var="${module}_DB_PASSWORD"
    if [ -z "${DEPLOY_VARS[$var]:-}" ]; then
      DEPLOY_VARS[$var]="${DEPLOY_VARS["DB_ROOT_PASSWORD"]}"
    fi
  done
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
    # 内置 MinIO：地址/凭证强制派生（忽略残留的外部 AK/SK，避免凭证错配）。
    # 地址静态指向 BASE_SVC_NS 的 minio-headless，up 时由 resolve_builtin_svc_locations 按实况改写
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
# nfs / nfs-sc 组件目标守卫（TARGETS 含二者之一时生效）。
# nfs 为部署/卸载的唯一用户入口目标（nfs-sc 显式指定仅限 up --render-only 手动渲染入口，
# parse_args 校验），实际组件集由 filter_targets_by_nfs_platform 按形态/外挂 NFS/节点架构
# 解析——进入本函数时 nfs 仅存在于 k8s 内置模式（openshift / 外部 NFS 场景已解析为 nfs-sc）。
# nfs-server（仅 k8s 形态）：命名空间规则与 mariadb 等基础服务一致——部署落 NAMESPACE，
# 消费按 本 ns → default 定位；卸载均经 component_ns(nfs) 定位（与 deploy_component 的
# ns 覆写一致）。
# nfs-sc（双形态通用）：openshift 形态无内置 NFS server，须指定外挂地址。
# 前置：parse_args 已完成（TARGETS 可用）、resolve_nfs_vars 已派生 ENABLE_EXTERNAL_NFS
check_nfs_component_vars() {
  # down 只消费 conf/ 现有清单卸载，不做渲染前置校验与解析，静默跳过
  [ "${CMD:-}" = down ] && return 0
  # MOUNT_TYPE（k8s 形态存储挂载模式）校验
  if is_k8s_cluster; then
    [[ "${DEPLOY_VARS["MOUNT_TYPE"]:-sc}" =~ ^(nfs|sc)$ ]]       || error "MOUNT_TYPE 非法（仅支持 nfs | sc）: ${DEPLOY_VARS["MOUNT_TYPE"]}"
    [ "${DEPLOY_VARS["MOUNT_TYPE"]}" = sc ] && [ "${DEPLOY_VARS["RENDER_ONLY"]}" != "true" ]       && [ -z "${DEPLOY_VARS["DATA_STORAGE_CLASS"]}" ] && [ -z "${DEPLOY_VARS["WORKSPACE_STORAGE_CLASS"]}" ]       && ! in_array nfs-sc "${TARGETS[@]:-}"       && info "存储类未配置，将自动解析（已有供给组件优先，其次集群默认 StorageClass）"
  fi
  local has_nfs=0 has_sc=0
  in_array nfs "${TARGETS[@]:-}" && has_nfs=1
  in_array nfs-sc "${TARGETS[@]:-}" && has_sc=1
  [ "$has_nfs" = 1 ] || [ "$has_sc" = 1 ] || return 0
  # nfs-server：仅 k8s 形态内置模式可达（openshift / 外部 NFS 已由
  # filter_targets_by_nfs_platform 解析为 nfs-sc）。
  # 部署守卫仅对 up 生效——down 是卸载操作，不得被"无需部署"类检查拦截
  if [ "$has_nfs" = 1 ]; then
    if [ "$CMD" = up ]; then
      is_k8s_cluster \
        || error "nfs 组件仅 CLUSTER_TYPE=k8s 支持：openshift 形态存储走 PVC，不使用 NFS server 组件"
    fi
    # 固定调度节点识别：up（连集群）必须成功——nfs-server 的 nodeName 与导出根
    # hostPath 都以部署节点为准；--render-only 离线渲染允许留空（nodeName 整行
    # 删除 + apply_nfs_extras 告警，正式部署须在集群可达环境重新渲染）
    [ "${DEPLOY_VARS["RENDER_ONLY"]}" = "true" ] || fetch_current_node_name
  fi
  # nfs-sc：双形态通用，数据源必须可达
  if [ "$has_sc" = 1 ]; then
    if is_k8s_cluster && [ "${DEPLOY_VARS["ENABLE_EXTERNAL_NFS"]}" != "true" ]; then
      # k8s 内置模式：数据源 = 内置 NFS server 的节点地址（须先 up nfs，此处自动解析回填）
      [ -n "${DEPLOY_VARS["NFS_SERVER_ADDR"]}" ] || {
        local ip
        ip=$(_resolve_builtin_nfs_server) || ip=""
        if [ -z "$ip" ] && in_array nfs "${TARGETS[@]:-}"; then
          # 本次 up 一并拉起内置 nfs-server（filter_targets_by_nfs_platform 补齐）：
          # Pod 尚未运行，解析回退到部署节点 InternalIP——nfs-server hostNetwork
          # 绑定节点 2049，供给器 Pod 就绪重试后即可连上
          ip=$(kubectl get node "${DEPLOY_VARS["CURRENT_NODE_NAME"]:-}" \
            -o jsonpath='{.status.addresses[?(@.type=="InternalIP")].address}' 2>/dev/null)
          [ -n "$ip" ] && info "nfs-sc 数据源: 内置 NFS server 部署节点 ${DEPLOY_VARS["CURRENT_NODE_NAME"]}（本次一并拉起，待其就绪）"
        fi
        [ -n "$ip" ] \
          || error "内置模式下 nfs-sc 渲染须解析内置 NFS server 地址（见 stderr）；或改填外部 NFS_SERVER_ADDR"
        DEPLOY_VARS["NFS_SERVER_ADDR"]="$ip"
        info "nfs-sc 数据源: 内置 NFS server（${ip}）"
      }
    elif ! is_k8s_cluster && [ -z "${DEPLOY_VARS["NFS_SERVER_ADDR"]}" ]; then
      error "openshift 形态部署 nfs-sc 须指定外挂 NFS server：请在 .env.custom 填写 NFS_SERVER_ADDR（内置 NFS server 组件仅 k8s 形态支持）"
    fi
    # 内置 nfs-sc 供给模式：存储类留空时自动指向供给器生成的 StorageClass；
    # 用户已配置视为外挂 StorageClass（见 2.1.3），不覆盖
    local auto_data=0 auto_ws=0
    [ -n "${DEPLOY_VARS["DATA_STORAGE_CLASS"]}" ] \
      || { DEPLOY_VARS["DATA_STORAGE_CLASS"]="${DEPLOY_VARS["NFS_SC_NAME"]}"; auto_data=1; }
    [ -n "${DEPLOY_VARS["WORKSPACE_STORAGE_CLASS"]}" ] \
      || { DEPLOY_VARS["WORKSPACE_STORAGE_CLASS"]="${DEPLOY_VARS["NFS_SC_NAME"]}"; auto_ws=1; }
    [ "$auto_data" = 1 ] && info "DATA_STORAGE_CLASS → ${DEPLOY_VARS["DATA_STORAGE_CLASS"]}（NFS 存储供给组件）"
    [ "$auto_ws" = 1 ] && info "WORKSPACE_STORAGE_CLASS → ${DEPLOY_VARS["WORKSPACE_STORAGE_CLASS"]}（NFS 存储供给组件）"
  fi
}

check_vars() {
  check_cluster_type_var
  check_namespace_var
  resolve_base_svc_ns               # 基础服务所在 ns：k8s→default（共享），openshift→本 ns；须先于 DB/OBS 派生
  resolve_api_server_var           # 校验 + 留空时自动探测回填（oc/kubeconfig）
  resolve_nfs_vars                 # 函数在 k8s_handler.sh（k8s 形态专属，含外挂模式派生）
  check_nfs_component_vars         # nfs 组件目标守卫（仅 TARGETS 含 nfs 时生效）
  resolve_db_vars
  resolve_redis_vars
  resolve_obs_vars                 # OBS_URL 空=内置 MinIO 自动派生；外部校验必填项
  resolve_manager_web_resolver     # MANAGER_WEB_RESOLVER 为空时按形态/环境自动解析
  check_image_vars
  check_replicas_vars
  resolve_deploy_mode_vars         # 三模式派生：gateway 条件 env + off 单副本校验
  resolve_sandbox_type_vars        # 沙箱类型枚举校验（off|kata|jiuwenbox，jiuwenbox 仅 k8s）
  check_storage_class_vars
  check_optional_vars
  check_identity_sso_vars          # 企业 SSO 三元组"全空或全配"校验（up + identity 时生效，门禁在函数内）
  resolve_dev_mode_vars            # 函数在 k8s_handler.sh（MODE=dev 校验与派生）
}

