#!/usr/bin/env bash

[ -n "${_DEPLOY_COMMON_SH:-}" ] && return 0
_DEPLOY_COMMON_SH=1

# common.sh —— 日志与公共工具函数（被其余所有脚本首先 source）

# ---------- 结构化进度（程序化调用） ----------
# DEPLOY_JSON_PROGRESS=1 时，主流程经 progress_json 向 stdout 输出机器可读进度行：
#   @@DEPLOY_JSON@@{"event":"phase",    "phase":"validate|render|preflight|apply",  "state":"start|ok|fail"}
#   @@DEPLOY_JSON@@{"event":"component","component":"gateway","index":"3","total":"5","state":"start|ok|fail"}
#   @@DEPLOY_JSON@@{"event":"summary",  "status":"success|failed","elapsed":"123"}
# 键值一律字符串（调用方自行转数值）；值经 JSON 转义，保证单行可解析
DEPLOY_JSON_PROGRESS="${DEPLOY_JSON_PROGRESS:-0}"
_CURRENT_PHASE="validate"    # 当前阶段（error() 兜底上报用，随主流程推进更新）
_CURRENT_COMPONENT=""        # 进行中的组件（deploy_component 置位/清空）

_json_escape() { # $1=字符串 → stdout: JSON 字符串安全形式（引号/反斜杠转义，控制字符压成空格）
  local s="$1"
  s="${s//\\/\\\\}"; s="${s//\"/\\\"}"
  s="${s//$'\n'/ }"; s="${s//$'\r'/ }"; s="${s//$'\t'/ }"
  printf '%s' "$s"
}

progress_json() { # $1=event（phase|component|summary）；其余参数 key=value（值字符串化）
  [ "$DEPLOY_JSON_PROGRESS" = 1 ] || return 0
  local event="$1" kv k v out
  shift
  out="{\"event\":\"$event\""
  for kv in "$@"; do
    k="${kv%%=*}"; v="${kv#*=}"
    out+=",\"$k\":\"$(_json_escape "$v")\""
  done
  printf '%s\n' "@@DEPLOY_JSON@@$out}"
}

info() { echo -e "\033[36m=== $@ ===\033[0m"; }
success() { echo -e "\033[32m✅ $@\033[0m"; }
warning() { echo -e "\033[33m⚠️  $@\033[0m"; }
# error() 是主流程唯一失败出口（exit 1）：先发结构化 fail 事件（组件级优先，其次阶段级）
# 与 summary=failed 终态行，再输出人读错误。注意命令替换子 shell 内的 error() 输出会被
# 捕获吞掉，由外层调用点的 || error 兜底重发
error() {
  if [ -n "$_CURRENT_COMPONENT" ]; then
    progress_json component component="$_CURRENT_COMPONENT" state=fail message="$*"
  else
    progress_json phase phase="$_CURRENT_PHASE" state=fail message="$*"
  fi
  progress_json summary status=failed elapsed="$SECONDS"
  echo -e "\033[31m❌ $@\033[0m" >&2; exit 1
}

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