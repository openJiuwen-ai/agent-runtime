#!/usr/bin/env bash
[ -n "${_DEPLOY_HANDLER_SH:-}" ] && return 0
_DEPLOY_HANDLER_SH=1

# deploy_handler.sh —— 目标解析 / 依赖预检 / 副本与镜像对齐 / 就绪与删除等待 / 部署 / 卸载

# up 目标按 ALL_COMPONENTS 规范依赖序重排（保证依赖方后于被依赖方部署）
sort_targets() {
  local -a sorted=()
  local m
  for m in "${ALL_COMPONENTS[@]}"; do
    in_array "$m" "${TARGETS[@]}" && sorted+=("$m")
  done
  TARGETS=("${sorted[@]}")
}

# 组件存在性探测：stdout 输出 http_code（200=已在集群；无探测映射或路径解析失败输出 000）。
# 探测命名空间经 component_ns：基础服务（k8s 形态）指向 default 共享实例，其余在本 ns
component_probe_code() { # $1=组件
  local probe p
  probe=$(component_probe "$1") || { echo "000"; return 0; }
  p=$(kctl_kind_path "${probe%%/*}" "$(component_ns "$1")") || { echo "000"; return 0; }
  kctl_http GET "$p/${probe#*/}" /dev/null
}

# up 依赖补全：目标组件的可自动补齐依赖（secret/redis）在集群中缺失时纳入本次部署目标；
# 集群已有则复用、不重复部署。基础服务不可自动补齐，缺失交给 preflight 报错
# 外挂 Redis（REDIS_HOST 已配置）时从部署目标剔除内置 redis：
# 默认目标 UP_TARGETS 写死了 redis，若不剔除，up 会无条件重新拉起内置实例。
# 放在 check_vars 之后调用，渲染与部署两条路径（--render-only / 完整 up）行为一致。
filter_targets_by_external_mode() {
  [ "${DEPLOY_VARS["ENABLE_EXTERNAL_REDIS"]}" = "true" ] || return 0
  local comp
  local -a no_redis=()
  for comp in "${TARGETS[@]}"; do
    [ "$comp" = "redis" ] || no_redis+=("$comp")
  done
  TARGETS=("${no_redis[@]}")
}

resolve_targets() {
  local comp dep
  local -a extra=()
  for comp in "${TARGETS[@]}"; do
    for dep in $(component_deps "$comp"); do
      in_array "$dep" "${TARGETS[@]}" && continue          # 本次一起部署
      component_autoprovision "$dep" || continue           # 不可自动补齐的依赖走 preflight
      component_probe "$dep" >/dev/null || continue        # 无探测方式的依赖跳过
      [ "$(component_probe_code "$dep")" = 200 ] && continue          # 已存在，复用
      in_array "$dep" "${extra[@]}" || extra+=("$dep")
    done
  done
  [ ${#extra[@]} -eq 0 ] && return 0
  info "集群缺失依赖，纳入部署: ${extra[*]}"
  TARGETS=("${extra[@]}" "${TARGETS[@]}")
  sort_targets
}

# 目标按 AGENT_RUNTIME_DEPLOY_MODE 派生（管理套件除外）：
#   默认目标：up/down 对称——非 off 时均纳入 runtime，确保完整部署与完整拆除；
#            off 不含 runtime（模式切换遗留的 runtime 用显式 `down runtime` 清理）
#   显式目标：不增删；仅 up 拦截与模式矛盾的组合（down 允许显式卸载任意组件）
# 前置条件：parse_args 与 check_vars 已完成
filter_targets_by_deploy_mode() {
  local mode="${DEPLOY_VARS["AGENT_RUNTIME_DEPLOY_MODE"]}" comp
  [ "${SCOPE:-}" = manager ] && return 0
  if [ "${TARGETS_EXPLICIT}" = 0 ]; then
    if [ "$CMD" = up ]; then
      local -a adjusted=()
      for comp in "${TARGETS[@]}"; do
        case "$mode:$comp" in
          full:agentserver) continue;;   # full：AgentServer 由 runtime 按需创建，不预启动
          off:runtime)      continue;;   # off：不部署 agent-runtime
        esac
        adjusted+=("$comp")
      done
      if [ "$mode" != off ] && ! in_array runtime "${adjusted[@]}"; then
        adjusted+=("runtime")
      fi
      TARGETS=("${adjusted[@]}")
    else
      # down 与 up 对称，确保完整拆除（runtime 不删除会钉住 secret/redis 的孤儿回收）
      if [ "$mode" != off ] && ! in_array runtime "${TARGETS[@]}"; then
        TARGETS+=("runtime")
      fi
      if [ "$mode" = full ]; then
        local -a kept=()
        for comp in "${TARGETS[@]}"; do
          [ "$comp" = agentserver ] || kept+=("$comp")
        done
        TARGETS=("${kept[@]}")
      fi
    fi
    sort_targets
    return 0
  fi
  # 显式目标不增删；仅 up 拦截与模式矛盾的组合
  [ "$CMD" = up ] || return 0
  for comp in "${TARGETS[@]}"; do
    case "$mode:$comp" in
      full:agentserver)
        error "AGENT_RUNTIME_DEPLOY_MODE=full：AgentServer 由 agent-runtime 按需创建，不预启动（请从目标剔除 agentserver，或改用 off/routing）";;
      off:runtime)
        error "AGENT_RUNTIME_DEPLOY_MODE=off：不部署 agent-runtime（请从目标剔除 runtime，或改用 routing/full）";;
    esac
  done
}

# k8s 形态：业务 ns 部署基础服务放行并提示（本 ns 实例优先于 default 共享实例被消费）；
# 目标 ns 为共享 ns 时（-n default）静默。
# 仅 up 流程调用（置于 kctl_ensure_namespace 之前避免误建 ns）；down 必须放行；
# --render-only 经早退自然豁免。前置条件：KCTL_NS 已赋值、resolve_base_svc_ns 已执行
filter_targets_by_cluster_type() {
  is_k8s_cluster || return 0
  [ "$KCTL_NS" = "${DEPLOY_VARS["BASE_SVC_NS"]}" ] && return 0
  local comp banned=()
  for comp in "${TARGETS[@]}"; do
    case "$comp" in
      mariadb|mysql|postgresql|minio) banned+=("$comp");;
    esac
  done
  [ ${#banned[@]} -eq 0 ] && return 0
  warning "k8s 形态：将在 ${KCTL_NS} 部署基础服务(${banned[*]})，本 ns 实例优先于 default 共享实例被消费"
}

# up 预检：目标组件的硬依赖必须就位——secret/redis 已由 resolve_targets 补齐，
# 此处拦截基础服务（mariadb/minio 等）缺失
preflight() {
  local comp dep code
  local probes=()
  for comp in "${TARGETS[@]}"; do
    for dep in $(component_deps "$comp"); do
      in_array "$dep" "${TARGETS[@]}" && continue        # 本次会一起部署
      # 外挂数据库：不检查内置 DB 组件（mariadb/mysql/postgresql 依赖按 ENABLE_EXTERNAL_DB 跳过）
      [ "${DEPLOY_VARS["ENABLE_EXTERNAL_DB"]}" = "true" ] \
        && in_array "$dep" mariadb mysql postgresql \
        && continue
      # 外挂 Redis：不检查内置 Redis 组件
      [ "${DEPLOY_VARS["ENABLE_EXTERNAL_REDIS"]}" = "true" ] \
        && in_array "$dep" redis \
        && continue
      # 外部 OBS：不检查内置 MinIO 组件
      [ "${DEPLOY_VARS["ENABLE_EXTERNAL_OBS"]}" = "true" ] && in_array "$dep" minio && continue
      component_autoprovision "$dep" && continue         # 自动补齐依赖由 resolve_targets 兜底
      component_probe "$dep" >/dev/null || continue      # 无探测方式的依赖（如 pvc）跳过
      in_array "$dep" "${probes[@]}" && continue         # 去重
      probes+=("$dep")
    done
  done
  [ ${#probes[@]} -eq 0 ] && return 0
  info "依赖预检"
  for dep in "${probes[@]}"; do
    code=$(component_probe_code "$dep")
    case "$code" in
      200) info "$(component_ns "$dep")/$(component_probe "$dep" | awk -F/ '{print $2}') 已在集群";;
      404) error "缺依赖: $(component_ns "$dep")/$(component_probe "$dep")，$(dep_hint "$dep")";;
      *)   error "探测 $dep 异常 → HTTP $code";;
    esac
  done
}

# 输出单文件清单中的工作负载资源路径（Deployment/StatefulSet，逐行）；
# 多文档 yaml 先经 kctl_split_resources 拆分。调用方须 mapfile 收齐后遍历：
# 拆分产物在共享临时目录，下次拆分即 rm，管道直读 + 中途退出会读到被删文件
list_workload_resources() { # $1=yaml 清单（单文件）
  local res
  kctl_split_resources "$1" "$(kctl_tmpdir)" || return 0
  for res in "${KCTL_RESOURCES[@]}"; do
    kctl_resource_fields "$res"
    [ "$KCTL_KIND" = Deployment ] || [ "$KCTL_KIND" = StatefulSet ] || continue
    printf '%s\n' "$res"
  done
}

# 组件 → 期望镜像（渲染后 conf 清单中工作负载第一个容器的镜像；取不到输出空串）
component_expected_image() { # $1=组件
  local comp="$1" f yaml res img
  local -a reslist=()
  local file_list; file_list=$(component_files "$comp") || return 0
  for f in $file_list; do
    yaml="$CONF_DIR/$f"
    [ -f "$yaml" ] || continue
    mapfile -t reslist < <(list_workload_resources "$yaml")
    for res in "${reslist[@]}"; do
      img=$(yq -r '.spec.template.spec.containers[0].image' "$res")
      if [ "$img" != "null" ] && [ -n "$img" ]; then printf '%s' "$img"; return 0; fi
    done
  done
  return 0
}

# 读取指定标签下 Pod 的 "total ready imgok"（ready=就绪数；imgok=请求镜像与期望一致的 Pod 数）。
# 仅拉取 PodList 小响应。镜像比对取 spec.containers[].image（创建时原样保留，与渲染清单同形）；
# status.containerStatuses[].image 会被 k8s 规范化（如 redis:7-alpine →
# docker.io/library/redis:7-alpine），不可直接比对
read_pods() { # $1=label selector $2=期望镜像（空=不校验镜像）
  local sel="$1" want_img="${2:-}" resp code total ready imgok
  [ -n "${KCTL_TOKEN:-}" ] || { echo "0 0 0"; return 0; }
  resp="$(kctl_tmpdir)/pods.json"
  code=$(kctl_http GET "/api/v1/namespaces/$KCTL_NS/pods?labelSelector=$sel" "$resp")
  [ "$code" = 200 ] || { echo "0 0 0"; return 0; }
  total=$(yq '.items | length' "$resp" 2>/dev/null)
  ready=$(yq '[.items[].status.containerStatuses[] | select(.ready == true)] | length' "$resp" 2>/dev/null)
  if [ -n "$want_img" ]; then
    # snap 版 yq 不支持 --arg，镜像值内嵌表达式（已过 check_vars 白名单校验）
    imgok=$(yq "[.items[].spec.containers[] | select(.image == \"$want_img\")] | length" "$resp" 2>/dev/null)
  else
    imgok=$total
  fi
  echo "${total:-0} ${ready:-0} ${imgok:-0}"
}

# 确保工作负载副本数与镜像和渲染清单一致
ensure_workload() {
  local yaml res resp code want cur img new cname p
  local tmp; tmp=$(kctl_tmpdir)
  local -a reslist=()
  for yaml in "$@"; do
    mapfile -t reslist < <(list_workload_resources "$yaml")
    for res in "${reslist[@]}"; do
      kctl_resource_fields "$res"
      p=$(kctl_kind_path "$KCTL_KIND" "$KCTL_NS") || continue
      resp="$tmp/resp"
      [ "$(kctl_http GET "$p/$KCTL_NAME" "$resp")" = 200 ] || continue
      # 期望值/现值一次读齐（resp 文件由 GET 与 PATCH 复用）
      want=$(yq '.spec.replicas' "$res")
      cur=$(yq '.spec.replicas' "$resp")
      img=$(yq -r '.spec.template.spec.containers[0].image' "$resp")
      new=$(yq -r '.spec.template.spec.containers[0].image' "$res")
      cname=$(yq -r '.spec.template.spec.containers[0].name' "$resp")
      if [ "$want" != "null" ] && [ "$cur" != "$want" ]; then
        info "${KCTL_KIND,,}/$KCTL_NAME 副本 $cur → $want（PATCH 恢复）"
        code=$(kctl_patch "$p/$KCTL_NAME" "{\"spec\":{\"replicas\":$want}}" "$resp")
        [ "$code" = 200 ] || error "PATCH scale 失败 → HTTP $code"
      fi
      if [ "$img" != "null" ] && [ -n "$img" ] && [ "$new" != "null" ] && [ -n "$new" ] && [ "$new" != "$img" ]; then
        info "${KCTL_KIND,,}/$KCTL_NAME 镜像 → $new（PATCH 触发滚动更新）"
        code=$(kctl_patch_strategic "$p/$KCTL_NAME" \
          "{\"spec\":{\"template\":{\"spec\":{\"containers\":[{\"name\":\"$cname\",\"image\":\"$new\"}]}}}}" \
          "$resp")
        [ "$code" = 200 ] || error "PATCH image 失败 → HTTP $code"
      fi
    done
  done
}

# 等待组件达到就绪策略（每 10s 轮询，预算 WAIT_TIMEOUT_SECS 默认 300s）
wait_ready() { # $1=组件
  local comp="$1" label mode i iters total ready imgok want_img
  mode=$(component_wait_mode "$comp")
  if [ "$mode" = none ]; then   # 非工作负载组件（如共享 Secret），apply 成功即就绪
    success "$comp 就绪"
    return 0
  fi
  label=$(component_label "$comp")
  want_img=$(component_expected_image "$comp")
  iters=$(( ${WAIT_TIMEOUT_SECS:-300} / 10 ))
  (( iters >= 1 )) || iters=1   # 预算不足 10s 时至少轮询一次
  for ((i = 1; i <= iters; i++)); do
    read -r total ready imgok <<< "$(read_pods "$label" "$want_img")"
    if [ "$total" -gt 0 ]; then
      if [ "$mode" = "all" ] && [ "$ready" -eq "$total" ] && [ "$imgok" -eq "$total" ]; then
        success "$comp 就绪（$ready/$total）"
        return 0
      fi
      [ $((i % 3)) -eq 1 ] && info "等 Pod 就绪…（就绪 $ready/$total，期望镜像就位 $imgok/$total）"
    else
      [ $((i % 3)) -eq 1 ] && info "等 Pod 出现…"
    fi
    sleep 10
  done
  error "$comp 就绪等待超时（mode=$mode），人工检查: kubectl get pod -n $KCTL_NS -l '$label'"
}

# ---------- 部署 / 卸载 ----------

deploy_component() { # $1=组件 $2=序号 $3=总数
  local comp="$1" idx="$2" n="$3" f file_list
  file_list=$(component_files "$comp") || error "未知组件: $comp"
  local paths=()
  for f in $file_list; do
    [ -f "$CONF_DIR/$f" ] || error "$comp 缺少清单: $CONF_DIR/$f"
    case "$f" in
      *.yaml) paths+=("$CONF_DIR/$f");;   # 非 K8s 清单（agentserver.json）只渲染不 apply
    esac
  done
  info "[$idx/$n] $comp（yaml: $file_list）"
  kctl_run apply "${paths[@]}"
  ensure_workload "${paths[@]}"
  wait_ready "$comp"
}

# 组件是否仍被集群中存活的组件依赖——本次卸载目标中的不算（会一并删除）。
# 典型场景：secret / redis 被 gateway（产品组）与 manager-server（管理组）共享，
# 任一依赖方还在集群就不能删
# 探测语义（fail-safe）：仅 404 视为确认不存在；200 与其余状态码（含探测失败 000、
# 401/403 等无法排除存活的情况）一律保守视为存活
has_alive_dependents() { # $1=组件 → 0=有依赖方存活
  local comp="$1" other comp_ns
  comp_ns=$(component_ns "$comp")
  local -a dependents=()
  for other in "${ALL_COMPONENTS[@]}"; do
    in_array "$other" "${TARGETS[@]}" && continue
    in_array "$comp" $(component_deps "$other") || continue
    # 同 ns 限定仅适用于本 ns 自有实例（secret/redis）；基础服务消费顺序为本 ns →
    # default，跨 ns 依赖方同样构成存活依赖
    if ! in_array "$comp" mariadb mysql postgresql minio; then
      [ "$(component_ns "$other")" = "$comp_ns" ] || continue
    fi
    dependents+=("$other")
  done
  [ ${#dependents[@]} -eq 0 ] && return 1
  for other in "${dependents[@]}"; do
    case "$(component_probe_code "$other")" in
      404) continue;;
      *)   return 0;;
    esac
  done
  return 1
}

# 等待组件的 Pod 全部删除——DELETE 是异步的（Terminating/宽限期），删完请求返回不代表 Pod 消失。
# 无 Pod 的组件（如 secret）label 为空直接返回；预算 WAIT_TIMEOUT_SECS 默认 300s，5s 轮询
wait_deleted() { # $1=组件
  local comp="$1" label i iters total
  label=$(component_label "$comp")
  [ -n "$label" ] || return 0
  iters=$(( ${WAIT_TIMEOUT_SECS:-300} / 5 ))
  (( iters >= 1 )) || iters=1
  for ((i = 1; i <= iters; i++)); do
    read -r total _ <<< "$(read_pods "$label" "")"
    if [ "${total:-0}" -eq 0 ]; then
      info "$comp Pod 已删除"
      return 0
    fi
    [ $((i % 3)) -eq 1 ] && info "等 Pod 删除…（残留 $total）"
    sleep 5
  done
  error "$comp Pod 删除等待超时，人工检查: kubectl get pod -n $KCTL_NS -l '$label'"
}

delete_component() { # $1=组件 $2=序号 $3=总数
  local comp="$1" idx="$2" n="$3" f file_list
  file_list=$(component_files "$comp") || error "未知组件: $comp"
  local paths=()
  for f in $file_list; do
    case "$f" in
      *.yaml) [ -f "$CONF_DIR/$f" ] && paths+=("$CONF_DIR/$f");;   # 缺清单跳过（卸载容错）；非 K8s 清单跳过
    esac
  done
  info "[$idx/$n] $comp（yaml: $file_list）"
  if has_alive_dependents "$comp"; then
    warning "$comp 仍被集群中存活组件依赖，跳过删除"
    return 0
  fi
  kctl_run delete "${paths[@]}"
  DOWN_DELETED_ANY=1
  wait_deleted "$comp"
}

# down 末尾回收孤儿共享依赖（secret/redis）：已无存活依赖方时自动卸载；
# mariadb/minio 等基础服务带数据卷，不自动回收，仍需显式 down
# 判定口径与 has_alive_dependents 一致（fail-safe）：仅 404 算确认不存在，
# 探测失败/权限等未知状态按存活处理并跳过回收
# 本次卸载未实际删除任何组件（全部被保护跳过）时依赖状态无变化，静默跳过扫描
gc_orphan_shared_deps() {
  [ "${DOWN_DELETED_ANY:-0}" = 1 ] || return 0
  local -a orphans=()
  local dep other orphan
  for dep in secret redis; do
    # 外挂 Redis：内置组件不参与回收
    if [ "$dep" = redis ] && [ "${DEPLOY_VARS["ENABLE_EXTERNAL_REDIS"]}" = "true" ]; then
      continue
    fi
    component_autoprovision "$dep" || continue
    component_probe "$dep" >/dev/null 2>&1 || continue
    case "$(component_probe_code "$dep")" in
      200)  ;;
      404)  continue;;   # 集群中不存在，无需回收
      *)    warning "$dep 探测失败（无法确认存活状态），跳过回收"; continue;;
    esac
    orphan=1
    for other in "${ALL_COMPONENTS[@]}"; do
      [ "$other" = "$dep" ] && continue
      in_array "$dep" $(component_deps "$other") || continue
      # 跨命名空间不构成对象级依赖（同 has_alive_dependents：共享 ns 基础服务
      # 不钉住本 ns 的 Secret/Redis）
      [ "$(component_ns "$other")" = "$(component_ns "$dep")" ] || continue
      case "$(component_probe_code "$other")" in
        404) continue;;   # 该依赖方确认不存在，检查下一个
        200) info "$dep 仍被存活组件 $other 依赖，保留"; orphan=0; break;;
        *)   warning "$dep 的依赖方 $other 探测失败（无法确认存活状态），跳过回收"; orphan=0; break;;
      esac
    done
    [ "$orphan" = 1 ] && orphans+=("$dep")
  done
  [ ${#orphans[@]} -eq 0 ] && return 0
  info "回收孤儿共享依赖: ${orphans[*]}"
  # 孤儿不在本次 TARGETS 内，主流程未渲染其清单；重渲染保证文件存在且与当前 namespace 一致
  # （陈旧 conf 可能残留其他 namespace 的同名资源，直接按文件删除会误删）
  local i=1 f
  resolve_db_vars   # 孤儿清单直连渲染，不经过 render_templates，先派生 DB_* 变量
  KCTL_IGNORE_NOT_FOUND=1
  for dep in "${orphans[@]}"; do
    for f in $(render_component_files "$dep"); do
      [ -f "$CONF_DIR/$f" ] || warning "$dep 缺清单 $CONF_DIR/$f，跳过该文件"
    done
    delete_component "$dep" "$i" "${#orphans[@]}"
    i=$((i + 1))
  done
  unset KCTL_IGNORE_NOT_FOUND
  return 0
}

run_phase() { # $1=apply|delete $2=标题 其余=组件（已按依赖序排好）
  local verb="$1" title="$2"; shift 2
  local comp i=0 n=$#
  for comp in "$@"; do
    i=$((i + 1))
    case "$verb" in
      apply)  deploy_component "$comp" "$i" "$n";;
      delete) delete_component "$comp" "$i" "$n";;
    esac
  done
  success "$title完成"
}
