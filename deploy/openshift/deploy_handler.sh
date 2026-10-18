#!/usr/bin/env bash
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

# 组件存在性探测：stdout 输出 http_code（200=已在集群；无探测映射或路径解析失败输出 000）
component_probe_code() { # $1=组件
  local probe p
  component_probe "$1" >/dev/null || { echo "000"; return 0; }
  probe=$(component_probe "$1")
  p=$(kctl_kind_path "${probe%%/*}" "$KCTL_NS") || { echo "000"; return 0; }
  kctl_http GET "$p/${probe#*/}" /dev/null
}

# up 依赖补全：目标组件的可自动补齐依赖（secret/redis）在集群中缺失时纳入本次部署目标；
# 集群已有则复用、不重复部署。基础服务不可自动补齐，缺失交给 preflight 报错
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

# up 预检：目标组件的硬依赖必须就位——secret/redis 已由 resolve_targets 补齐，
# 此处拦截基础服务（mariadb/minio 等）缺失
preflight() {
  local comp dep code
  local probes=()
  for comp in "${TARGETS[@]}"; do
    for dep in $(component_deps "$comp"); do
      in_array "$dep" "${TARGETS[@]}" && continue        # 本次会一起部署
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
      200) info "$(component_probe "$dep" | awk -F/ '{print $2}') 已在集群";;
      404) error "缺依赖: $(component_probe "$dep")，$(dep_hint "$dep")";;
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
# 不走 kubectl get：其 discovery 缓存过期后会拉取 MB 级响应体，跨境链路不可用；
# 此处仅取 PodList 小响应。比对 Pod spec 里的镜像（创建时原样保留，与渲染清单同形），
# status.containerStatuses[].image 会被 k8s 规范化（redis:7-alpine →
# docker.io/library/redis:7-alpine），直接比对必失配
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

# 确保工作负载副本数与清单一致——apply 对已存在资源 unchanged 跳过，
# 集群中被人为缩容的副本无法靠 apply 恢复，需 PATCH scale
ensure_replicas() { # $@=组件 yaml 清单
  local yaml res resp code want cur p
  local tmp; tmp=$(kctl_tmpdir)
  local -a reslist=()
  for yaml in "$@"; do
    mapfile -t reslist < <(list_workload_resources "$yaml")
    for res in "${reslist[@]}"; do
      kctl_resource_fields "$res"   # 重解析当前资源字段
      want=$(yq '.spec.replicas' "$res")
      [ "$want" = "null" ] && continue
      p=$(kctl_kind_path "$KCTL_KIND" "$KCTL_NS") || continue
      resp="$tmp/resp"
      [ "$(kctl_http GET "$p/$KCTL_NAME" "$resp")" = 200 ] || continue
      cur=$(yq '.spec.replicas' "$resp")
      [ "$cur" = "$want" ] && continue
      info "${KCTL_KIND,,}/$KCTL_NAME 副本 $cur → $want（PATCH 恢复）"
      code=$(kctl_patch "$p/$KCTL_NAME" "{\"spec\":{\"replicas\":$want}}" "$resp")
      [ "$code" = 200 ] || error "PATCH scale 失败 → HTTP $code"
    done
  done
}

# 确保工作负载镜像与渲染清单一致——apply 对已存在资源 unchanged 跳过，
# 升级/回滚后的镜像变更需 strategic-merge PATCH 对齐（按容器 name 合并），触发滚动更新
ensure_image() { # $@=组件 yaml 清单
  local yaml res resp code img new cname p
  local tmp; tmp=$(kctl_tmpdir)
  local -a reslist=()
  for yaml in "$@"; do
    mapfile -t reslist < <(list_workload_resources "$yaml")
    for res in "${reslist[@]}"; do
      kctl_resource_fields "$res"   # 重解析当前资源字段
      p=$(kctl_kind_path "$KCTL_KIND" "$KCTL_NS") || continue
      resp="$tmp/resp"
      [ "$(kctl_http GET "$p/$KCTL_NAME" "$resp")" = 200 ] || continue
      img=$(yq -r '.spec.template.spec.containers[0].image' "$resp")
      if [ "$img" = "null" ] || [ -z "$img" ]; then continue; fi
      new=$(yq -r '.spec.template.spec.containers[0].image' "$res")
      if [ "$new" = "null" ] || [ -z "$new" ]; then continue; fi
      [ "$new" = "$img" ] && continue
      cname=$(yq -r '.spec.template.spec.containers[0].name' "$resp")
      info "${KCTL_KIND,,}/$KCTL_NAME 镜像 → $new（PATCH 触发滚动更新）"
      code=$(kctl_patch_strategic "$p/$KCTL_NAME" \
        "{\"spec\":{\"template\":{\"spec\":{\"containers\":[{\"name\":\"$cname\",\"image\":\"$new\"}]}}}}" \
        "$resp")
      [ "$code" = 200 ] || error "PATCH image 失败 → HTTP $code"
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
    paths+=("$CONF_DIR/$f")
  done
  info "[$idx/$n] $comp（yaml: $file_list）"
  kctl_run apply "${paths[@]}"
  ensure_replicas "${paths[@]}"
  ensure_image "${paths[@]}"
  wait_ready "$comp"
}

# 组件是否仍被集群中存活的组件依赖——本次卸载目标中的不算（会一并删除）。
# 典型场景：secret / redis 被 gateway（产品组）与 manager-server（管理组）共享，
# 任一依赖方还在集群就不能删
has_alive_dependents() { # $1=组件 → 0=有依赖方存活
  local comp="$1" other
  local -a dependents=()
  for other in "${ALL_COMPONENTS[@]}"; do
    in_array "$other" "${TARGETS[@]}" && continue
    in_array "$comp" $(component_deps "$other") && dependents+=("$other")
  done
  [ ${#dependents[@]} -eq 0 ] && return 1
  for other in "${dependents[@]}"; do
    [ "$(component_probe_code "$other")" = 200 ] && return 0
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
    [ -f "$CONF_DIR/$f" ] && paths+=("$CONF_DIR/$f")   # 缺清单跳过（卸载容错）
  done
  info "[$idx/$n] $comp（yaml: $file_list）"
  if has_alive_dependents "$comp"; then
    warning "$comp 仍被集群中存活组件依赖，跳过删除"
    return 0
  fi
  kctl_run delete "${paths[@]}"
  wait_deleted "$comp"
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
