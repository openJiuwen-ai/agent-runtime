#!/usr/bin/env bash
# kubectl.sh —— 试用集群 kubectl -f 等价工具（curl 直连 Kubernetes REST API）
# 适用场景：kubectl/oc apply 提交资源前需拉取数 MB 的 OpenAPI schema，跨境链路易传输失败；
# 本脚本仅收发小请求/小响应，动词与语义对齐原生 kubectl（apply/create/replace/delete）。
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
#   check_dependency                           # 校验 curl/yq/oc（入口脚本各调用一次）
#   kctl_auth                                  # 解析 SERVER/NS/TOKEN → KCTL_*（幂等）
#   kctl_kind_path Service rapheale-dev        # → stdout: REST 集合路径；未知 kind 返回 1
#   kctl_http GET "$path/名称" resp文件        # → stdout: http_code（网络瞬断自动重试）
#   kctl_patch "$path/名称" '{"spec":{...}}' resp文件   # merge-patch → stdout: http_code
#   kctl_patch_strategic "$path/名称" '{...}' resp文件  # strategic merge patch → stdout: http_code
#   kctl_split_resources file.yaml 目录        # 多文档拆分 → 全局数组 KCTL_RESOURCES
#   kctl_apply_resource res.yaml               # 单资源：同 kubectl apply -f
#   kctl_create_resource res.yaml              # 单资源：同 kubectl create -f
#   kctl_replace_resource res.yaml             # 单资源：同 kubectl replace -f（PUT）
#   kctl_delete_resource res.yaml              # 单资源：同 kubectl delete -f
#   kctl_run VERB file.yaml ...                # 高层入口：文件→拆分→逐资源执行
# 可预设配置（source 前设置，未设时回落到环境变量 SERVER/TOKEN/NS 再到内置默认）：
#   KCTL_SERVER / KCTL_TOKEN / KCTL_NS
# 运行期输出变量：
#   KCTL_KIND KCTL_NAME KCTL_RES_NS KCTL_PATH KCTL_LK（单资源解析结果）
#   KCTL_RESOURCES（拆分结果） KCTL_TMP（临时目录 <脚本目录>/kapply-<PID>，进程退出时由 trap 统一清理）
# 依赖：curl yq oc；临时目录在脚本所在目录下 kapply-<PID>（须保持非隐藏且位于 $HOME 内：snap 版 yq 沙箱限制）

# 本脚本所在目录
_KCTL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

source "$_KCTL_DIR/common.sh"

# ---------- 基础设施 ----------

# 校验运行依赖（入口脚本各调用一次；kctl_* 函数运行期不再重复检查）
check_dependency() {
  local cmd
  for cmd in curl yq oc; do
    command -v "$cmd" >/dev/null 2>&1 || error "缺少依赖: $cmd"
  done
}

# 解析 API server / namespace / token 配置（幂等，可重复调用；取不到 token 报错退出）
kctl_auth() {
  : "${KCTL_SERVER:=${SERVER:-https://api.rm1.0a51.p1.openshiftapps.com:6443}}"
  : "${KCTL_NS:=${NS:-rapheale-dev}}"
  if [ -z "${KCTL_TOKEN:-}" ]; then
    KCTL_TOKEN="${TOKEN:-$(oc whoami -t 2>/dev/null)}"
    [ -n "$KCTL_TOKEN" ] || error "取不到 token：控制台右上角 Copy login command → oc login，或 TOKEN=xxx 传入"
  fi
  return 0
}

# 惰性创建临时目录（拆分的资源文件/响应文件落这里）。
# 固定为 <脚本目录>/kapply-<PID>：$$ 在子 shell 中等于父进程 PID，命令替换内多次调用
# 返回同一目录，不会因子 shell 变量不可见而重复建目录；按进程隔离避免并发冲突。
# 位置约束：须保持非隐藏且位于 $HOME 内（snap 版 yq 沙箱限制，隐藏目录与 /tmp 读不了）。
# 进程退出时由入口脚本的 EXIT trap 清理
kctl_tmpdir() {
  KCTL_TMP="$_KCTL_DIR/kapply-$$"
  mkdir -p "$KCTL_TMP"
  printf '%s' "$KCTL_TMP"
}

# curl 封装：stdout 输出 http_code；网络瞬断/DNS 抖动自动重试 3 次
kctl_http() { # $1=method $2=path $3=resp文件 $4=body文件(可选)
  [ -n "${KCTL_TOKEN:-}" ] || kctl_auth
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
_kctl_patch() { # $1=Content-Type $2=资源路径 $3=JSON体 $4=resp文件
  [ -n "${KCTL_TOKEN:-}" ] || kctl_auth
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
  KCTL_KIND=$(yq '.kind' "$1")
  KCTL_NAME=$(yq '.metadata.name' "$1")
  KCTL_RES_NS=$(yq '.metadata.namespace' "$1")
  [ "$KCTL_KIND" = "null" ] && KCTL_KIND=""
  [ "$KCTL_NAME" = "null" ] && KCTL_NAME=""
  [ "$KCTL_RES_NS" = "null" ] && KCTL_RES_NS=""
  return 0
}

# 解析资源并定位 REST 路径 → KCTL_PATH/KCTL_LK
kctl_resolve_resource() { # $1=资源文件
  [ -n "${KCTL_TOKEN:-}" ] || kctl_auth
  kctl_resource_fields "$1"
  [ -n "$KCTL_KIND" ] && [ -n "$KCTL_NAME" ] || error "资源缺 kind/name: $(basename "$1")"
  local ns="$KCTL_RES_NS"
  [ -z "$ns" ] && ns="$KCTL_NS"
  KCTL_PATH=$(kctl_kind_path "$KCTL_KIND" "$ns") || error "未收录的 kind: $KCTL_KIND（在 kctl_kind_path() 补充映射）"
  [[ "$KCTL_PATH" != *"/namespaces//"* ]] || error "$KCTL_KIND/$KCTL_NAME 缺 namespace（yaml 里显式写，或 -n / KCTL_NS 指定）"
  KCTL_LK="${KCTL_KIND,,}"
}

# 非 2xx 响应统一报错退出；401 给出 token 续期指引
kctl_show_err() { # $1=code $2=resp文件
  if [ "$1" = "401" ]; then
    error "401 token 过期：控制台 → Copy login command → oc login 后重跑"
  fi
  error "HTTP $1: $(head -c 300 "$2" 2>/dev/null)"
}

# ---------- 单资源操作（返回 0=成功/跳过，失败走 error 退出） ----------

# POST 创建（apply/create 共用）：先 ?dryRun=All 准入预检，通过后真建。
# 前置条件：调用方已 GET 探测确认资源不存在——已存在的 NodePort Service 直接 dryRun POST
# 会先撞端口占用 422（nodePort already allocated）而非 409，造成误报。
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

# 同 kubectl apply -f：不存在则建，已存在 unchanged 跳过（不做增量合并，改配置用 replace）
kctl_apply_resource() { # $1=资源文件
  kctl_resolve_resource "$1"
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

# kctl_run VERB FILE...（VERB=apply|create|replace|delete）
kctl_run() {
  [ $# -ge 1 ] || error "用法: kctl_run VERB FILE..."
  local verb="$1"; shift
  case "$verb" in
    apply|create|replace|delete) ;;
    *) error "未知动词: $verb（apply/create/replace/delete）";;
  esac
  [ -n "${KCTL_TOKEN:-}" ] || kctl_auth
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

usage() {
  cat <<'EOF'
用法: kubectl.sh [apply|create|replace|delete] -f <file.yaml> [-f ...] [选项]
  动词省略默认 apply：不存在建，已存在 unchanged 跳过（不做增量合并，改配置用 replace）
  -f/--filename FILE    可重复；也接受 shell 通配符展开出的裸路径（含未展开的 '**.yaml'）
  -n/--namespace NS     yaml 未显式写 namespace 时的兜底
  --ignore-not-found    delete 404 时不报错（同 kubectl）
  --server URL / --token TOK    覆盖 API server / Bearer token（默认取 oc whoami -t）
环境变量: SERVER / TOKEN / NS 同样可覆盖默认（优先级：flag > 环境变量 > 内置默认）
函数库:   source kubectl.sh 后可调用 check_dependency / kctl_auth / kctl_http / kctl_kind_path / kctl_run / ...
EOF
}

main() {
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
      -h|--help) usage; exit 0;;
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

  check_dependency
  kctl_auth

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

# 仅直接执行时进 main；被 source 时只注入 kctl_* 函数库（不改动 caller 的 shell 选项）
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  set -uo pipefail
  trap 'rm -rf "$_KCTL_DIR/kapply-$$"' EXIT   # 临时目录兜底清理（正常退出/error 退出均生效）
  trap 'exit 130' INT; trap 'exit 143' TERM   # 转发为正常退出，确保 EXIT trap 执行（SIGKILL 除外）
  main "$@"
  exit $?
fi
