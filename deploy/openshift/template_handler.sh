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
    case "$f" in
      *.json) t="$TEMPLATE_DIR/${f%.json}.template.json";;
      *)      t="$TEMPLATE_DIR/${f%.yaml}.template.yaml";;
    esac
    [ -f "$t" ] || error "目标组件缺清单模板: $t"
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
  # 变量派生（resolve_*）已统一在 check_vars 完成，此处只做清单渲染与后处理
  for comp in "${TARGETS[@]}"; do
    # 渲染在命令替换子 shell 中执行，error() 的 exit 只退出子 shell：
    # 不检查退出码时错误文本会混入 out 被当清单文件名（表现为 stat conf/<错误文案>）
    out=$(render_component_files "$comp") || error "组件 ${comp} 渲染失败（检查 $CUSTOM_ENV_FILE 与 templates/）"
    for f in $out; do
      in_array "$f" "${wanted[@]}" || wanted+=("$f")
    done
  done
  info "渲染 ${#wanted[@]} 个清单 → conf/（目标: ${TARGETS[*]}）"
  apply_nfs_direct_mounts "${wanted[@]}"
  apply_k8s_extras "${wanted[@]}"
  apply_runtime_mode_extras "${wanted[@]}"
  apply_sandbox_type_extras "${wanted[@]}"
  apply_dev_code_mounts "${wanted[@]}"
}
