#!/usr/bin/env bash
# template_handler.sh —— 清单渲染（对齐 ../jiuwenswarm-ee/template_handler.sh 的 <<VAR>> 机制）
# templates/ 下全部为 *.template.yaml（含 <<VAR>> 占位符，如 <<AGENTSERVER_IMAGE>>、<<NAMESPACE>>），
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
  awk -v ph="${placeholder}" -v val="${var_value}" '{ gsub(ph, val); print }' \
    "$destfile" > "$destfile.tmp" && mv -f "$destfile.tmp" "$destfile" \
    || error "占位符替换失败: ${placeholder} → ${destfile}"
}

# 渲染 TARGETS 所需清单 → conf/：只处理本次目标组件关联的模板
render_templates() {
  local t f base conf_name comp ph var_name count=0
  local -a wanted=()
  for comp in "${TARGETS[@]}"; do
    for f in $(component_files "$comp"); do
      in_array "$f" "${wanted[@]}" || wanted+=("$f")
    done
  done
  # 目标组件的清单必须在 templates/ 有对应模板，缺了在渲染前报错
  for f in "${wanted[@]}"; do
    [ -f "$TEMPLATE_DIR/${f%.yaml}.template.yaml" ] || error "目标组件缺清单模板: templates/${f%.yaml}.template.yaml"
  done
  mkdir -p "$CONF_DIR"
  for t in "$TEMPLATE_DIR"/*.template.yaml; do
    base="$(basename "$t")"
    conf_name="${base%.template.yaml}.yaml"
    f="$CONF_DIR/$conf_name"
    in_array "$conf_name" "${wanted[@]}" || continue
    cp -f "$t" "$f" || error "模板复制失败: $t"
    for ph in $(extract_placeholders "$t"); do
      var_name="${ph#<<}"; var_name="${var_name%>>}"
      [ -n "${DEPLOY_VARS[$var_name]:-}" ] || error "变量未设置: $var_name（检查 $CUSTOM_ENV_FILE）"
      replace_placeholder "$ph" "$f"
    done
    count=$((count + 1))
  done
  info "渲染 $count 个清单 → conf/（目标: ${TARGETS[*]}）"
}
