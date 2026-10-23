#!/usr/bin/env bash
[ -n "${_DEPLOY_VAR_HANDLER_SH:-}" ] && return 0
_DEPLOY_VAR_HANDLER_SH=1

# var_handler.sh —— 部署变量校验
# check_vars 统一入口（deploy.sh main 调用）；集群级三项（CLUSTER_TYPE/API_SERVER/NAMESPACE）
# 为公共校验，kubectl.sh 的 kctl_check_vars 复用

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

# namespace：k8s DNS label 规范
check_namespace_var() {
  local val="${DEPLOY_VARS["NAMESPACE"]:-}"
  [ -n "$val" ] || error "NAMESPACE 未设置（检查 $CUSTOM_ENV_FILE）"
  [[ "$val" =~ ^[a-z0-9]([-a-z0-9]*[a-z0-9])?$ ]] \
    || error "NAMESPACE 非法（须为 DNS label：小写字母数字与 -）: $val"
}

# 数据库类型：mariadb | mysql | postgresql
check_db_type_var() {
  local val="${DEPLOY_VARS["DB_TYPE"]}"
  [[ "$val" =~ ^(mariadb|mysql|postgresql)$ ]] || error "DB_TYPE 非法（mariadb | mysql | postgresql）: $val"
}

# 集群形态：openshift | k8s
check_cluster_type_var() {
  local val="${DEPLOY_VARS["CLUSTER_TYPE"]}"
  [[ "$val" =~ ^(openshift|k8s)$ ]] || error "CLUSTER_TYPE 非法（openshift | k8s）: $val"
}

# 自动探测 API Server：优先 oc 登录态，其次 kubeconfig 当前上下文
detect_api_server() {
  local detected=""
  if command -v oc >/dev/null 2>&1; then
    detected="$(oc whoami --show-server 2>/dev/null)" && [ -n "$detected" ] && { printf '%s\n' "$detected"; return 0; }
  fi
  if command -v kubectl >/dev/null 2>&1; then
    detected="$(kubectl config view --minify -o jsonpath='{.clusters[0].cluster.server}' 2>/dev/null)" \
      && [ -n "$detected" ] && { printf '%s\n' "$detected"; return 0; }
  fi
  return 1
}

# 集群 API Server：openshift 形态必填；k8s 形态走 kubeconfig 认证，可留空
# .env.custom 留空时自动探测（oc/kubeconfig），探测成功回填 DEPLOY_VARS，均失败才报错
check_api_server_var() {
  is_k8s_cluster && return 0
  local val="${DEPLOY_VARS["CLUSTER_API_SERVER"]:-}"
  if [ -z "$val" ] && val="$(detect_api_server)"; then
    DEPLOY_VARS["CLUSTER_API_SERVER"]="$val"
    info "CLUSTER_API_SERVER 未设置，已自动探测: $val"
  fi
  [ -n "$val" ] || error "CLUSTER_API_SERVER 未设置且自动探测失败（检查 $CUSTOM_ENV_FILE，或先 oc login）"
  [[ "$val" =~ ^https?://[A-Za-z0-9._:/-]+$ ]] \
    || error "CLUSTER_API_SERVER 非法（须为 http(s) URL）: $val"
}

# 副本数：正整数
check_replicas_vars() {
  local v val
  for v in "${REPLICAS_VARS[@]}"; do
    val="${DEPLOY_VARS[$v]:-}"
    [[ "$val" =~ ^[1-9][0-9]*$ ]] || error "$v 非法（须为正整数）: $val"
  done
}

# StorageClass：DNS subdomain 规范；留空使用集群默认（OPTIONAL_VARS）
check_storage_class_vars() {
  local v val
  for v in "${OPTIONAL_VARS[@]}"; do
    val="${DEPLOY_VARS[$v]:-}"
    [ -z "$val" ] || [[ "$val" =~ ^[a-z0-9]([-a-z0-9.]*[a-z0-9])?$ ]] \
      || error "$v 非法（须为 DNS subdomain：小写字母数字与 -.）: $val"
  done
}

# 变量校验统一入口：逐类分派
check_vars() {
  check_cluster_type_var
  check_db_type_var
  check_image_vars
  check_namespace_var
  check_api_server_var
  check_replicas_vars
  check_storage_class_vars
  check_nfs_vars
  check_dev_mode_vars
}
