#!/usr/bin/env bash
# global_vars.sh —— 全局变量与默认配置
# DEPLOY_VARS 仅提供通用默认值；镜像名等环境相关值一律在 .env.custom 维护（缺失时
# check_vars 报错提示，见 envfile_handler.sh 的 read_env_from_file）

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

# 副本数变量清单，与 templates/ 下 <<XXX_REPLICAS>> 占位符一一对应
REPLICAS_VARS=(AGENT_SERVER_REPLICAS GATEWAY_REPLICAS WEB_REPLICAS IDENTITY_REPLICAS MANAGER_SERVER_REPLICAS MANAGER_WEB_REPLICAS)

# ---------- 部署变量默认值 ----------

declare -A DEPLOY_VARS=(
    ["NAMESPACE"]="rapheale-dev"
    ["RENDER_ONLY"]="false"
    # 组件副本数（.env.custom 可覆盖，改后重跑 up 生效）
    ["AGENT_SERVER_REPLICAS"]="1"
    ["GATEWAY_REPLICAS"]="3"
    ["WEB_REPLICAS"]="2"
    ["IDENTITY_REPLICAS"]="1"
    ["MANAGER_SERVER_REPLICAS"]="2"
    ["MANAGER_WEB_REPLICAS"]="2"
)

# ---------- 通用工具 ----------

in_array() {
  local v; for v in "${@:2}"; do [ "$v" = "$1" ] && return 0; done; return 1
}

# ---------- 变量校验 ----------

# 变量值会内嵌进渲染（awk gsub）与 yq 表达式/REST 路径，统一做格式校验防止注入与拼坏 yaml
check_vars() {
  local v val
  # 镜像名（仅由 .env.custom 提供，无内置默认）：校验非空；格式覆盖
  # registry:port/repo:tag 与 @sha256 摘要形态
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
  # 副本数：正整数
  for v in "${REPLICAS_VARS[@]}"; do
    val="${DEPLOY_VARS[$v]:-}"
    [[ "$val" =~ ^[1-9][0-9]*$ ]] || error "$v 非法（须为正整数）: $val"
  done
}
