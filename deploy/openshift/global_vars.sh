#!/usr/bin/env bash
# global_vars.sh —— 全局变量与默认配置（组织方式对齐 ../jiuwenswarm-ee/global_vars.sh）
# DEPLOY_VARS 提供默认值，.env.custom 覆盖默认值（见 envfile_handler.sh 的 read_env_from_file）

# SCRIPT_DIR 由入口 deploy.sh 定义；独立 source 时自行推导
SCRIPT_DIR="${SCRIPT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
CONF_DIR="$SCRIPT_DIR/conf"
TEMPLATE_DIR="$SCRIPT_DIR/templates"
CUSTOM_ENV_FILE="$SCRIPT_DIR/.env.custom"

# ---------- 组件清单 ----------

# 全部组件的规范依赖序（up 按此序排列；down 倒序）。
# 基础服务（mariadb/mysql/postgresql/minio）可显式部署；secret：全组件共用的配置 Secret，
# 与 redis 同为共享依赖，随用随补、无人使用才可删
ALL_COMPONENTS=(mariadb mysql postgresql minio secret redis agentserver gateway web identity manager-server manager-web)

UP_TARGETS=(secret redis agentserver gateway web)         # up 默认目标（规范依赖序）
MANAGER_TARGETS=(identity manager-server manager-web)     # manager 组

# 镜像名变量清单，与 templates/ 下 <<XXX_IMAGE>> 占位符一一对应
IMAGE_VARS=(AGENTSERVER_IMAGE GATEWAY_IMAGE WEB_IMAGE IDENTITY_IMAGE MANAGER_SERVER_IMAGE MANAGER_WEB_IMAGE)

# ---------- 部署变量默认值 ----------

declare -A DEPLOY_VARS=(
    ["NAMESPACE"]="rapheale-dev"
    ["DB_NAME_SUFFIX"]="rapheale-dev"
    ["RENDER_ONLY"]="false"
    ["AGENTSERVER_IMAGE"]="swr.cn-north-4.myhuaweicloud.com/openjiuwen/jiuwenclaw-core-ocp-amd64:0.0.4z"
    ["GATEWAY_IMAGE"]="swr.cn-north-4.myhuaweicloud.com/openjiuwen/jiuwenclaw-core-ocp-amd64:0.0.4z"
    ["IDENTITY_IMAGE"]="swr.cn-north-4.myhuaweicloud.com/openjiuwen/jiuwenclaw-identity-ocp-amd64:0.0.4z"
    ["MANAGER_SERVER_IMAGE"]="swr.cn-north-4.myhuaweicloud.com/openjiuwen/jiuwenclaw-manager-server-ocp-amd64:0.0.4z"
    ["MANAGER_WEB_IMAGE"]="swr.cn-north-4.myhuaweicloud.com/openjiuwen/jiuwenclaw-manager-web-ocp-amd64:0.0.4z"
    ["WEB_IMAGE"]="swr.cn-north-4.myhuaweicloud.com/openjiuwen/jiuwenclaw-web-ocp-amd64:0.0.4z"
)

# ---------- 通用工具 ----------

in_array() {
  local v; for v in "${@:2}"; do [ "$v" = "$1" ] && return 0; done; return 1
}

# ---------- 变量校验 ----------

# 变量值会内嵌进渲染（awk gsub）与 yq 表达式/REST 路径，统一做格式校验防止注入与拼坏 yaml
check_vars() {
  local v val
  # 镜像名：覆盖 registry:port/repo:tag 与 @sha256 摘要形态
  for v in "${IMAGE_VARS[@]}"; do
    val="${DEPLOY_VARS[$v]:-}"
    [ -n "$val" ] || error "$v 未设置（检查 $CUSTOM_ENV_FILE）"
    [[ "$val" =~ ^[A-Za-z0-9._:@/-]+$ ]] \
      || error "$v 含非法字符（仅允许字母数字._:@/-）: $val"
  done
  # namespace：k8s DNS label 规范
  val="${DEPLOY_VARS["NAMESPACE"]:-}"
  [ -n "$val" ] || error "NAMESPACE 未设置（检查 $CUSTOM_ENV_FILE）"
  [[ "$val" =~ ^[a-z0-9]([-a-z0-9]*[a-z0-9])?$ ]] \
    || error "NAMESPACE 非法（须为 DNS label：小写字母数字与 -）: $val"
  # 库名后缀：mariadb 已建库以 <业务>_DB_NAME_SUFFIX 命名，独立于 namespace（换 ns 不换库）
  val="${DEPLOY_VARS["DB_NAME_SUFFIX"]:-}"
  [ -n "$val" ] || error "DB_NAME_SUFFIX 未设置（检查 $CUSTOM_ENV_FILE）"
  [[ "$val" =~ ^[A-Za-z0-9_-]+$ ]] \
    || error "DB_NAME_SUFFIX 非法（仅允许字母数字与 _-）: $val"
}
