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
# .env.custom；up 前渲染到 conf/（渲染产物即部署清单）；down 不渲染，直接消费
# conf/ 现有清单（按部署时资源名卸载），缺失时提示先 down --render-only 补齐。
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
  progress_json phase phase=validate state=start
  read_env_from_file "$CUSTOM_ENV_FILE" DEPLOY_VARS
  parse_args "$@"
  check_vars
  progress_json phase phase=validate state=ok
  filter_targets_by_external_mode
  filter_targets_by_deploy_mode   # 三模式目标派生（up 专属；down 模式无关）
  KCTL_NS="${DEPLOY_VARS["NAMESPACE"]}"   # 全部组件的 namespace 由 .env.custom / -n 统一控制

  # --render-only：只渲染清单（纯本地 awk，不需要集群连接与 oc token）
  if [ "${DEPLOY_VARS["RENDER_ONLY"]}" = "true" ]; then
    info "--render-only：只渲染清单，不连接集群"
    _CURRENT_PHASE=render; progress_json phase phase=render state=start
    render_templates
    success "渲染完成 → $CONF_DIR"
    progress_json phase phase=render state=ok
    progress_json summary status=success elapsed="$SECONDS"
    return 0
  fi

  check_dependency
  kctl_check_vars

  if [ "$CMD" = up ]; then
    filter_targets_by_cluster_type   # k8s 形态：基础服务复用 default 共享实例，禁在本 ns up（-n default 预置放行）；仅 up 生效
    kctl_ensure_namespace "$KCTL_NS"   # 仅 up：目标 ns/Project 缺失时自动创建；无权限降级警告继续；down 不创建
    resolve_builtin_svc_locations   # 内置基础服务实例定位（本 ns → default），渲染前执行
    resolve_targets        # 可拉起依赖（redis）集群缺失时自动纳入
    info "部署目标: ${TARGETS[*]}"
    _CURRENT_PHASE=render; progress_json phase phase=render state=start
    render_templates
    progress_json phase phase=render state=ok
    _CURRENT_PHASE=preflight; progress_json phase phase=preflight state=start
    preflight
    progress_json phase phase=preflight state=ok
    ensure_manager_deploy_credentials   # 部署凭证 Secret 缺失时自动创建（双形态，manager up 前置）
    _CURRENT_PHASE=apply; progress_json phase phase=apply state=start
    run_phase apply "部署" "${TARGETS[@]}"
    progress_json phase phase=apply state=ok
    progress_json summary status=success elapsed="$SECONDS"
  else
    # 卸载不渲染，直接消费 conf/ 现有清单（上次 up 渲染的部署时形态）：
    # 资源名与在役资源一致，模板漂移也不会漏删；conf/ 产物不被 down 覆盖。
    # 非 K8s 清单（agentserver.json）卸载不消费，缺失不校验
    local missing=() cf
    for comp in "${TARGETS[@]}"; do
      for cf in $(component_files "$comp"); do
        case "$cf" in *.json) continue;; esac
        [ -f "$CONF_DIR/$cf" ] || missing+=("$cf")
      done
    done
    if [ "${#missing[@]}" -gt 0 ]; then
      error "conf/ 缺卸载所需清单: ${missing[*]}
先执行 ./deploy.sh up --render-only 补齐渲染（产物落 conf/），再执行 down"
    fi
    # kubectl delete -f 以清单内 namespace 为准（-n 不同也不改写删除目标）：
    # 校验 conf/ 内嵌 ns 与 -n 一致，防卸载错命名空间或静默删空
    local probe_ns=""
    for comp in "${TARGETS[@]}"; do
      for cf in $(component_files "$comp"); do
        case "$cf" in *.json) continue;; esac
        [ -f "$CONF_DIR/$cf" ] || continue
        probe_ns=$(yq -r '.metadata.namespace // ""' "$CONF_DIR/$cf" 2>/dev/null | head -1)
        break 2
      done
    done
    if [ -n "$probe_ns" ] && [ "$probe_ns" != "$KCTL_NS" ]; then
      error "conf/ 清单 namespace=$probe_ns 与卸载目标 -n $KCTL_NS 不一致
用 -n $probe_ns 卸载，或 ./deploy.sh up --render-only 重新渲染"
    fi
    local reversed=() i
    for ((i=${#TARGETS[@]}-1; i>=0; i--)); do reversed+=("${TARGETS[$i]}"); done
    warning "卸载将删除资源与数据（PVC 数据一并删除且不可恢复）"
    info "卸载目标（倒序）: ${reversed[*]}"
    _CURRENT_PHASE=delete; progress_json phase phase=delete state=start
    KCTL_IGNORE_NOT_FOUND=1 run_phase delete "卸载" "${reversed[@]}"
    progress_json phase phase=delete state=ok
    gc_orphan_shared_deps   # secret/redis 已无存活依赖方时自动回收
    # full 模式：runtime 创建的 AgentServer 是裸 Pod（无 owner 控制器），runtime
    # 组件卸载后无人回收——按标签兜底清理。仅 full 生效：routing/off 的
    # AgentServer Pod 由 StatefulSet 托管，按该标签清理会误删在役 Pod
    if [ "${DEPLOY_VARS["AGENT_RUNTIME_DEPLOY_MODE"]}" = full ] \
      && in_array runtime "${TARGETS[@]}"; then
      kctl_delete_pods_by_label "$KCTL_NS" "jiuwenswarm-component=agentserver"
    fi
    progress_json summary status=success elapsed="$SECONDS"
  fi
}

# 仅直接执行时跑 main；被 source 时只提供函数
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  set -uo pipefail
  main "$@"
  exit $?
fi
