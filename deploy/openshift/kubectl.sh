#!/usr/bin/env bash
[ -n "${_DEPLOY_KUBECTL_SH:-}" ] && return 0
_DEPLOY_KUBECTL_SH=1

# kubectl.sh —— 轻量 kubectl -f 等价工具（curl 直连 Kubernetes REST API）
# 动词与语义对齐原生 kubectl，仅收发小请求/小响应
#
# 命令行用法（直接执行）：
#   ./kubectl.sh -f xxx.yaml                 # 默认动词 apply；多文档一次提交；已存在自动跳过
#   ./kubectl.sh apply -f a.yaml -f b.yaml   # 不存在则建；已存在 unchanged 跳过
#                                            #   （不做增量合并，改配置走 replace 或删后建）
#   ./kubectl.sh create -f xxx.yaml          # 已存在报 AlreadyExists，退出码 1
#   ./kubectl.sh replace -f xxx.yaml         # PUT 整对象替换，404 报错退出码 1
#   ./kubectl.sh delete -f xxx.yaml          # 404 默认报错，--ignore-not-found 才忽略
#   ./kubectl.sh -n <ns> -f xxx.yaml         # yaml 未显式写 namespace 时的兜底
#   ./kubectl.sh --server URL --token TOK    # 覆盖 API server / 凭证
#
# 函数库用法（source 后调用）：
#   check_dependency                           # 校验运行依赖（按 CLUSTER_TYPE: curl/yq/oc 或 yq/kubectl）
#   kctl_check_vars                            # 校验集群配置并解析 server/NS/TOKEN → KCTL_*（幂等；k8s 免地址、置 kubeconfig 哨兵）
#   kctl_kind_path Service my-ns               # → stdout: REST 集合路径；未知 kind 返回 1
#   kctl_http GET "$path/名称" resp文件        # → stdout: http_code（网络瞬断自动重试）
#   kctl_patch "$path/名称" '{"spec":{...}}' resp文件   # merge-patch → stdout: http_code
#   kctl_patch_strategic "$path/名称" '{...}' resp文件  # strategic merge patch → stdout: http_code
#   kctl_split_resources file.yaml 目录        # 多文档拆分 → 全局数组 KCTL_RESOURCES
#   kctl_apply_resource res.yaml               # 单资源：同 kubectl apply -f
#   kctl_create_resource res.yaml              # 单资源：同 kubectl create -f
#   kctl_replace_resource res.yaml             # 单资源：同 kubectl replace -f（PUT）
#   kctl_delete_resource res.yaml              # 单资源：同 kubectl delete -f
#   kctl_run VERB file.yaml ...                # 高层入口：文件→拆分→逐资源执行
# CLUSTER_API_SERVER / NAMESPACE 覆盖顺序：--server/-n > .env.custom
# token 覆盖顺序：--token > 环境变量 TOKEN > oc 登录态
# CLUSTER_TYPE=k8s：kubectl + kubeconfig 认证，--server/--token 与 oc 逻辑不生效

# 本脚本所在目录
_KCTL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

source "$_KCTL_DIR/common.sh"
source "$_KCTL_DIR/envfile_handler.sh"
# DEPLOY_VARS 默认值唯一定义处；.env.custom 由各入口 main 读取后覆盖
source "$_KCTL_DIR/global_vars.sh"
# 集群级变量公共校验函数库（供 kctl_check_vars 复用）
source "$_KCTL_DIR/var_handler.sh"
# k8s 形态专属函数（_kctl_k8s_* 通道实现、渲染后处理）；独立执行本脚本时也需要
source "$_KCTL_DIR/k8s_handler.sh"

# 临时目录随进程退出清理（kapply-<PID> 按进程隔离）；SIGINT/SIGTERM 转发为正常退出以触发本 trap
trap 'rm -rf "$_KCTL_DIR/kapply-$$"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# ---------- 基础设施 ----------

# 校验运行依赖（按 CLUSTER_TYPE 选用：openshift 走 curl + oc 登录态，k8s 走 kubectl + kubeconfig）
check_dependency() {
  if is_k8s_cluster; then
    require_cmds "${DEPS_K8S[@]}"
  else
    require_cmds "${DEPS_OPENSHIFT[@]}"
  fi
}

# 校验集群级配置并解析 KCTL_*（幂等；调用后 kctl_http 等不再自带认证兜底）
# server/ns 优先取 KCTL_* 预设（--server/-n），否则取 DEPLOY_VARS；取不到 token 报错退出
# CLUSTER_TYPE=k8s 走 kubeconfig 认证，KCTL_TOKEN 置哨兵值供调用方判空
kctl_check_vars() {
  check_cluster_type_var
  # CLI 覆盖（--server/-n）已写入 KCTL_*：回填 DEPLOY_VARS 后走统一校验，
  # .env.custom 缺失/缺项不再阻塞 --server/-n 的独立使用
  [ -n "${KCTL_SERVER:-}" ] && DEPLOY_VARS["CLUSTER_API_SERVER"]="${KCTL_SERVER}"
  [ -n "${KCTL_NS:-}" ] && DEPLOY_VARS["NAMESPACE"]="${KCTL_NS}"
  resolve_api_server_var
  check_namespace_var

  if is_k8s_cluster; then
    : "${KCTL_TOKEN:=kubeconfig}"
    return 0
  fi
  : "${KCTL_SERVER:=${DEPLOY_VARS["CLUSTER_API_SERVER"]:-}}"
  : "${KCTL_NS:=${DEPLOY_VARS["NAMESPACE"]:-}}"
  if [ -z "${KCTL_TOKEN:-}" ]; then
    KCTL_TOKEN="${TOKEN:-$(oc whoami -t 2>/dev/null)}"
    [ -n "$KCTL_TOKEN" ] || error "取不到 token：控制台右上角 Copy login command → oc login，或 TOKEN=xxx 传入"
  fi
  return 0
}

# k8s 形态通道实现见 k8s_handler.sh；本文件保留原语入口与形态分流

# 惰性创建临时目录：kapply-<PID> 按进程隔离，命令替换内多次调用返回同一目录；
# 路径须位于 $HOME 内且非隐藏（snap 版 yq 沙箱要求），退出时由 EXIT trap 清理
kctl_tmpdir() {
  KCTL_TMP="$_KCTL_DIR/kapply-$$"
  mkdir -p "$KCTL_TMP"
  printf '%s' "$KCTL_TMP"
}

# curl 封装：stdout 输出 http_code；网络瞬断/DNS 抖动自动重试 3 次
# CLUSTER_TYPE=k8s 时改走 kubectl 通道（_kctl_k8s_http，见 k8s_handler.sh）
kctl_http() { # $1=method $2=path $3=resp文件 $4=body文件(可选)
  is_k8s_cluster && { _kctl_k8s_http "$1" "$2" "$3"; return 0; }
  local method="$1" url="$KCTL_SERVER$2" out="$3" body="${4:-}" code n
  for n in 1 2 3; do
    if [ -n "$body" ]; then
      code=$(curl -s -m 60 -H "Authorization: Bearer $KCTL_TOKEN" -H "Content-Type: application/yaml" \
             -o "$out" -w "%{http_code}" -X "$method" --data-binary @"$body" "$url")
    else
      code=$(curl -s -m 60 -H "Authorization: Bearer $KCTL_TOKEN" \
             -o "$out" -w "%{http_code}" -X "$method" "$url")
    fi
    if [ -n "$code" ] && [ "$code" != "000" ]; then printf '%s' "$code"; return 0; fi
    sleep 2
  done
  printf '%s' "000"
}

# merge-patch / strategic merge patch 通用实现；stdout 输出 http_code
# CLUSTER_TYPE=k8s 时改走 kubectl 通道（_kctl_k8s_patch）
_kctl_patch() { # $1=Content-Type $2=资源路径 $3=JSON体 $4=resp文件
  is_k8s_cluster && { _kctl_k8s_patch "$@"; return 0; }
  curl -s -m 60 -H "Authorization: Bearer $KCTL_TOKEN" -H "Content-Type: $1" \
       -o "$4" -w "%{http_code}" -X PATCH --data-binary "$3" "$KCTL_SERVER$2"
}

# merge-patch（小 JSON 体）：用于 scale 等字段级修改
kctl_patch() { # $1=资源路径 $2=JSON体 $3=resp文件
  _kctl_patch "application/merge-patch+json" "$@"
}

# strategic merge patch：内置类型按 name 合并 containers 等列表，用于镜像对齐
kctl_patch_strategic() { # $1=资源路径 $2=JSON体 $3=resp文件
  _kctl_patch "application/strategic-merge-patch+json" "$@"
}

# kind → REST 集合路径（$2 为 namespace，集群级资源忽略）；未知 kind 返回 1
kctl_kind_path() {
  case "$1" in
    Pod)                     echo "/api/v1/namespaces/$2/pods";;
    Service)                 echo "/api/v1/namespaces/$2/services";;
    ConfigMap)               echo "/api/v1/namespaces/$2/configmaps";;
    Secret)                  echo "/api/v1/namespaces/$2/secrets";;
    PersistentVolumeClaim)   echo "/api/v1/namespaces/$2/persistentvolumeclaims";;
    ServiceAccount)          echo "/api/v1/namespaces/$2/serviceaccounts";;
    Deployment)              echo "/apis/apps/v1/namespaces/$2/deployments";;
    StatefulSet)             echo "/apis/apps/v1/namespaces/$2/statefulsets";;
    DaemonSet)               echo "/apis/apps/v1/namespaces/$2/daemonsets";;
    ReplicaSet)              echo "/apis/apps/v1/namespaces/$2/replicasets";;
    Job)                     echo "/apis/batch/v1/namespaces/$2/jobs";;
    CronJob)                 echo "/apis/batch/v1/namespaces/$2/cronjobs";;
    Role)                    echo "/apis/rbac.authorization.k8s.io/v1/namespaces/$2/roles";;
    RoleBinding)             echo "/apis/rbac.authorization.k8s.io/v1/namespaces/$2/rolebindings";;
    Ingress)                 echo "/apis/networking.k8s.io/v1/namespaces/$2/ingresses";;
    NetworkPolicy)           echo "/apis/networking.k8s.io/v1/namespaces/$2/networkpolicies";;
    HorizontalPodAutoscaler) echo "/apis/autoscaling/v2/namespaces/$2/horizontalpodautoscalers";;
    PodDisruptionBudget)     echo "/apis/policy/v1/namespaces/$2/poddisruptionbudgets";;
    Route)                   echo "/apis/route.openshift.io/v1/namespaces/$2/routes";;
    ImageStream)             echo "/apis/image.openshift.io/v1/namespaces/$2/imagestreams";;
    Namespace)               echo "/api/v1/namespaces";;
    PersistentVolume)        echo "/api/v1/persistentvolumes";;
    StorageClass)            echo "/apis/storage.k8s.io/v1/storageclasses";;
    ClusterRole)             echo "/apis/rbac.authorization.k8s.io/v1/clusterroles";;
    ClusterRoleBinding)      echo "/apis/rbac.authorization.k8s.io/v1/clusterrolebindings";;
    *)                       return 1;;
  esac
}

# 多文档 yaml 拆分 → 全局数组 KCTL_RESOURCES；返回 0=拆出文档
# 拆分前先清理同目录残留资源文件，避免上一文件的资源混入本次清单
kctl_split_resources() { # $1=yaml文件 $2=输出目录
  rm -f "$2"/res-*.yaml
  awk -v d="$2" 'BEGIN{i=0} /^---$/{i++; next} NF{print > (d "/res-" i ".yaml")}' "$1"
  KCTL_RESOURCES=("$2"/res-*.yaml)
  [ -e "${KCTL_RESOURCES[0]}" ]
}

# 解析单资源 → KCTL_KIND/KCTL_NAME/KCTL_RES_NS（namespace 未写时为空串）
kctl_resource_fields() { # $1=资源文件
  local -a fields=()
  mapfile -t fields < <(yq '.kind, .metadata.name, .metadata.namespace' "$1")
  KCTL_KIND="${fields[0]:-}"; KCTL_NAME="${fields[1]:-}"; KCTL_RES_NS="${fields[2]:-}"
  [ "$KCTL_KIND" = "null" ] && KCTL_KIND=""
  [ "$KCTL_NAME" = "null" ] && KCTL_NAME=""
  [ "$KCTL_RES_NS" = "null" ] && KCTL_RES_NS=""
  return 0
}

# 解析资源并定位 REST 路径 → KCTL_PATH/KCTL_LK
kctl_resolve_resource() { # $1=资源文件
  kctl_resource_fields "$1"
  [ -n "$KCTL_KIND" ] && [ -n "$KCTL_NAME" ] || error "资源缺 kind/name: $(basename "$1")"
  local ns="$KCTL_RES_NS"
  [ -z "$ns" ] && ns="$KCTL_NS"
  KCTL_PATH=$(kctl_kind_path "$KCTL_KIND" "$ns") || error "未收录的 kind: $KCTL_KIND（在 kctl_kind_path() 补充映射）"
  [[ "$KCTL_PATH" != *"/namespaces//"* ]] || error "$KCTL_KIND/$KCTL_NAME 缺 namespace（yaml 里显式写，或 -n / KCTL_NS 指定）"
  KCTL_LK="${KCTL_KIND,,}"
}

# 非 2xx 响应统一报错退出；401 按形态给出续期指引
kctl_show_err() { # $1=code $2=resp文件
  if [ "$1" = "401" ]; then
    if is_k8s_cluster; then
      error "401 认证失败：检查 kubeconfig 凭证有效性（kubectl auth whoami）"
    else
      error "401 token 过期：控制台 → Copy login command → oc login 后重跑"
    fi
  fi
  error "HTTP $1: $(head -c 300 "$2" 2>/dev/null)"
}

# ---------- 单资源操作（返回 0=成功/跳过，失败走 error 退出） ----------

# POST 创建（apply/create 共用）：?dryRun=All 预检通过后真建；调用方须先 GET 确认资源不存在
# $3=撞 409 时的动作：skip（apply 语义）| fail（create 语义）
_kctl_post_resource() { # $1=资源文件 $2=resp文件 $3=skip|fail
  local res="$1" resp="$2" code
  code=$(kctl_http POST "$KCTL_PATH?dryRun=All" "$resp" "$res")
  [ "$code" = 201 ] || kctl_show_err "$code" "$resp"
  code=$(kctl_http POST "$KCTL_PATH" "$resp" "$res")
  case "$code" in
    201) success "$KCTL_LK/$KCTL_NAME created";;
    409) if [ "$3" = "skip" ]; then
           info "$KCTL_LK/$KCTL_NAME unchanged（已存在）"
         else
           error "Error from server (AlreadyExists): $KCTL_LK/$KCTL_NAME"
         fi;;
    *)   kctl_show_err "$code" "$resp";;
  esac
}

# 同 kubectl apply -f：openshift 形态不存在则建、已存在 unchanged 跳过（改配置用 replace）；
# k8s 形态走 kubectl apply（服务端增量合并）
kctl_apply_resource() { # $1=资源文件
  kctl_resolve_resource "$1"
  is_k8s_cluster && { _kctl_k8s_apply_resource "$1"; return 0; }
  local resp; resp="$(kctl_tmpdir)/resp"
  local code; code=$(kctl_http GET "$KCTL_PATH/$KCTL_NAME" "$resp")
  case "$code" in
    200) info "$KCTL_LK/$KCTL_NAME unchanged（已存在，更新用 replace）"; return 0;;
    404) ;;
    *)   kctl_show_err "$code" "$resp";;
  esac
  _kctl_post_resource "$1" "$resp" skip
}

# 同 kubectl create -f：已存在报 AlreadyExists（与 kubectl 一致，退出码 1）
kctl_create_resource() { # $1=资源文件
  kctl_resolve_resource "$1"
  is_k8s_cluster && { _kctl_k8s_create_resource "$1"; return 0; }
  local resp; resp="$(kctl_tmpdir)/resp"
  local code; code=$(kctl_http GET "$KCTL_PATH/$KCTL_NAME" "$resp")
  case "$code" in
    200) error "Error from server (AlreadyExists): $KCTL_LK/$KCTL_NAME";;
    404) ;;
    *)   kctl_show_err "$code" "$resp";;
  esac
  _kctl_post_resource "$1" "$resp" fail
}

# 整对象替换（PUT）；不做创建（404 报错，与 kubectl replace 一致）
kctl_replace_resource() { # $1=资源文件
  kctl_resolve_resource "$1"
  is_k8s_cluster && { _kctl_k8s_replace_resource "$1"; return 0; }
  local resp; resp="$(kctl_tmpdir)/resp"
  local code; code=$(kctl_http PUT "$KCTL_PATH/$KCTL_NAME" "$resp" "$1")
  case "$code" in
    200) success "$KCTL_LK/$KCTL_NAME replaced";;
    404) error "$KCTL_LK/$KCTL_NAME 不存在（replace 不做创建，请先 apply）";;
    *)   kctl_show_err "$code" "$resp";;
  esac
}

# 同 kubectl delete -f：404 默认报 NotFound（与 kubectl 一致）；KCTL_IGNORE_NOT_FOUND=1 时忽略
kctl_delete_resource() { # $1=资源文件
  kctl_resolve_resource "$1"
  is_k8s_cluster && { _kctl_k8s_delete_resource "$1"; return 0; }
  local resp; resp="$(kctl_tmpdir)/resp"
  local code; code=$(kctl_http DELETE "$KCTL_PATH/$KCTL_NAME" "$resp")
  case "$code" in
    200) success "$KCTL_LK/$KCTL_NAME deleted";;
    404) if [ "${KCTL_IGNORE_NOT_FOUND:-0}" = 1 ]; then
           info "$KCTL_LK/$KCTL_NAME not found（已忽略）"
         else
           error "Error from server (NotFound): $KCTL_LK/$KCTL_NAME"
         fi;;
    *)   kctl_show_err "$code" "$resp";;
  esac
}

# ---------- 高层入口 ----------

# 按 label 批删 Pod（down 兜底：runtime 创建的裸 AgentServer Pod 无 owner 控制器，
# 控制器组件卸载后需显式回收）。openshift 走 curl 通道 list→逐个 DELETE；
# k8s 走 kubectl delete -l
kctl_delete_pods_by_label() { # $1=namespace $2=labelSelector
  local ns="$1" sel="$2" resp names name code
  if is_k8s_cluster; then
    kubectl delete pods -n "$ns" -l "$sel" --ignore-not-found >/dev/null 2>&1
    return 0
  fi
  resp="$(kctl_tmpdir)/pods-list"
  kctl_http GET "/api/v1/namespaces/$ns/pods?labelSelector=${sel//:/\\%3A}" "$resp" >/dev/null || return 0
  names=$(yq -r '.items[].metadata.name' "$resp" 2>/dev/null) || return 0
  for name in $names; do
    code=$(kctl_http DELETE "/api/v1/namespaces/$ns/pods/$name" "$resp")
    case "$code" in
      200) info "pod 已删除: $name";;
      404) ;;
      *)   warning "pod 删除失败($code): $name";;
    esac
  done
}

# kctl_run VERB FILE...（VERB=apply|create|replace|delete）
kctl_run() {
  [ $# -ge 1 ] || error "用法: kctl_run VERB FILE..."
  local verb="$1"; shift
  case "$verb" in
    apply|create|replace|delete) ;;
    *) error "未知动词: $verb（apply/create/replace/delete）";;
  esac
  local file res
  kctl_tmpdir >/dev/null
  for file in "$@"; do
    info "═══ $file ═══"
    if ! kctl_split_resources "$file" "$KCTL_TMP"; then
      warning "未解析出任何资源，跳过: $file"
      continue
    fi
    info "共 ${#KCTL_RESOURCES[@]} 个资源"
    for res in "${KCTL_RESOURCES[@]}"; do
      case "$verb" in
        apply)   kctl_apply_resource "$res";;
        create)  kctl_create_resource "$res";;
        replace) kctl_replace_resource "$res";;
        delete)  kctl_delete_resource "$res";;
      esac
    done
  done
}

# ---------- 命令行入口 ----------

kctl_usage() {
  cat <<'EOF'
用法: kubectl.sh [apply|create|replace|delete] -f <file.yaml> [-f ...] [选项]
  动词省略默认 apply：不存在建，已存在 unchanged 跳过（不做增量合并，改配置用 replace）
  -f/--filename FILE    可重复；也接受 shell 通配符展开出的裸路径（含未展开的 '**.yaml'）
  -n/--namespace NS     yaml 未显式写 namespace 时的兜底
  --ignore-not-found    delete 404 时不报错（同 kubectl）
  --server URL / --token TOK    覆盖 API server / Bearer token（默认取 oc whoami -t）
集群配置: 默认取同目录 .env.custom 的 CLUSTER_API_SERVER / NAMESPACE，可用 --server / -n 覆盖
环境变量: TOKEN 覆盖默认 token（默认取 oc whoami -t）
函数库:   source kubectl.sh 后可调用 check_dependency / kctl_check_vars / kctl_http / kctl_kind_path / kctl_run / ...
EOF
}

kctl_main() {
  local verb="" ns_arg=""
  local files=()
  while [ $# -gt 0 ]; do
    case "$1" in
      apply|create|replace|delete) verb="$1"; shift;;
      -f|--filename)  [ $# -ge 2 ] || error "$1 需要文件参数"
                      files+=("$2"); shift 2;;
      -n|--namespace) [ $# -ge 2 ] || error "$1 需要参数"
                      ns_arg="$2"; shift 2;;
      --ignore-not-found) KCTL_IGNORE_NOT_FOUND=1; shift;;
      --server)  [ $# -ge 2 ] || error "$1 需要参数"
                 KCTL_SERVER="$2"; shift 2;;
      --token)   [ $# -ge 2 ] || error "$1 需要参数"
                 KCTL_TOKEN="$2"; shift 2;;
      -h|--help) kctl_usage; exit 0;;
      # shell 通配符展开出的裸路径（含未被 shell 展开的 '**.yaml'）也收为文件
      *)
        if [ -f "$1" ] || [[ "$1" == *[*]* ]]; then
          files+=("$1"); shift
        else
          error "无法识别的参数: $1"
        fi;;
    esac
  done
  [ -z "$verb" ] && verb=apply
  [ ${#files[@]} -gt 0 ] || error "缺少 -f <file.yaml>（用法见 -h）"

  [ -n "$ns_arg" ] && KCTL_NS="$ns_arg"

  # .env.custom 可选（独立使用时 --server/-n/--token 已足够）；deploy.sh 路径由其 main 读取
  [ -f "$CUSTOM_ENV_FILE" ] && read_env_from_file "$CUSTOM_ENV_FILE" DEPLOY_VARS
  check_dependency
  kctl_check_vars

  # 展开通配符（含 bash 默认不展开的 **.yaml 字面量）
  local all=() f
  for f in "${files[@]}"; do
    if [ -f "$f" ]; then
      all+=("$f")
    elif [[ "$f" == *[*]* ]]; then
      local g=()
      mapfile -t g < <(compgen -G "$f")
      [ ${#g[@]} -gt 0 ] || error "通配符无匹配: $f"
      all+=("${g[@]}")
    else
      error "找不到文件: $f"
    fi
  done

  kctl_run "$verb" "${all[@]}"
}

# 仅直接执行时进 main；被 source 时只注入 kctl_* 函数库
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  set -uo pipefail
  kctl_main "$@"
  exit $?
fi
