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

  # down 直接消费 conf/ 现有清单（按部署时资源名卸载），不支持重渲染：
  # 清单缺失时按 main 里的提示先 up --render-only 补齐
  [ "${DEPLOY_VARS["RENDER_ONLY"]}" = "true" ] && [ "$CMD" = down ] \
    && error "down 不支持 --render-only（卸载直接消费 conf/ 现有清单；缺失时先 up --render-only 补齐）"

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
用法: deploy.sh [manager] [组件...] <up|down> [-n NAMESPACE] [--render-only]
  up                部署缺省目标（产品组件，按 AGENT_RUNTIME_DEPLOY_MODE 派生，见 .env.example）:
                    off:     secret → redis → agentserver → gateway → web
                    routing: secret → redis → runtime → agentserver → gateway → web
                    full:    secret → redis → runtime → gateway → web（AgentServer 由 runtime 按需创建）
  manager up        部署管理套件: identity → manager-server → manager-web
  up <组件>...      部署指定组件（可多选，自动按依赖序执行）:
                    基础服务: mariadb / mysql / postgresql / minio（数据库三选一）、log（日志采集，一次部署长期运行）
                    共享依赖: secret / redis（目标组件缺失依赖时自动补齐）
  down [组件...]    卸载指定组件（按依赖逆序；缺省目标与 up 对称）。
                    secret / redis 仍被在役组件依赖时自动保留；
                    模式切换（routing→off）后遗留的 runtime 需显式 down runtime 清理
  -n NAMESPACE      指定 namespace（缺省取 .env.custom 的 NAMESPACE）
  --render-only     仅渲染目标组件清单到 conf/，不连接集群（仅 up）
前置条件: 产品组件依赖数据库与对象存储: ./deploy.sh up mariadb minio（数据库三选一，详见 README）
镜像版本: 组件镜像由 .env.custom 的 *_IMAGE 变量管理，up 时自动滚动更新
示例: ./deploy.sh up mariadb minio && ./deploy.sh up && ./deploy.sh manager up
      ./deploy.sh up log                    # 部署日志采集（基础服务，部署一次长期运行）
      ./deploy.sh up mariadb --render-only  # 仅渲染 mariadb 清单
      ./deploy.sh -n other-ns up --render-only
EOF
}
