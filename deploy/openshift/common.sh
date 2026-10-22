#!/usr/bin/env bash

[ -n "${_DEPLOY_COMMON_SH:-}" ] && return 0
_DEPLOY_COMMON_SH=1

# common.sh —— 日志与公共工具函数（被其余所有脚本首先 source）

info() { echo -e "\033[36m=== $@ ===\033[0m"; }
success() { echo -e "\033[32m✅ $@\033[0m"; }
warning() { echo -e "\033[33m⚠️  $@\033[0m"; }
error() { echo -e "\033[31m❌ $@\033[0m"; exit 1; }

in_array() {
  local v; for v in "${@:2}"; do [ "$v" = "$1" ] && return 0; done; return 1
}

# 集群形态判断
is_k8s_cluster() { [ "${DEPLOY_VARS["CLUSTER_TYPE"]}" = k8s ]; }

# 逐一校验命令可用（任一缺失报错退出）
require_cmds() {
  local cmd
  for cmd in "$@"; do
    command -v "$cmd" >/dev/null 2>&1 || error "缺少依赖: $cmd"
  done
}