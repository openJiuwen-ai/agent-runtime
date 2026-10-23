#!/usr/bin/env bash
[ -n "${_DEPLOY_TEMPLATE_HANDLER_SH:-}" ] && return 0
_DEPLOY_TEMPLATE_HANDLER_SH=1

# template_handler.sh —— 清单渲染
# templates/ 下为 *.template.yaml 模板（<<VAR>> 占位符，如 <<AGENTSERVER_IMAGE>>、<<NAMESPACE>>），
# 渲染时替换为 DEPLOY_VARS 对应值；产物输出到 conf/（*.template.yaml → 同名 *.yaml）。
# up/down 前都要执行（down 依赖 conf 里的资源名与模板保持一致）

# 提取模板中的 <<VAR>> 占位符（去重排序）
extract_placeholders() { # $1=模板文件 → stdout: 占位符列表
  grep -oE '<<[^>]+>>' "$1" | sort -u
}

# ---------- 渲染前变量解析 ----------

# MANAGER_WEB_RESOLVER 为空 → 自动解析 manager-web nginx 可用的 DNS resolver；设置有值 → 直接使用：
#   openshift 形态 → OCP CoreDNS 服务名（平台固定）
#   k8s 形态       → kubectl 查 kube-dns/coredns Service ClusterIP
#                    → 本机 /etc/resolv.conf 私网 nameserver（RFC1918 校验，防 render-only 写入公网 DNS）
#                    → kube-dns 服务名兜底
resolve_manager_web_resolver() {
  [ -n "${DEPLOY_VARS["MANAGER_WEB_RESOLVER"]:-}" ] && return 0
  if ! is_k8s_cluster; then
    DEPLOY_VARS["MANAGER_WEB_RESOLVER"]="dns-default.openshift-dns.svc.cluster.local"
    return 0
  fi
  local dns_ip=""
  dns_ip=$(kubectl get svc -n kube-system -l k8s-app=kube-dns \
    -o jsonpath='{.items[0].spec.clusterIP}' 2>/dev/null || true)
  [ -n "$dns_ip" ] || dns_ip=$(kubectl get svc -n kube-system kube-dns \
    -o jsonpath='{.spec.clusterIP}' 2>/dev/null || true)
  [ -n "$dns_ip" ] || dns_ip=$(kubectl get svc -n kube-system coredns \
    -o jsonpath='{.spec.clusterIP}' 2>/dev/null || true)
  if [ -z "$dns_ip" ] && [ -r /etc/resolv.conf ]; then
    local ns; ns=$(awk '/^nameserver[[:space:]]+/ { print $2; exit }' /etc/resolv.conf 2>/dev/null || true)
    if [[ "$ns" =~ ^10\.([0-9]{1,3}\.){2}[0-9]{1,3}$ ]] \
      || [[ "$ns" =~ ^192\.168\.([0-9]{1,3}\.)[0-9]{1,3}$ ]] \
      || [[ "$ns" =~ ^172\.(1[6-9]|2[0-9]|3[0-1])\.([0-9]{1,3}\.)[0-9]{1,3}$ ]]; then
      dns_ip="$ns"
    fi
  fi
  if [ -n "$dns_ip" ]; then
    DEPLOY_VARS["MANAGER_WEB_RESOLVER"]="$dns_ip"
    info "MANAGER_WEB_RESOLVER 自动解析 → $dns_ip"
  else
    DEPLOY_VARS["MANAGER_WEB_RESOLVER"]="kube-dns.kube-system.svc.cluster.local"
    warning "MANAGER_WEB_RESOLVER 解析失败，兜底使用 kube-dns.kube-system.svc.cluster.local"
  fi
  return 0
}

# 数据库连接要素按 DB_TYPE 派生：服务名/端口/超级用户/root 密码，供模板 <<DB_*>> 占位符使用
resolve_db_vars() {
  case "${DEPLOY_VARS["DB_TYPE"]}" in
    mariadb)    DEPLOY_VARS["DB_HEADLESS_SVC"]="mariadb-headless";    DEPLOY_VARS["DB_PORT"]="3306"; DEPLOY_VARS["DB_USER"]="root";      DEPLOY_VARS["DB_ROOT_PASSWORD"]="${DEPLOY_VARS["MARIADB_ROOT_PASSWORD"]:-}";;
    mysql)      DEPLOY_VARS["DB_HEADLESS_SVC"]="mysql-headless";      DEPLOY_VARS["DB_PORT"]="3306"; DEPLOY_VARS["DB_USER"]="root";      DEPLOY_VARS["DB_ROOT_PASSWORD"]="${DEPLOY_VARS["MYSQL_ROOT_PASSWORD"]:-}";;
    postgresql) DEPLOY_VARS["DB_HEADLESS_SVC"]="postgresql-headless"; DEPLOY_VARS["DB_PORT"]="5432"; DEPLOY_VARS["DB_USER"]="postgres"; DEPLOY_VARS["DB_ROOT_PASSWORD"]="${DEPLOY_VARS["POSTGRES_PASSWORD"]:-}";;
    *) error "DB_TYPE 非法（mariadb | mysql | postgresql）: ${DEPLOY_VARS["DB_TYPE"]}" ;;
  esac
}

# 将单个 <<VAR>> 占位符替换为 DEPLOY_VARS 中的值
replace_placeholder() { # $1=占位符 $2=目标文件
  local placeholder="$1" destfile="$2"
  local var_name="${placeholder#<<}"; var_name="${var_name%>>}"
  local var_value="${DEPLOY_VARS[$var_name]:-}"
  # 变量值已通过 check_vars 白名单校验（awk gsub 替换串中 & 与 \ 为特殊字符）
  # 可选变量（OPTIONAL_VARS）留空时，占位符独占行值的整行删除（如 storageClassName 走集群默认）
  if [ -z "$var_value" ]; then
    awk -v ph="${placeholder}" '
      $0 ~ ("^[[:space:]]*[A-Za-z0-9_-]+:[[:space:]]*" ph "[[:space:]]*$") { next }
      { gsub(ph, ""); print }' \
      "$destfile" > "$destfile.tmp" && mv -f "$destfile.tmp" "$destfile" \
      || error "占位符替换失败: ${placeholder} → ${destfile}"
    return 0
  fi
  awk -v ph="${placeholder}" -v val="${var_value}" '{ gsub(ph, val); print }' \
    "$destfile" > "$destfile.tmp" && mv -f "$destfile.tmp" "$destfile" \
    || error "占位符替换失败: ${placeholder} → ${destfile}"
}

# 渲染 TARGETS 所需清单 → conf/：只处理本次目标组件关联的模板
# 渲染单个组件的全部清单 → conf/（供 render_templates 与 down 孤儿回收复用）
render_component_files() { # $1=组件 → stdout: 渲染的清单文件名（空格分隔）
  local comp="$1" t f var_name
  local -a files=()
  mkdir -p "$CONF_DIR"
  for f in $(component_files "$comp"); do
    t="$TEMPLATE_DIR/${f%.yaml}.template.yaml"
    [ -f "$t" ] || error "目标组件缺清单模板: templates/${f%.yaml}.template.yaml"
    cp -f "$t" "$CONF_DIR/$f" || error "模板复制失败: $t"
    for ph in $(extract_placeholders "$t"); do
      var_name="${ph#<<}"; var_name="${var_name%>>}"
      [ -n "${DEPLOY_VARS[$var_name]:-}" ] || in_array "$var_name" "${OPTIONAL_VARS[@]}" \
        || error "变量未设置: $var_name（检查 $CUSTOM_ENV_FILE）"
      replace_placeholder "$ph" "$CONF_DIR/$f"
    done
    files+=("$f")
  done
  printf '%s\n' "${files[*]}"
}

render_templates() {
  local comp out f
  local -a wanted=()
  resolve_manager_web_resolver   # MANAGER_WEB_RESOLVER 为空时按形态/环境自动解析
  resolve_db_vars                # DB 主机/端口/用户/密码按 DB_TYPE 派生
  for comp in "${TARGETS[@]}"; do
    out=$(render_component_files "$comp")
    for f in $out; do
      in_array "$f" "${wanted[@]}" || wanted+=("$f")
    done
  done
  info "渲染 ${#wanted[@]} 个清单 → conf/（目标: ${TARGETS[*]}）"
  apply_nfs_direct_mounts "${wanted[@]}"
  apply_k8s_extras "${wanted[@]}"
  apply_dev_code_mounts "${wanted[@]}"
}
