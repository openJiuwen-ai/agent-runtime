#!/usr/bin/env bash
[ -n "${_DEPLOY_K8S_HANDLER_SH:-}" ] && return 0
_DEPLOY_K8S_HANDLER_SH=1

# k8s_handler.sh —— 原生 k8s 形态（CLUSTER_TYPE=k8s）专属函数；openshift 形态下均不生效。
# 包含三部分：
#   1. k8s 形态通道实现（_kctl_k8s_*）：kubectl.sh 共享原语的 k8s 分支，按形态分流调用
#   2. manager-web NodePort Service 追加（apply_k8s_extras）
#   3. NFS 直连挂载（check_nfs_vars + apply_nfs_direct_mounts）：PVC 卷改写为 NFS 直连挂载

# ---------- NFS 直连挂载改写 ----------
# 改写范围（yq 动态修改渲染产物 conf/*.yaml）：mariadb/mysql/postgresql/agentserver/minio → 删 PVC 文档，卷改为 nfs
# redis 纯缓存不挂卷（模板已关闭 AOF），不参与改写
# claw-pvc.yaml 由 component_files 按形态剔除，k8s 形态不渲染不部署。
# NFS 路径布局：<NFS_SHARE_PATH>/<NAMESPACE>/<组件>（v4 相对导出根），组件间数据隔离；
# 数据目录须在 NFS 服务器上预先创建（NFS 不支持远程 mkdir）

# NFS 变量校验（check_vars 统一入口调用；openshift 形态 no-op）：
# 服务器地址必填，共享根路径须为绝对路径
check_nfs_vars() {
  is_k8s_cluster || return 0
  local val="${DEPLOY_VARS["NFS_SERVER_ADDR"]:-}"
  [ -n "$val" ] || error "CLUSTER_TYPE=k8s 需配置 NFS_SERVER_ADDR（检查 $CUSTOM_ENV_FILE）"
  [[ "$val" =~ ^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$ ]] \
    || error "NFS_SERVER_ADDR 非法（须为主机名或 IP）: $val"
  val="${DEPLOY_VARS["NFS_SHARE_PATH"]:-}"
  [[ "$val" =~ ^/[A-Za-z0-9_./-]*$ ]] \
    || error "NFS_SHARE_PATH 非法（须为绝对路径）: $val"
}

# 单文档资源：卷的 persistentVolumeClaim → nfs
_nfs_rewrite_volume() { # $1=资源文件 $2=卷名 $3=nfs路径 $4=server
  local file="$1" vol="$2" path="$3" server="$4"
  yq -i "(.spec.template.spec.volumes[] | select(.name == \"$vol\") | .persistentVolumeClaim) = null
         | (.spec.template.spec.volumes[] | select(.name == \"$vol\") | .nfs) = {\"server\": \"$server\", \"path\": \"$path\"}" "$file"
  [ "$(yq ".spec.template.spec.volumes[] | select(.name == \"$vol\") | .nfs.server" "$file")" != "null" ] \
    || error "NFS 卷改写失败: $(basename "$file")/$vol"
}

# 多文档清单改写：拆分 → 逐资源处理 → 重组（避免多文档 yq select 丢文档）
_nfs_rewrite_file() { # $1=conf 清单 $2=卷名 $3=nfs路径 $4=server
  local file="$1" vol="$2" path="$3" server="$4"
  local tmp res kind kept=()
  tmp="$CONF_DIR/nfsrw-$$"; mkdir -p "$tmp"
  kctl_split_resources "$file" "$tmp" || { rm -rf "$tmp"; return 0; }
  for res in "${KCTL_RESOURCES[@]}"; do
    kind=$(yq '.kind' "$res")
    case "$kind" in
      PersistentVolumeClaim) continue;;
      Deployment|StatefulSet) _nfs_rewrite_volume "$res" "$vol" "$path" "$server";;
    esac
    kept+=("$res")
  done
  awk 'FNR==1 && NR>1 {print "---"} {print}' "${kept[@]}" > "$file"
  rm -rf "$tmp"
}

# CLUSTER_TYPE=k8s 的渲染后处理入口；其余形态 no-op
# 按需改写：仅处理本次渲染产物（render_templates 传入的清单），conf/ 中其他文件不动；
apply_nfs_direct_mounts() { # $@=本次渲染的清单文件名
  is_k8s_cluster || return 0
  local server="${DEPLOY_VARS["NFS_SERVER_ADDR"]:-}"
  [ -n "$server" ] || error "CLUSTER_TYPE=k8s 需配置 NFS_SERVER_ADDR"
  local base="${DEPLOY_VARS["NFS_SHARE_PATH"]}"
  base="${base%/}"
  local ns="${DEPLOY_VARS["NAMESPACE"]}"
  local -a rewritten=()
  # 改写范围遵循约定：模板 <组件>.yaml，卷名 <组件>-data，数据目录 <base>/<ns>/<组件>；
  # 卷名与模板不符时 _nfs_rewrite_volume 的结果校验会报错拦截
  local cmp
  for cmp in mariadb mysql postgresql minio agentserver; do
    in_array "$cmp.yaml" "$@" || continue
    _nfs_rewrite_file "$CONF_DIR/$cmp.yaml" "${cmp}-data" "$base/$ns/$cmp" "$server"
    info "NFS 直连改写完成: $server:$base/$ns/$cmp"
    rewritten+=("$cmp")
  done
  if [ ${#rewritten[@]} = 1 ]; then
    info "若目录未创建，请在 NFS 服务器执行: mkdir -p $base/$ns/${rewritten[0]}"
  elif [ ${#rewritten[@]} -gt 1 ]; then
    info "若目录未创建，请在 NFS 服务器执行: mkdir -p $base/$ns/{$(IFS=,; echo "${rewritten[*]}")}"
  fi
  return 0
}

# ---------- k8s 形态通道实现（由 kubectl.sh 的共享原语按形态分流调用） ----------
# kubectl.sh 的 kctl_http / _kctl_patch / 资源动词在 CLUSTER_TYPE=k8s 时路由到本节函数；
# 语义对齐 curl 通道（错误码映射、网络类故障重试），保证上层 yq 解析行为一致

# kubectl stderr → http_code（对齐 curl 通道语义；网络类故障输出 000 触发重试）
_kctl_k8s_err_code() { # $1=stderr 文本
  case "$1" in
    *"(NotFound)"*)          echo 404;;
    *"(AlreadyExists)"*)     echo 409;;
    *"(Forbidden)"*)         echo 403;;
    *"(Unauthorized)"*)      echo 401;;
    *"(BadRequest)"*)        echo 400;;
    *"Unable to connect"*|*"connection refused"*|*"i/o timeout"*|*"Client.Timeout"*|*"no such host"*)
                             echo 000;;
    *)                       echo 500;;
  esac
}

# GET 走 kubectl get --raw（路径含 namespace/query 原样透传）；
# POST/PUT/DELETE 不经此通道（资源动词按文件处理，见各 kctl_*_resource）
_kctl_k8s_http() { # $1=method $2=path $3=resp文件
  local method="$1" url="$2" out="$3" err code n
  [ "$method" = GET ] || error "k8s 形态不支持 raw $method（资源操作走文件动词）"
  err="$(kctl_tmpdir)/k8s-err"
  for n in 1 2 3; do
    if kubectl get --raw "$url" > "$out" 2> "$err"; then
      printf '%s' "200"; return 0
    fi
    code=$(_kctl_k8s_err_code "$(cat "$err")")
    [ "$code" = 000 ] || break
    sleep 2
  done
  mv -f "$err" "$out"
  printf '%s' "$code"
}

# merge-patch / strategic merge patch：路径解析出 ns/plural/name → kubectl patch
_kctl_k8s_patch() { # $1=Content-Type $2=资源路径 $3=JSON体 $4=resp文件
  local ct="$1" url="$2" body="$3" out="$4" err plural ns name code type
  case "$ct" in
    *strategic-merge-patch*) type=strategic;;
    *merge-patch*)           type=merge;;
    *) error "k8s 形态不支持的 patch 类型: $ct";;
  esac
  plural=$(sed -E 's#.*/([^/]+)/[^/]+$#\1#' <<< "$url")
  name=$(sed -E 's#.*/##' <<< "$url")
  ns=""; [[ "$url" == *"/namespaces/"* ]] && ns=$(sed -E 's#.*/namespaces/([^/]+)/.*#\1#' <<< "$url")
  err="$(kctl_tmpdir)/k8s-err"
  if kubectl patch "$plural" "$name" ${ns:+-n "$ns"} --type="$type" -p "$body" -o json > "$out" 2> "$err"; then
    printf '%s' "200"; return 0
  fi
  code=$(_kctl_k8s_err_code "$(cat "$err")")
  mv -f "$err" "$out"
  printf '%s' "$code"
}

# ---------- manager-web NodePort Service ----------

# 资源动词的 k8s 分支（由 kubectl.sh 的 kctl_*_resource 按 is_k8s_cluster 分流调用；
# KCTL_LK/KCTL_NAME 已由调用方 kctl_resolve_resource 解析）
_kctl_k8s_apply_resource() { # $1=资源文件
  local err out code
  err="$(kctl_tmpdir)/k8s-err"
  if out=$(kubectl apply -f "$1" 2> "$err"); then
    info "$out"
  else
    code=$(_kctl_k8s_err_code "$(cat "$err")")
    kctl_show_err "$code" "$err"
  fi
}

_kctl_k8s_create_resource() { # $1=资源文件
  local err code
  err="$(kctl_tmpdir)/k8s-err"
  if kubectl create -f "$1" > /dev/null 2> "$err"; then
    success "$KCTL_LK/$KCTL_NAME created"
  else
    code=$(_kctl_k8s_err_code "$(cat "$err")")
    if [ "$code" = 409 ]; then
      error "Error from server (AlreadyExists): $KCTL_LK/$KCTL_NAME"
    fi
    kctl_show_err "$code" "$err"
  fi
}

_kctl_k8s_replace_resource() { # $1=资源文件
  local err code
  err="$(kctl_tmpdir)/k8s-err"
  if kubectl replace -f "$1" > /dev/null 2> "$err"; then
    success "$KCTL_LK/$KCTL_NAME replaced"
  else
    code=$(_kctl_k8s_err_code "$(cat "$err")")
    if [ "$code" = 404 ]; then
      error "$KCTL_LK/$KCTL_NAME 不存在（replace 不做创建，请先 apply）"
    fi
    kctl_show_err "$code" "$err"
  fi
}

_kctl_k8s_delete_resource() { # $1=资源文件
  local err code out
  err="$(kctl_tmpdir)/k8s-err"
  if [ "${KCTL_IGNORE_NOT_FOUND:-0}" = 1 ]; then
    out=$(kubectl delete -f "$1" --ignore-not-found 2> "$err") \
      || { code=$(_kctl_k8s_err_code "$(cat "$err")"); kctl_show_err "$code" "$err"; }
    info "${out:-$KCTL_LK/$KCTL_NAME not found（已忽略）}"
    return 0
  fi
  if kubectl delete -f "$1" > /dev/null 2> "$err"; then
    success "$KCTL_LK/$KCTL_NAME deleted"
  else
    code=$(_kctl_k8s_err_code "$(cat "$err")")
    if [ "$code" = 404 ]; then
      error "Error from server (NotFound): $KCTL_LK/$KCTL_NAME"
    fi
    kctl_show_err "$code" "$err"
  fi
}

# 单端口占用判断：本机监听（netstat/ss）+ 集群 nodePort 绑定（kubectl 获取后 yq 解析）
_port_taken() { # → 0=占用 1=空闲
  local port="$1"
  grep -qE ":${port}[[:space:]]" <<<"$2" && return 0
  grep -qx "${port}" <<<"$3" && return 0
  return 1
}

# NodePort 申请：
#   .env.custom 已配置 → 校验占用，被占即报错
#   未配置             → 从 NODE_PORT_RANGE_START~END 自动挑选首个空闲端口写入 DEPLOY_VARS
#   NO_CHECK_PORTS=true → 跳过检查，但变量必须已显式配置
ensure_node_port() { # $1=DEPLOY_VARS 变量名（如 MANAGER_WEB_NODE_PORT）
  local name="$1"
  local configured="${DEPLOY_VARS[$name]:-}"
  if [ "${DEPLOY_VARS["NO_CHECK_PORTS"]}" = "true" ]; then
    [ -n "$configured" ] || error "NO_CHECK_PORTS=true 时需在 .env.custom 显式配置 ${name}"
    return 0
  fi
  # 占用集合一次性预取（避免逐端口查询 APIServer）；查询失败按空处理（视为无占用）。
  # 仅排除「本 namespace 的」manager-web NodePort Service：其他 namespace 同名 Service
  # 占用的 nodePort 是真实冲突（nodePort 集群级唯一），必须计入占用
  local local_listens cluster_ports svc_json current_port
  local ns="${DEPLOY_VARS["NAMESPACE"]}"
  local_listens=$(netstat -tuln 2>/dev/null || ss -tuln 2>/dev/null || true)
  svc_json=$(kubectl get svc --all-namespaces -o json 2>/dev/null || true)
  cluster_ports=$(printf '%s' "$svc_json" \
    | yq '.items[] | select(.metadata.name != "jiuwenclaw-manager-web-nodeport" or .metadata.namespace != "'"${ns}"'") | .spec.ports[]? | .nodePort | select(. != null)' 2>/dev/null \
    | sort -u || true)
  # 本 namespace 自家 Service 已持有的 nodePort：留空自动分配时优先复用（复跑端口稳定）
  current_port=$(printf '%s' "$svc_json" \
    | yq '.items[] | select(.metadata.name == "jiuwenclaw-manager-web-nodeport" and .metadata.namespace == "'"${ns}"'") | .spec.ports[]? | .nodePort | select(. != null)' 2>/dev/null \
    | head -1)
  if [ -n "$configured" ]; then
    if _port_taken "$configured" "$local_listens" "$cluster_ports"; then
      error "${name}=${configured} 已被占用，请在 .env.custom 更换，或留空以自动分配"
    fi
    info "使用预配置 NodePort: ${name}=${configured}"
    return 0
  fi
  local port
  if [ -n "$current_port" ]; then
    DEPLOY_VARS[$name]="$current_port"
    info "复用已分配 NodePort: ${name}=$current_port"
    return 0
  fi
  for port in $(seq "${DEPLOY_VARS["NODE_PORT_RANGE_START"]}" "${DEPLOY_VARS["NODE_PORT_RANGE_END"]}"); do
    if ! _port_taken "$port" "$local_listens" "$cluster_ports"; then
      DEPLOY_VARS[$name]="$port"
      info "自动分配 NodePort: ${name}=$port（范围 ${DEPLOY_VARS["NODE_PORT_RANGE_START"]}-${DEPLOY_VARS["NODE_PORT_RANGE_END"]}）"
      return 0
    fi
  done
  error "NodePort 自动分配失败：${DEPLOY_VARS["NODE_PORT_RANGE_START"]}-${DEPLOY_VARS["NODE_PORT_RANGE_END"]} 范围内无空闲端口"
}

# k8s 形态给 manager-web 追加 NodePort Service（openshift 形态模板仅保留 ClusterIP）
# 幂等：render_templates 每次从模板重建 conf 清单，重复渲染不会叠加
append_manager_web_nodeport() { # $1=conf/manager-web.yaml
  local file="$1"
  local ns="${DEPLOY_VARS["NAMESPACE"]}" node_port="${DEPLOY_VARS["MANAGER_WEB_NODE_PORT"]:-}"
  [ -n "$node_port" ] || error "MANAGER_WEB_NODE_PORT 未分配（先调用 ensure_node_port）"
  [ -z "$(yq 'select(.kind == "Service" and .metadata.name == "jiuwenclaw-manager-web-nodeport") | .metadata.name' "$file")" ] \
    || { info "manager-web NodePort Service 已存在，跳过追加"; return 0; }
  yq eval-all -i 'select(fileIndex == 0), select(fileIndex == 1)' "$file" \
    <(yq -n -o=yaml '.apiVersion = "v1" | .kind = "Service" | .metadata.name = "jiuwenclaw-manager-web-nodeport" | .metadata.namespace = "'"$ns"'" | .spec.type = "NodePort" | .spec.selector.app = "jiuwenclaw-manager-web" | .spec.ports = [{"name": "http", "port": 5273, "targetPort": 5273, "nodePort": '"$node_port"'}]') \
    || error "manager-web NodePort Service 追加失败: $file"
  [ -n "$(yq 'select(.kind == "Service" and .metadata.name == "jiuwenclaw-manager-web-nodeport") | .spec.type' "$file")" ] \
    || error "manager-web NodePort Service 校验失败: $file"
  info "manager-web NodePort Service 已追加（nodePort=$node_port，外部访问 http://<节点IP>:$node_port）"
}

# k8s 形态清单后处理分发（与 apply_nfs_direct_mounts 并列；其余形态 no-op）
apply_k8s_extras() { # $@=本次渲染的清单文件名
  is_k8s_cluster || return 0
  in_array "manager-web.yaml" "$@" || return 0
  ensure_node_port MANAGER_WEB_NODE_PORT
  append_manager_web_nodeport "$CONF_DIR/manager-web.yaml"
}

# ---------- dev 模式代码挂载（MODE=dev） ----------
# hostPath 把宿主机仓库代码挂载到镜像内代码路径，改代码无需重建镜像；Pod 固定到代码所在节点
# 仅 CLUSTER_TYPE=k8s 支持（openshift SCC 禁止 hostPath）

# 给清单内 Deployment 的首个容器追加一个 hostPath 代码挂载（volume + volumeMount）
# 注：select(...).path |= 是更新运算符（非管道 select），yq 对不匹配的文档原样保留，
# 多文档清单（Deployment + Service）不会丢文档——勿改成 select(...) | .path = 值 的管道形式
add_code_mount() { # $1=清单文件 $2=卷名 $3=宿主机路径 $4=容器内路径 $5=hostPath.type(默认 Directory)
  local file="$1" name="$2" host_path="$3" mount_path="$4" host_type="${5:-Directory}"
  yq -i '
    select(.kind == "Deployment").spec.template.spec.containers[0].volumeMounts
      |= ((. // []) + [{"name": "'"${name}"'", "mountPath": "'"${mount_path}"'"}])
    | select(.kind == "Deployment").spec.template.spec.volumes
      |= ((. // []) + [{"name": "'"${name}"'", "hostPath": {"path": "'"${host_path}"'", "type": "'"${host_type}"'"}}])
  ' "$file"
}

mount_claw_code() { # jiuwenclaw 仓库 → 镜像内代码根
  [ -n "${DEPLOY_VARS["CLAW_CODE_PATH"]:-}" ] || return 0
  add_code_mount "$1" "claw-code" "${DEPLOY_VARS["CLAW_CODE_PATH"]}" "${DEPLOY_VARS["CLAW_POD_CODE_PATH"]}"
}

mount_runtime_code() { # agent-runtime 仓库 → 镜像内代码根
  [ -n "${DEPLOY_VARS["RUNTIME_CODE_PATH"]:-}" ] || return 0
  add_code_mount "$1" "runtime-code" "${DEPLOY_VARS["RUNTIME_CODE_PATH"]}" "${DEPLOY_VARS["RUNTIME_POD_CODE_PATH"]}"
}

mount_runtime_pkg() { # agent-runtime 的 foundation/management 子包 → site-packages
  [ -n "${DEPLOY_VARS["RUNTIME_CODE_PATH"]:-}" ] || return 0
  local src="${DEPLOY_VARS["RUNTIME_CODE_PATH"]}" dst="${DEPLOY_VARS["RUNTIME_POD_PKG_PATH"]}"
  add_code_mount "$1" "foundation-code" "${src}/foundation/openjiuwen_runtime/foundation" "${dst}/foundation"
  add_code_mount "$1" "management-code" "${src}/management/openjiuwen_runtime/management" "${dst}/management"
}

mount_core_pkg() { # agent-core 的 openjiuwen 包 → site-packages
  [ -n "${DEPLOY_VARS["CORE_CODE_PATH"]:-}" ] || return 0
  add_code_mount "$1" "core-code" "${DEPLOY_VARS["CORE_CODE_PATH"]}/openjiuwen" "${DEPLOY_VARS["CORE_POD_PKG_PATH"]}"
}

mount_web_nginx_template() { # web：仓库 nginx 模板与可选 upstream 脚本盖到镜像 entrypoint 读取路径
  add_code_mount "$1" "web-nginx-template" \
    "${DEPLOY_VARS["CLAW_CODE_PATH"]}/docker/web.nginx.conf.template" \
    "/etc/nginx/templates/default.conf.template" "File"
  add_code_mount "$1" "web-optional-upstreams" \
    "${DEPLOY_VARS["CLAW_CODE_PATH"]}/docker/web-optional-upstreams.sh" \
    "/docker-entrypoint.d/40-optional-upstreams.sh" "File"
}

mount_manager_web_nginx_template() { # manager-web：仓库 nginx 模板同上
  add_code_mount "$1" "manager-web-nginx-template" \
    "${DEPLOY_VARS["RUNTIME_CODE_PATH"]}/docker/manager-web.nginx.conf.template" \
    "/etc/nginx/templates/default.conf.template" "File"
}

# 自动识别当前节点（hostPath 是节点本地的，Pod 必须固定到代码所在节点）：
# 优先 .env.custom 的 CURRENT_NODE_NAME；否则按本机 hostname、再按本机 IP 匹配集群节点
fetch_current_node_name() {
  [ -n "${DEPLOY_VARS["CURRENT_NODE_NAME"]:-}" ] && { info "CURRENT_NODE_NAME: ${DEPLOY_VARS["CURRENT_NODE_NAME"]}"; return 0; }
  local host ip
  host=$(hostname)
  DEPLOY_VARS["CURRENT_NODE_NAME"]=$(kubectl get nodes -o json 2>/dev/null \
    | HOST="$host" yq -r '.items[] | select(.status.addresses[]? | select(.type == "Hostname") | .address == strenv(HOST)) | .metadata.name' 2>/dev/null | head -1)
  [ -n "${DEPLOY_VARS["CURRENT_NODE_NAME"]}" ] && { info "CURRENT_NODE_NAME: ${DEPLOY_VARS["CURRENT_NODE_NAME"]}（按 hostname 识别）"; return 0; }
  for ip in $(hostname -I 2>/dev/null); do
    DEPLOY_VARS["CURRENT_NODE_NAME"]=$(kubectl get nodes -o json 2>/dev/null \
      | IP="$ip" yq -r '.items[] | select(.status.addresses[]? | select(.type == "InternalIP") | .address == strenv(IP)) | .metadata.name' 2>/dev/null | head -1)
    [ -n "${DEPLOY_VARS["CURRENT_NODE_NAME"]}" ] && { info "CURRENT_NODE_NAME: ${DEPLOY_VARS["CURRENT_NODE_NAME"]}（按 IP 识别）"; return 0; }
  done
  error "无法识别当前节点（本机可能不是集群节点）：MODE=dev 需在 .env.custom 配置 CURRENT_NODE_NAME"
}

# MODE=dev 的渲染后处理：按组件挂载代码并固定节点；openshift 形态忽略 MODE=dev
apply_dev_code_mounts() { # $@=本次渲染的清单文件名
  is_k8s_cluster || return 0
  [ "${CMD:-}" = "up" ] || return 0
  [ "${DEPLOY_VARS["MODE"]}" = "dev" ] || return 0
  fetch_current_node_name
  local f comp
  for f in "$@"; do
    comp="${f%.yaml}"
    case "$comp" in
      agentserver|gateway)
        mount_claw_code "$CONF_DIR/$f"; mount_runtime_pkg "$CONF_DIR/$f"; mount_core_pkg "$CONF_DIR/$f";;
      web)
        [ "${DEPLOY_VARS["IS_MOUNT_WEB_CODE"]}" = "true" ] || continue
        mount_claw_code "$CONF_DIR/$f"; mount_web_nginx_template "$CONF_DIR/$f";;
      identity|manager-server)
        mount_runtime_code "$CONF_DIR/$f";;
      manager-web)
        [ "${DEPLOY_VARS["IS_MOUNT_MANAGER_WEB_CODE"]}" = "true" ] || continue
        mount_runtime_code "$CONF_DIR/$f"; mount_manager_web_nginx_template "$CONF_DIR/$f";;
      *) continue;;   # mariadb/minio/redis/secret 等基础服务不挂代码
    esac
    # hostPath 是节点本地的，Deployment 固定到代码所在节点（更新运算符写法不丢多文档中的 Service）
    yq -i 'select(.kind == "Deployment").spec.template.spec.nodeName = "'"${DEPLOY_VARS["CURRENT_NODE_NAME"]}"'"' "$CONF_DIR/$f"
    info "dev 模式后处理完成: $comp（节点 ${DEPLOY_VARS["CURRENT_NODE_NAME"]}）"
  done
  return 0
}

# MODE=dev 代码路径校验：须为绝对路径且目录存在
require_abs_dir() { # $1=变量名
  local p="${DEPLOY_VARS[$1]}"
  [[ "$p" = /* ]] || error "$1 须为绝对路径: $p"
  [ -d "$p" ] || error "$1 目录不存在: $p"
}

# MODE 相关校验（check_vars 统一入口调用）
# MODE 为 k8s 形态专属调试开关：openshift 形态及 down 命令直接忽略全部 MODE 设置
check_dev_mode_vars() {
  is_k8s_cluster || return 0
  [ "${CMD:-}" = "up" ] || return 0
  local mode="${DEPLOY_VARS["MODE"]}"
  [[ "$mode" =~ ^(product|dev)$ ]] || error "MODE 非法（product | dev）: $mode"
  [ "$mode" = "dev" ] || return 0

  # --render-only 不做节点自动识别（nodeName 要写进清单），节点名必须显式给出
  if [ "${DEPLOY_VARS["RENDER_ONLY"]}" = "true" ] && [ -z "${DEPLOY_VARS["CURRENT_NODE_NAME"]}" ]; then
    error "MODE=dev + --render-only 需在 .env.custom 显式配置 CURRENT_NODE_NAME"
  fi

  [ -n "${DEPLOY_VARS["CLAW_CODE_PATH"]}" ] && require_abs_dir CLAW_CODE_PATH
  local v
  for v in RUNTIME_CODE_PATH CORE_CODE_PATH; do
    [ -n "${DEPLOY_VARS[$v]}" ] && require_abs_dir "$v"
  done
  return 0
}
