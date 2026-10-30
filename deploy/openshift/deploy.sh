#!/usr/bin/env bash
# deploy.sh —— 组件部署/卸载入口（复用同目录 kubectl.sh 函数库；
# openshift 形态经 curl 直连 Kubernetes REST API，k8s 形态走 kubectl CLI）
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

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

source "$SCRIPT_DIR/common.sh"
source "$SCRIPT_DIR/kubectl.sh"
source "$SCRIPT_DIR/global_vars.sh"
source "$SCRIPT_DIR/var_handler.sh"
source "$SCRIPT_DIR/envfile_handler.sh"
source "$SCRIPT_DIR/args_handler.sh"
source "$SCRIPT_DIR/component_handler.sh"
source "$SCRIPT_DIR/template_handler.sh"
source "$SCRIPT_DIR/k8s_handler.sh"
source "$SCRIPT_DIR/deploy_handler.sh"

# ---------- Main ----------

main() {
  read_env_from_file "$CUSTOM_ENV_FILE" DEPLOY_VARS
  parse_args "$@"
  check_vars
  filter_targets_by_external_mode
  filter_targets_by_deploy_mode   # 三模式目标派生（up 专属；down 模式无关）
  KCTL_NS="${DEPLOY_VARS["NAMESPACE"]}"   # 全部组件的 namespace 由 .env.custom / -n 统一控制

  # --render-only：只渲染清单（纯本地 awk，不需要集群连接与 oc token）
  if [ "${DEPLOY_VARS["RENDER_ONLY"]}" = "true" ]; then
    info "--render-only：只渲染清单，不连接集群"
    render_templates
    success "渲染完成 → $CONF_DIR"
    return 0
  fi

  check_dependency
  kctl_check_vars

  if [ "$CMD" = up ]; then
    filter_targets_by_cluster_type   # k8s 形态：基础服务复用 default 共享实例，禁在本 ns up（-n default 预置放行）；仅 up 生效
    kctl_ensure_namespace "$KCTL_NS"   # 仅 up：目标 ns/Project 缺失时自动创建；down 不创建
    resolve_builtin_svc_locations   # 内置基础服务实例定位（本 ns → default），渲染前执行
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
    gc_orphan_shared_deps   # secret/redis 已无存活依赖方时自动回收
    # full 模式：runtime 创建的 AgentServer 是裸 Pod（无 owner 控制器），runtime
    # 组件卸载后无人回收——按标签兜底清理。仅 full 生效：routing/off 的
    # AgentServer Pod 由 StatefulSet 托管，按该标签清理会误删在役 Pod
    if [ "${DEPLOY_VARS["AGENT_RUNTIME_DEPLOY_MODE"]}" = full ] \
      && in_array runtime "${TARGETS[@]}"; then
      kctl_delete_pods_by_label "$KCTL_NS" "jiuwenswarm-component=agentserver"
    fi
  fi
}

# 仅直接执行时跑 main；被 source 时只提供函数
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  set -uo pipefail
  main "$@"
  exit $?
fi
