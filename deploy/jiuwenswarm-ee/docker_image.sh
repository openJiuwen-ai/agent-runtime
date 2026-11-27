#!/usr/bin/env bash
# docker_image.sh —— 试用集群命名空间内镜像查看/删除工具（语义对齐 docker images / docker rmi）
# 数据源：
#   - ImageStream：命名空间内部镜像对象，ls 展示 tag 列表，rm 可删 tag 或整个流
#   - 运行中 Pod 引用的外部镜像（如 SWR）：只读展示；集群内无可删对象，registry 侧
#     删除走对应 registry 控制台，节点缓存由 kubelet 自动 GC
# 复用同目录 kubectl.sh 函数库（curl 直连 REST API，避免拉取大体积 OpenAPI schema）

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

source "$SCRIPT_DIR/common.sh"
source "$SCRIPT_DIR/kubectl.sh"
source "$SCRIPT_DIR/global_vars.sh"
source "$SCRIPT_DIR/envfile_handler.sh"

IS_PREFIX="/apis/image.openshift.io/v1/namespaces"

usage() {
  cat <<'EOF'
用法: docker_image.sh ls|rm [目标] [-n NS]
  ls                查看镜像：ImageStream tag 列表 + Pod 引用的外部镜像
  rm <name>:<tag>   删除 ImageStream 的指定 tag
  rm <name>         删除整个 ImageStream（含全部 tag）
  -n NS             指定命名空间（默认取 .env.custom 的 NAMESPACE）
  -h/--help         本说明
示例:
  docker_image.sh ls
  docker_image.sh rm workswarm:0.25.1
  docker_image.sh rm workswarm -n other-ns
EOF
}

# 解析 name[:tag] → IMG_NAME/IMG_TAG（tag 可空）
parse_target() {
  local t="$1"
  case "$t" in
    *:*) IMG_NAME="${t%%:*}"; IMG_TAG="${t#*:}"
         [ -n "$IMG_TAG" ] || error "tag 为空: $t（删除整个 ImageStream 请省略冒号，如 rm $IMG_NAME）";;
    *)   IMG_NAME="$t"; IMG_TAG="";;
  esac
  [[ "$IMG_NAME" =~ ^[a-z0-9]([-a-z0-9]*[a-z0-9])?$ ]] \
    || error "镜像名非法（DNS label）: $IMG_NAME"
  if [ -n "$IMG_TAG" ]; then
    [[ "$IMG_TAG" =~ ^[A-Za-z0-9._-]{1,63}$ ]] || error "tag 非法: $IMG_TAG"
  fi
}

# 列出 ImageStream tag（对齐 docker images 列式输出）
cmd_ls() {
  local resp list_code pod_code
  resp="$(kctl_tmpdir)/resp"
  list_code=$(kctl_http GET "$IS_PREFIX/$KCTL_NS/imagestreams" "$resp")
  if [ "$list_code" != 200 ]; then
    if [ "$list_code" = 401 ]; then
      error "401 token 过期：控制台 → Copy login command → oc login 后重跑"
    fi
    kctl_show_err "$list_code" "$resp"
  fi

  local streams; mapfile -t streams < <(yq -r '.items[].metadata.name' "$resp")
  printf "%-45s %-16s %-20s %s\n" "REPOSITORY" "TAG" "IMAGE ID" "CREATED"
  local name tags line code
  for name in "${streams[@]}"; do
    [ -n "$name" ] || continue
    code=$(kctl_http GET "$IS_PREFIX/$KCTL_NS/imagestreams/$name" "$resp")
    if [ "$code" != 200 ]; then
      warning "ImageStream 详情获取失败(HTTP $code): $KCTL_NS/$name，跳过"
      continue
    fi
    tags=$(yq -r '.status.tags[]? | .tag + "|" + ((.items[0].image // "-") + "|" + ((.items[0].created // "-") | split("T")[0]))' "$resp")
    if [ -z "$tags" ]; then
      printf "%-45s %-16s %-20s %s\n" "$KCTL_NS/$name" "-" "-" "-"
      continue
    fi
    while IFS='|' read -r tag digest created; do
      [ -n "$tag" ] || continue
      printf "%-45s %-16s %-20s %s\n" "$KCTL_NS/$name" "$tag" "${digest:0:19}" "$created"
    done <<< "$tags"
  done

  echo
  info "Pod 引用的外部镜像（随 Pod 生命期，集群内不可单独删除）"
  pod_code=$(kctl_http GET "/api/v1/namespaces/$KCTL_NS/pods" "$resp")
  if [ "$pod_code" = 200 ]; then
    yq -r '.items[].spec.containers[].image' "$resp" | sort -u | sed 's/^/  /'
  else
    warning "Pod 列表获取失败(HTTP $pod_code)，跳过外部镜像展示"
  fi
}

# 删除 ImageStream tag / 整个 ImageStream
cmd_rm() {
  local resp code
  resp="$(kctl_tmpdir)/resp"
  parse_target "$1"

  if [ -n "$IMG_TAG" ]; then
    code=$(kctl_http GET "$IS_PREFIX/$KCTL_NS/imagestreams/$IMG_NAME" "$resp")
    if [ "$code" != 200 ]; then
      if [ "$code" = 404 ]; then
        error "ImageStream 不存在: $IMG_NAME"
      fi
      kctl_show_err "$code" "$resp"
    fi
    # strategic merge patch 按 name 合并 tags 列表，$patch: delete 移除指定 tag
    code=$(kctl_patch_strategic "$IS_PREFIX/$KCTL_NS/imagestreams/$IMG_NAME" \
      "{\"spec\":{\"tags\":[{\"name\":\"$IMG_TAG\",\"\$patch\":\"delete\"}]}}" "$resp")
    if [ "$code" != 200 ]; then
      if [ "$code" = 401 ]; then
        error "401 token 过期：控制台 → Copy login command → oc login 后重跑"
      fi
      kctl_show_err "$code" "$resp"
    fi
    success "已删除 tag: $KCTL_NS/$IMG_NAME:$IMG_TAG"
  else
    code=$(kctl_http DELETE "$IS_PREFIX/$KCTL_NS/imagestreams/$IMG_NAME" "$resp")
    case "$code" in
      200) success "已删除 ImageStream: $KCTL_NS/$IMG_NAME（含全部 tag）";;
      404) error "ImageStream 不存在: $KCTL_NS/$IMG_NAME";;
      401) error "401 token 过期：控制台 → Copy login command → oc login 后重跑";;
      *)   kctl_show_err "$code" "$resp";;
    esac
  fi
}

main() {
  local cmd="" target="" ns_arg=""
  while [ $# -gt 0 ]; do
    case "$1" in
      ls|rm)     [ -z "$cmd" ] || error "命令重复: $cmd 与 $1"; cmd="$1";;
      -n)        [ $# -ge 2 ] || error "-n 需要参数"; ns_arg="$2"; shift;;
      -h|--help) usage; exit 0;;
      *)         if [ "$cmd" = "rm" ] && [ -z "$target" ]; then
                   target="$1"
                 else
                   error "无法识别的参数: $1（用法见 -h）"
                 fi;;
    esac
    shift
  done
  [ -n "$cmd" ] || { usage; exit 1; }
  if [ "$cmd" = "rm" ] && [ -z "$target" ]; then
    error "rm 需要目标: <name>[:<tag>]（用法见 -h）"
  fi

  read_env_from_file "$CUSTOM_ENV_FILE" DEPLOY_VARS
  KCTL_NS="${ns_arg:-${DEPLOY_VARS["NAMESPACE"]:-}}"
  [[ "$KCTL_NS" =~ ^[a-z0-9]([-a-z0-9]*[a-z0-9])?$ ]] || error "NAMESPACE 非法: $KCTL_NS"

  check_dependency
  kctl_auth

  case "$cmd" in
    ls) cmd_ls;;
    rm) cmd_rm "$target";;
  esac
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  set -uo pipefail
  main "$@"
  exit $?
fi
