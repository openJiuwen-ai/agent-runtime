#!/usr/bin/env bash
# Keep this file identical in openshift/ and jiuwenswarm-ee/: each directory is
# distributed as a standalone deployment tool. No cluster access during render.

# Deliver the final, postprocessed config_sync payload to Manager. Never use a
# node-local hostPath: Manager may run on a different node from the deploy tool.
render_manager_runtime_defaults() (
  local manifest="$1" source_json="$2" namespace="$3"
  local tmp_manifest tmp_output checksum bytes
  [ -f "$manifest" ] || return 0
  if [ ! -f "$source_json" ]; then
    warning "Manager 默认运行时模板尚未准备：缺少 ${source_json}。先在同一命名空间运行 ./deploy.sh up runtime（或加 --render-only 仅生成配置），再执行 ./deploy.sh up manager。"
    return 0
  fi
  command -v yq >/dev/null 2>&1 || error "交付 Manager 默认模板需要 mikefarah/yq v4"
  command -v base64 >/dev/null 2>&1 || error "交付 Manager 默认模板需要 base64"
  bytes=$(wc -c < "$source_json")
  # kubectl client-side apply also stores base64 data in last-applied, whose
  # annotation limit is 256KiB (smaller than the Secret's 1MiB payload limit).
  [ "$bytes" -le 180000 ] || error "agentserver.json 超过 180KB 交付上限（kubectl apply 注解限制），请精简配置"
  # Parse strictly as JSON; avoid dumping a payload which may contain credentials.
  if ! yq -p=json -e '
      .type == "config_sync" and
      (.rawdata.containers | tag) == "!!seq" and
      (.rawdata.containers | length) > 0 and
      (.rawdata.templates | tag) == "!!seq" and
      (.rawdata.templates | length) > 0
    ' "$source_json" >/dev/null 2>&1; then
    error "agentserver.json 不是有效的 config_sync 配置；请使用当前部署工具重新渲染"
  fi
  if ! MANAGER_DEFAULTS_NAMESPACE="$namespace" yq -p=json -e '
      [.rawdata.templates[] | select(.namespace != strenv(MANAGER_DEFAULTS_NAMESPACE))] | length == 0
    ' "$source_json" >/dev/null 2>&1; then
    error "agentserver.json 的运行时命名空间与目标 $namespace 不一致；请在目标命名空间重新渲染后再部署 Manager"
  fi
  if command -v sha256sum >/dev/null 2>&1; then
    checksum=$(sha256sum "$source_json" | awk '{print $1}')
  else
    command -v shasum >/dev/null 2>&1 || error "交付 Manager 默认模板需要 sha256sum 或 shasum"
    checksum=$(shasum -a 256 "$source_json" | awk '{print $1}')
  fi
  [ -n "$checksum" ] || error "无法计算 Manager 默认模板校验值"
  umask 077
  tmp_manifest=$(mktemp "${manifest}.defaults-manifest.XXXXXX") || error "无法创建 Manager 默认模板临时清单"
  tmp_output=""
  trap 'rm -f "$tmp_manifest" "$tmp_output"' EXIT
  tmp_output=$(mktemp "${manifest}.defaults-output.XXXXXX") || error "无法创建 Manager 默认模板输出清单"
  MANAGER_DEFAULTS_CHECKSUM="$checksum" yq eval '
    select(.kind != "Secret" or .metadata.name != "jiuwenswarm-manager-runtime-defaults") |
    (. | select(.kind == "Deployment") | .spec.template.metadata.annotations."jiuwenswarm.io/runtime-defaults-sha256") = strenv(MANAGER_DEFAULTS_CHECKSUM) |
    (. | select(.kind == "Deployment") | .spec.template.spec.volumes) =
      ((.spec.template.spec.volumes // [] | map(select(.name != "manager-runtime-defaults"))) + [{
        "name": "manager-runtime-defaults", "secret": {"secretName": "jiuwenswarm-manager-runtime-defaults", "defaultMode": 292}
      }]) |
    (. | select(.kind == "Deployment") | .spec.template.spec.containers[] | select(.name == "manager") | .env) |=
      ((. // [] | map(select(.name != "AGENTSERVER_JSON_PATH" and .name != "MANAGER_RUNTIME_DEFAULTS_ENABLED"))) + [
        {"name": "AGENTSERVER_JSON_PATH", "value": "/etc/manager/runtime-defaults/agentserver.json"},
        {"name": "MANAGER_RUNTIME_DEFAULTS_ENABLED", "value": "true"}
      ]) |
    (. | select(.kind == "Deployment") | .spec.template.spec.containers[] | select(.name == "manager") | .volumeMounts) |=
      ((. // [] | map(select(.name != "manager-runtime-defaults"))) + [{
        "name": "manager-runtime-defaults", "mountPath": "/etc/manager/runtime-defaults", "readOnly": true
      }])
    ' "$manifest" > "$tmp_manifest" || error "Manager 默认模板挂载渲染失败"
  # Secret is first so it exists before Manager starts. down consumes this same
  # manifest and removes it along with Manager. Never log encoded/plain payloads.
  {
    printf 'apiVersion: v1\nkind: Secret\nmetadata:\n  name: jiuwenswarm-manager-runtime-defaults\n  namespace: %s\n  annotations:\n    jiuwenswarm.io/runtime-defaults-sha256: "%s"\ntype: Opaque\ndata:\n  agentserver.json: ' "$namespace" "$checksum"
    base64 < "$source_json" | tr -d '\r\n'
    printf '\n---\n'
    cat "$tmp_manifest"
  } > "$tmp_output"
  mv -f "$tmp_output" "$manifest" || error "Manager 默认模板清单保存失败"
  info "Manager 默认模板已交付：$source_json → 只读 Secret（启动时自动初始化，不自动绑定集群）"
)

# OpenShift's lightweight apply skips existing resources. Update only the Secret
# owned by this feature and the Manager Pod fields which consume it. K8s/EE use
# native kubectl apply and do not call this function.
is_manager_runtime_defaults_resource() {
  yq -e '(.kind == "Secret" and .metadata.name == "jiuwenswarm-manager-runtime-defaults" and
    .metadata.annotations."jiuwenswarm.io/runtime-defaults-sha256" != null) or
    (.kind == "Deployment" and .spec.template.metadata.annotations."jiuwenswarm.io/runtime-defaults-sha256" != null)' "$1" >/dev/null 2>&1
}

update_existing_manager_runtime_defaults() (
  local desired="$1" current="$2" checksum old_checksum patch existing_patch code tmp
  checksum=$(yq -r '.metadata.annotations."jiuwenswarm.io/runtime-defaults-sha256" // .spec.template.metadata.annotations."jiuwenswarm.io/runtime-defaults-sha256" // ""' "$desired")
  [ -n "$checksum" ] || return 1
  if [ "$KCTL_KIND" = Secret ] && [ "$KCTL_NAME" = jiuwenswarm-manager-runtime-defaults ]; then
    old_checksum=$(yq -r '.metadata.annotations."jiuwenswarm.io/runtime-defaults-sha256" // ""' "$current")
    if [ "$old_checksum" = "$checksum" ] && MANAGER_DEFAULTS_DESIRED="$desired" yq -e '
      .data."agentserver.json" == load(strenv(MANAGER_DEFAULTS_DESIRED)).data."agentserver.json"
      ' "$current" >/dev/null 2>&1; then return 0; fi
    umask 077
    tmp=$(mktemp "${desired}.update.XXXXXX")
    trap 'rm -f "$tmp"' EXIT
    # Retain resourceVersion and unrelated metadata/data. File-backed PUT avoids
    # placing credentials in command arguments and the OS argument-size limit.
    MANAGER_DEFAULTS_DESIRED="$desired" yq eval '
      .data."agentserver.json" = load(strenv(MANAGER_DEFAULTS_DESIRED)).data."agentserver.json" |
      .metadata.annotations."jiuwenswarm.io/runtime-defaults-sha256" = load(strenv(MANAGER_DEFAULTS_DESIRED)).metadata.annotations."jiuwenswarm.io/runtime-defaults-sha256"
      ' "$current" > "$tmp" || error "Manager 默认模板 Secret 更新渲染失败"
    code=$(kctl_http PUT "$KCTL_PATH/$KCTL_NAME" "$current" "$tmp")
    [ "$code" = 200 ] || error "Manager 默认模板 Secret 更新失败 → HTTP $code"
    success "$KCTL_LK/$KCTL_NAME updated"
    return 0
  fi
  [ "$KCTL_KIND" = Deployment ] || return 1
  patch=$(manager_runtime_defaults_pod_patch "$desired") || error "Manager 默认模板 Pod 更新渲染失败"
  existing_patch=$(manager_runtime_defaults_pod_patch "$current") || return 1
  if [ "$patch" = "$existing_patch" ]; then return 0; fi
  code=$(kctl_patch_strategic "$KCTL_PATH/$KCTL_NAME" "$patch" "$current")
  [ "$code" = 200 ] || error "Manager 默认模板 Pod 更新失败 → HTTP $code"
  success "$KCTL_LK/$KCTL_NAME 默认模板挂载已更新"
)

manager_runtime_defaults_pod_patch() {
  yq -o=json -I=0 '{"spec": {"template": {
    "metadata": {"annotations": {"jiuwenswarm.io/runtime-defaults-sha256": .spec.template.metadata.annotations."jiuwenswarm.io/runtime-defaults-sha256"}},
    "spec": {
      "volumes": [.spec.template.spec.volumes[] | select(.name == "manager-runtime-defaults")],
      "containers": [.spec.template.spec.containers[] | select(.name == "manager") | {
        "name": .name,
        "env": [.env[] | select(.name == "AGENTSERVER_JSON_PATH" or .name == "MANAGER_RUNTIME_DEFAULTS_ENABLED")],
        "volumeMounts": [.volumeMounts[] | select(.name == "manager-runtime-defaults")]
      }]
    }
  }}}' "$1"
}
