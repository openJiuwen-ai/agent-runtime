#!/usr/bin/env bash
[ -n "${_DEPLOY_ARGS_HANDLER_SH:-}" ] && return 0
_DEPLOY_ARGS_HANDLER_SH=1

# args_handler.sh —— 命令行解析
# 解析结果写入全局：CMD(up|down)、SCOPE(""|manager)、TARGETS

CMD=""
SCOPE=""
TARGETS=()
TARGETS_EXPLICIT=0   # 1=命令行显式指定组件（默认目标按模式派生，显式目标按模式校验矛盾）

parse_args() {
  while [ $# -gt 0 ]; do
    case "$1" in
      up|down)   [ -z "$CMD" ] || error "命令重复: $CMD 与 $1"
                 CMD="$1";;
      manager)   [ -z "$SCOPE" ] || error "组重复: manager"
                 SCOPE=manager;;
      -n)        [ $# -ge 2 ] || error "-n 需要参数"
                 DEPLOY_VARS["NAMESPACE"]="$2"; shift;;
      --render-only) DEPLOY_VARS["RENDER_ONLY"]="true";;
      -y|--yes)  ;;   # 兼容保留参数，无实际作用（卸载无需确认）
      -h|--help) usage; exit 0;;
      *)         component_managed "$1" || error "不支持的参数: $1（可用：[manager] up|down，组件名，-n，--render-only，-y，-h）"
                 in_array "$1" "${TARGETS[@]}" || TARGETS+=("$1");;   # 显式指定组件
    esac
    shift
  done

  [ -n "$CMD" ] || error "缺少命令 up|down（用法见 -h）"

  # 目标组件：显式指定优先（按规范依赖序排列）；未指定则取组默认
  if [ ${#TARGETS[@]} -gt 0 ]; then
    TARGETS_EXPLICIT=1
    sort_targets
  elif [ "$SCOPE" = manager ]; then
    TARGETS=("${MANAGER_TARGETS[@]}")
  else
    TARGETS=("${UP_TARGETS[@]}")
  fi
}

usage() {
  cat <<'EOF'
用法: deploy.sh [manager] [组件...] <up|down> [-n NAMESPACE] [--render-only] [-y]
  up                拉起产品组件（按 AGENT_RUNTIME_DEPLOY_MODE 派生，见 .env.example）:
                    off:     secret → redis → agentserver → gateway → web
                    routing: 同上 + runtime（会话粘性路由，零 K8s 权限）
                    full:    暂未开放（配置即报错，待开放）
  manager up        拉起管理套件: identity → manager-server → manager-web
                    （依赖的 secret / redis 集群缺失时自动拉起，已有则复用）
  up mariadb        只部署/渲染指定组件；可多选，如: up mariadb minio
                    基础服务四件套: mariadb / mysql / postgresql / minio（数据库三选一）
  down [组件...]    卸载目标（倒序删除；共享依赖 secret/redis 仍被存活组件依赖时保留，
                    卸载后依赖方清零则自动回收）。默认 down 与 up 目标对称；
                    模式切换（routing→off）后遗留的 runtime 用 `down runtime` 清理
  -n NAMESPACE      指定 namespace（默认取 .env.custom 的 NAMESPACE）
  --render-only     只渲染本次目标组件的清单到 conf/，不连接集群（核对/交付 yaml 用）
  -y                兼容保留参数，无实际作用（卸载不再需要确认）
前置条件: 基础服务已拉起（./deploy.sh up mariadb minio，见 README）
版本管理: 组件镜像名（含 amd64/arm64 架构段）统一维护在 .env.custom
示例: ./deploy.sh up && ./deploy.sh manager up
      ./deploy.sh up mariadb --render-only   # 只渲染 mariadb 清单
      ./deploy.sh -n other-ns up --render-only
EOF
}
