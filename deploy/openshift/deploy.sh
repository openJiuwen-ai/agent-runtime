#!/usr/bin/env bash
# deploy.sh —— OpenShift 组件部署/卸载入口（经 curl 直连 Kubernetes REST API 提交，
# 复用同目录 kubectl.sh 函数库）
#
# 用法（详见 usage 与 README）：
#   ./deploy.sh up                        # 拉起产品组件: secret → redis → agentserver → gateway → web
#   ./deploy.sh manager up                # 拉起管理套件: identity → manager-server → manager-web
#   ./deploy.sh up mariadb [minio ...]    # 按组件部署（基础服务/共享依赖/产品/管理组件均可）
#   ./deploy.sh down [组件...]            # 卸载（倒序删除；共享依赖有人用则保留；等 Pod 删净）
#   ./deploy.sh -n other-ns up            # 指定 namespace（默认取 .env.custom 的 NAMESPACE）
#   ./deploy.sh up mariadb --render-only  # 只渲染本次目标清单到 conf/，不连接集群
#
# 配置与渲染：templates/ 为清单模板（<<VAR>> 占位符），镜像名与 namespace 统一维护在
# .env.custom；up/down 前渲染到 conf/（渲染产物即部署清单）。
#
# 约束：agentserver 先于 gateway 部署（gateway 启动依赖 agentserver），脚本已保证；
# 命令幂等，可重复执行，已存在资源跳过；副本/镜像与配置不一致时自动 PATCH 对齐。
# 环境变量：WAIT_TIMEOUT_SECS —— Pod 就绪等待预算，默认 300s（首次拉取镜像建议调大）
#
# 文件拆分：
#   common.sh             日志函数（error 即退出）
#   global_vars.sh        全局变量、默认配置与变量校验
#   envfile_handler.sh    .env.custom 读取
#   args_handler.sh       命令行解析
#   component_handler.sh  组件映射表（清单/标签/依赖/探测/自动补齐范围）
#   template_handler.sh   清单渲染（<<VAR>> 占位符替换；templates/ 为唯一清单源）
#   deploy_handler.sh     目标解析/预检/副本与镜像对齐/部署与卸载（含就绪与删除等待）

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

source "$SCRIPT_DIR/common.sh"
source "$SCRIPT_DIR/kubectl.sh"          # 注入 kctl_* 函数库（source 模式不会触发其 main）
source "$SCRIPT_DIR/global_vars.sh"
source "$SCRIPT_DIR/envfile_handler.sh"
source "$SCRIPT_DIR/args_handler.sh"
source "$SCRIPT_DIR/component_handler.sh"
source "$SCRIPT_DIR/template_handler.sh"
source "$SCRIPT_DIR/deploy_handler.sh"

# ---------- Main ----------

main() {
  read_env_from_file "$CUSTOM_ENV_FILE" DEPLOY_VARS
  parse_args "$@"
  check_vars
  KCTL_NS="${DEPLOY_VARS["NAMESPACE"]}"   # 全部组件的 namespace 由 .env.custom / -n 统一控制

  # --render-only：只渲染清单（纯本地 awk，不需要集群连接与 oc token）
  if [ "${DEPLOY_VARS["RENDER_ONLY"]}" = "true" ]; then
    info "--render-only：只渲染清单，不连接集群"
    render_templates
    success "渲染完成 → $CONF_DIR"
    return 0
  fi

  check_dependency   # curl/yq/oc 各检查一次（render-only 分支不需要，故置于其后）
  kctl_auth

  if [ "$CMD" = up ]; then
    resolve_targets        # 可拉起依赖（redis）集群缺失时自动纳入
    info "部署目标: ${TARGETS[*]}"
    render_templates
    preflight
    run_phase apply "部署" "${TARGETS[@]}"
  else
    render_templates
    local reversed=() i
    for ((i=${#TARGETS[@]}-1; i>=0; i--)); do reversed+=("${TARGETS[$i]}"); done
    warning "卸载将删除资源与数据（PVC 数据一并删除且不可恢复）"
    info "卸载目标（倒序）: ${reversed[*]}"
    KCTL_IGNORE_NOT_FOUND=1 run_phase delete "卸载" "${reversed[@]}"
  fi
}

# 仅直接执行时跑 main；被 source 时只提供 deploy_* / component_* 等函数（复用 kubectl.sh 函数库，
# kapply-* 临时目录的清理 trap 由 kubectl.sh 注册）
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  set -uo pipefail
  main "$@"
  exit $?
fi
