#!/usr/bin/env bash
[ -n "${_DEPLOY_K8S_HANDLER_SH:-}" ] && return 0
_DEPLOY_K8S_HANDLER_SH=1

# 本次运行内已分配的 NodePort（ensure_node_port 进程内去重，防止同批组件撞端口）
_ENSURED_NODE_PORTS=""

# k8s_handler.sh —— 原生 k8s 形态（CLUSTER_TYPE=k8s）专属函数；openshift 形态下均不生效。
# 包含三部分：
#   1. k8s 形态通道实现（_kctl_k8s_*）：kubectl.sh 共享原语的 k8s 分支，按形态分流调用
#   2. manager-web NodePort Service 追加（apply_k8s_extras）
#   3. NFS 直连挂载（resolve_nfs_vars + apply_nfs_direct_mounts）：PVC 卷改写为 NFS 直连挂载；
#      NFS_SERVER_ADDR 空=内置 NFS server（地址解析 + 数据目录自动创建），非空=外部 NFS

# ---------- NFS 直连挂载改写 ----------
# 改写范围（yq 动态修改渲染产物 conf/*.yaml）：mariadb/mysql/postgresql/agentserver/minio → 删 PVC 文档，卷改为 nfs
# redis 纯缓存不挂卷（模板已关闭 AOF），不参与改写
# swarm-pvc.yaml 由 component_files 按形态剔除，k8s 形态不渲染不部署。
# ENABLE_EXTERNAL_NFS=true（外部 NFS）时目录由使用方自行管理

# 在内置 NFS server Pod 内创建组件数据目录（mkdir -p + chmod 777，随机 uid 可写）；
# RENDER_ONLY（不连集群）与外部 NFS 模式跳过
# 定位内置 NFS server Pod。
# 注意：失败用 return 1 + stderr 提示，不能在函数内 error()——命令替换 $(...) 中
# error 的 exit 1 只退出子 shell，报错文本（stdout）会被父 shell 捕获成返回值
builtin_nfs_pod() { # → stdout: Pod 名；失败原因 → stderr；rc 1=未找到
  local pod
  pod=$(kubectl get pods -n "${DEPLOY_VARS["NFS_POD_NAMESPACE"]}" \
    -l "${DEPLOY_VARS["NFS_POD_LABEL"]}" \
    -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
  if [ -z "$pod" ]; then
    echo "未找到内置 NFS server Pod（${DEPLOY_VARS["NFS_POD_NAMESPACE"]}，label ${DEPLOY_VARS["NFS_POD_LABEL"]}）" >&2
    return 1
  fi
  printf '%s' "$pod"
}

# 内置 NFS server 的挂载地址 = Pod 所在节点 IP（数据目录在节点 hostPath 下）
_resolve_builtin_nfs_server() { # → stdout: 节点 IP；失败原因 → stderr；rc 1=失败
  local pod ip
  pod=$(builtin_nfs_pod) || return 1
  ip=$(kubectl get pod "$pod" -n "${DEPLOY_VARS["NFS_POD_NAMESPACE"]}" \
    -o jsonpath='{.status.hostIP}' 2>/dev/null)
  if [ -z "$ip" ]; then
    echo "内置 NFS server Pod ${pod} 尚无 hostIP（可能未就绪/正在重建）" >&2
    return 1
  fi
  printf '%s' "$ip"
}

# 在内置 NFS server Pod 内创建组件数据目录（mkdir -p + chmod 777，随机 uid 可写）；
# 外部 NFS（ENABLE_EXTERNAL_NFS=true）时目录由使用方自行管理，跳过
prepare_nfs_path() { # $1=组件
  [ "${DEPLOY_VARS["ENABLE_EXTERNAL_NFS"]}" = "true" ] && return 0
  local comp="$1"
  # 创建路径 = <NFS_POD_PATH> + <NFS_SHARE_PATH>/<NAMESPACE>/<组件>，与清单里的
  # nfs.path（NFS 伪根相对路径）严格对应——NFS_SHARE_PATH 为子路径时二者才会指向同一目录
  local root="${DEPLOY_VARS["NFS_POD_PATH"]%/}"
  local share="${DEPLOY_VARS["NFS_SHARE_PATH"]%/}"
  local dir="${root}${share}/${DEPLOY_VARS["NAMESPACE"]}/${comp}"
  local pod
  pod=$(builtin_nfs_pod) \
    || error "未找到内置 NFS server Pod，无法创建数据目录 $dir（原因见 stderr 输出）"
  info "创建 NFS 数据目录: ${dir}（Pod $pod）"
  kubectl exec "$pod" -n "${DEPLOY_VARS["NFS_POD_NAMESPACE"]}" \
    -- sh -c "mkdir -p '${dir}' && chmod 777 '${dir}'" \
    || error "NFS 数据目录创建失败: $dir"
}

# NFS 变量校验与内置/外置模式派生（NFS 直连挂载仅 k8s 形态使用）：
# 内置/外置唯一判据为 NFS_SERVER_ADDR 是否为空；设置时须为主机名或 IP，
# 共享根路径须为绝对路径
resolve_nfs_vars() {
  is_k8s_cluster || return 0
  local val="${DEPLOY_VARS["NFS_SERVER_ADDR"]:-}"
  if [ -n "$val" ]; then
    DEPLOY_VARS["ENABLE_EXTERNAL_NFS"]="true"    # 外部 NFS server
  else
    DEPLOY_VARS["ENABLE_EXTERNAL_NFS"]="false"   # 内置 NFS server
  fi
  [ -z "$val" ] || [[ "$val" =~ ^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$ ]] \
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
  # 仅 up 改写数据卷并准备目录：down 只需 conf 里的资源名与集群一致（卷形态不影响按名删除），
  # 且拆除时内置 NFS Pod 可能已先被删——此时解析 NFS 地址不应阻塞卸载
  [ "${CMD:-}" = "up" ] || return 0
  local base="${DEPLOY_VARS["NFS_SHARE_PATH"]}"
  base="${base%/}"
  local ns="${DEPLOY_VARS["NAMESPACE"]}"

  # 模式由 resolve_nfs_vars 派生：NFS_SERVER_ADDR 空=built-in（ENABLE_EXTERNAL_NFS=false，
  # 地址从 Pod hostIP 解析，目录自动创建）；非空=external（ENABLE_EXTERNAL_NFS=true，目录须已存在）
  local external="${DEPLOY_VARS["ENABLE_EXTERNAL_NFS"]}" server=""
  if [ "$external" = "true" ]; then
    server="${DEPLOY_VARS["NFS_SERVER_ADDR"]}"
    info "外部 NFS 模式：$server（数据目录须已存在于 NFS 服务器）"
  elif [ "${DEPLOY_VARS["RENDER_ONLY"]}" = "true" ]; then
    info "内置 NFS 模式 + --render-only：跳过数据卷改写（NFS 地址需连接集群解析），部署请走完整 up"
    return 0
  else
    server=$(_resolve_builtin_nfs_server) \
      || error "内置 NFS server 解析失败（原因见 stderr 输出）"
    [[ "$server" =~ ^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$ ]] \
      || error "解析到的 NFS 地址非法: $server"
    DEPLOY_VARS["NFS_SERVER_ADDR"]="$server"   # 回填解析结果，下游统一取用
    info "内置 NFS 模式：$server（数据目录自动创建）"
  fi

  local -a rewritten=()
  # 改写范围遵循约定：模板 <组件>.yaml，卷名 <组件>-data，数据目录 <base>/<ns>/<组件>；
  # 卷名与模板不符时 _nfs_rewrite_volume 的结果校验会报错拦截
  local cmp
  for cmp in mariadb mysql postgresql minio agentserver; do
    in_array "$cmp.yaml" "$@" || continue
    _nfs_rewrite_file "$CONF_DIR/$cmp.yaml" "${cmp}-data" "$base/$ns/$cmp" "$server"
    info "NFS 直连改写完成: $server:$base/$ns/$cmp"
    rewritten+=("$cmp")
    [ "$external" = "true" ] || prepare_nfs_path "$cmp"   # 外部 NFS 跳过，内置自动创建
  done
  # full 模式：agentserver.json（runtime 建 Pod 的 config_sync 载荷）data 卷同样
  # 改写为 NFS 直连（与 agentserver.yaml 同源路径），目录一并就绪
  if in_array "agentserver.json" "$@"; then
    _nfs_rewrite_json "$CONF_DIR/agentserver.json" "$base/$ns/agentserver" "$server"
    info "NFS 直连改写完成: agentserver.json → $server:$base/$ns/agentserver"
    [ "$external" = "true" ] || prepare_nfs_path agentserver
  fi
  return 0
}

# agentserver.json（full 模式 runtime 建 Pod 的 config_sync 载荷）数据卷 NFS 直连改写：
# data 卷由 PVC 源改写为 NFS 源（server/path），与 agentserver.yaml 的直连约定同源
# （<NFS_SHARE_PATH>/<NAMESPACE>/agentserver）。json 保持 JSON 格式输出（-o=json）
_nfs_rewrite_json() { # $1=json文件 $2=nfs路径 $3=server
  yq -i -o=json '
    (.rawdata.templates[].volumes[]
      | select(.persistentVolumeClaim.claimName == "jiuwenswarm-pvc")) |=
    ({"name": .name,
      "nfs": {"server": "'"${3}"'", "path": "'"${2}"'"}})' "$1"
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
#   .env.custom 已配置 → 校验占用（含本次运行已分配），被占即报错
#   未配置             → 优先复用本 namespace 同名 Service 已持有的端口（复跑稳定），
#                        否则从 NODE_PORT_RANGE_START~END 自动挑选首个空闲端口写入 DEPLOY_VARS
ensure_node_port() { # $1=DEPLOY_VARS 变量名（如 MANAGER_WEB_NODE_PORT）[$2=复用检查的 Service 名，默认 jiuwenswarm-manager-web-nodeport]
  local name="$1"
  local reuse_svc="${2:-jiuwenswarm-manager-web-nodeport}"
  local configured="${DEPLOY_VARS[$name]:-}"
  # 占用集合一次性预取（避免逐端口查询 APIServer）；查询失败按空处理（视为无占用）。
  # 复用检查仅排除「本 namespace 的」$reuse_svc：其他 namespace 同名 Service 占用的
  # nodePort 是真实冲突（nodePort 集群级唯一），必须计入占用
  local local_listens cluster_ports svc_json current_port
  local ns="${DEPLOY_VARS["NAMESPACE"]}"
  local_listens=$(netstat -tuln 2>/dev/null || ss -tuln 2>/dev/null || true)
  svc_json=$(kubectl get svc --all-namespaces -o json 2>/dev/null || true)
  cluster_ports=$(printf '%s' "$svc_json" \
    | yq '.items[] | select(.metadata.name != "'"$reuse_svc"'" or .metadata.namespace != "'"${ns}"'") | .spec.ports[]? | .nodePort | select(. != null)' 2>/dev/null \
    | sort -u || true)
  # 本 namespace 自家 Service 已持有的 nodePort：留空自动分配时优先复用（复跑端口稳定）
  current_port=$(printf '%s' "$svc_json" \
    | yq '.items[] | select(.metadata.name == "'"$reuse_svc"'" and .metadata.namespace == "'"${ns}"'") | .spec.ports[]? | .nodePort | select(. != null)' 2>/dev/null \
    | head -1)
  if [ -n "$configured" ]; then
    if _port_taken "$configured" "$local_listens" "$cluster_ports" \
      || grep -qx "${configured}" <<<"${_ENSURED_NODE_PORTS:-}"; then
      error "${name}=${configured} 已被占用（本机监听 / 集群 Service / 本次运行已分配），请在 .env.custom 更换，或留空以自动分配"
    fi
    _ENSURED_NODE_PORTS+="${configured}"
    _ENSURED_NODE_PORTS+=$'\n'
    info "使用预配置 NodePort: ${name}=${configured}"
    return 0
  fi
  local port
  if [ -n "$current_port" ]; then
    DEPLOY_VARS[$name]="$current_port"
    _ENSURED_NODE_PORTS+="${current_port}"
    _ENSURED_NODE_PORTS+=$'\n'
    info "复用已分配 NodePort: ${name}=$current_port"
    return 0
  fi
  for port in $(seq "${DEPLOY_VARS["NODE_PORT_RANGE_START"]}" "${DEPLOY_VARS["NODE_PORT_RANGE_END"]}"); do
    if ! _port_taken "$port" "$local_listens" "$cluster_ports" \
      && ! grep -qx "${port}" <<<"${_ENSURED_NODE_PORTS:-}"; then
      DEPLOY_VARS[$name]="$port"
      _ENSURED_NODE_PORTS+="${port}"
      _ENSURED_NODE_PORTS+=$'\n'
      info "自动分配 NodePort: ${name}=$port（范围 ${DEPLOY_VARS["NODE_PORT_RANGE_START"]}-${DEPLOY_VARS["NODE_PORT_RANGE_END"]}）"
      return 0
    fi
  done
  error "NodePort 自动分配失败：${DEPLOY_VARS["NODE_PORT_RANGE_START"]}-${DEPLOY_VARS["NODE_PORT_RANGE_END"]} 范围内无空闲端口"
}

# 通用：向清单文件追加单端口 NodePort Service 文档（幂等，按名称查重）
append_nodeport_svc() { # $1=conf文件 $2=Service名 $3=app标签 $4=nodePort $5=端口名 $6=端口号
  local file="$1" name="$2" app="$3" node_port="$4" pname="$5" pnum="$6"
  local ns="${DEPLOY_VARS["NAMESPACE"]}"
  [ -n "$node_port" ] || error "NodePort 未分配（$name，先调用 ensure_node_port）"
  [ -z "$(yq 'select(.kind == "Service" and .metadata.name == "'"$name"'") | .metadata.name' "$file")" ] \
    || { info "NodePort Service $name 已存在，跳过追加"; return 0; }
  yq eval-all -i 'select(fileIndex == 0), select(fileIndex == 1)' "$file" \
    <(yq -n -o=yaml '.apiVersion = "v1" | .kind = "Service" | .metadata.name = "'"$name"'" | .metadata.namespace = "'"$ns"'" | .spec.type = "NodePort" | .spec.selector.app = "'"$app"'" | .spec.ports = [{"name": "'"$pname"'", "port": '"$pnum"', "targetPort": '"$pnum"', "nodePort": '"$node_port"'}]') \
    || error "NodePort Service 追加失败: $name"
  [ -n "$(yq 'select(.kind == "Service" and .metadata.name == "'"$name"'") | .spec.type' "$file")" ] \
    || error "NodePort Service 校验失败: $name"
  info "NodePort Service 已追加: $name（nodePort=$node_port，外部访问 http://<节点IP>:$node_port）"
}

# k8s 形态清单后处理分发（与 apply_nfs_direct_mounts 并列；其余形态 no-op）
apply_k8s_extras() { # $@=本次渲染的清单文件名
  is_k8s_cluster || return 0
  local f
  for f in "$@"; do
    case "$f" in *.yaml) ;; *) continue;; esac   # 非 K8s 清单（agentserver.json）不经 yq 改写
    # k8s 形态无 Route 资源（openshift 独有）：剥离清单中残留的 Route 文档（幂等）
    yq -i 'select(.kind != "Route")' "$CONF_DIR/$f"
    case "$f" in
      manager-web.yaml)
        ensure_node_port MANAGER_WEB_NODE_PORT
        append_nodeport_svc "$CONF_DIR/manager-web.yaml" jiuwenswarm-manager-web-nodeport \
          jiuwenswarm-manager-web "${DEPLOY_VARS["MANAGER_WEB_NODE_PORT"]}" http 5273;;
      minio.yaml)
        ensure_node_port MINIO_API_NODE_PORT minio-api-nodeport
        ensure_node_port MINIO_CONSOLE_NODE_PORT minio-console-nodeport
        append_nodeport_svc "$CONF_DIR/minio.yaml" minio-api-nodeport minio \
          "${DEPLOY_VARS["MINIO_API_NODE_PORT"]}" api 9000
        append_nodeport_svc "$CONF_DIR/minio.yaml" minio-console-nodeport minio \
          "${DEPLOY_VARS["MINIO_CONSOLE_NODE_PORT"]}" console 9001;;
      mariadb.yaml)
        ensure_node_port MARIADB_NODE_PORT mariadb-nodeport
        append_nodeport_svc "$CONF_DIR/mariadb.yaml" mariadb-nodeport mariadb \
          "${DEPLOY_VARS["MARIADB_NODE_PORT"]}" mysql 3306;;
      mysql.yaml)
        ensure_node_port MYSQL_NODE_PORT mysql-nodeport
        append_nodeport_svc "$CONF_DIR/mysql.yaml" mysql-nodeport mysql \
          "${DEPLOY_VARS["MYSQL_NODE_PORT"]}" mysql 3306;;
      postgresql.yaml)
        ensure_node_port POSTGRESQL_NODE_PORT postgresql-nodeport
        append_nodeport_svc "$CONF_DIR/postgresql.yaml" postgresql-nodeport postgresql \
          "${DEPLOY_VARS["POSTGRESQL_NODE_PORT"]}" postgres 5432;;
    esac
  done
}

# ---------- agent-runtime / manager-server 三模式后处理 ----------

# 按部署模式修整渲染产物：
# - runtime + full：注入 Pod 操作 RBAC（SA/Role/RoleBinding 文档前置 +
#   Deployment serviceAccountName），agent-runtime 获得 AgentServer 增删查能力；
#   routing/off 不注入（零 K8s 权限形态）
# - manager-server + 非 routing：剥除 AGENTSERVER_POD_DNS_LIST env 条目
#   （静态注册清单仅 routing 消费；off/full 不需要该环境变量）
apply_runtime_mode_extras() { # $@=本次渲染的清单文件名
  local mode="${DEPLOY_VARS["AGENT_RUNTIME_DEPLOY_MODE"]}" f
  for f in "$@"; do
    case "${f%.yaml}" in
      runtime)
        [ "$mode" = full ] || continue
        [ -f "$CONF_DIR/$f" ] || continue
        {
          echo "---"
          yq -n '{"apiVersion":"v1","kind":"ServiceAccount","metadata":{"name":"jiuwenswarm-agent-runtime-sa","namespace":"'"${DEPLOY_VARS["NAMESPACE"]}"'"}}'
          echo "---"
          yq -n '{"apiVersion":"rbac.authorization.k8s.io/v1","kind":"Role","metadata":{"name":"jiuwenswarm-agent-runtime-pods","namespace":"'"${DEPLOY_VARS["NAMESPACE"]}"'"},"rules":[{"apiGroups":[""],"resources":["pods"],"verbs":["get","list","watch","create","delete"]},{"apiGroups":[""],"resources":["pods/log"],"verbs":["get"]}]}'
          echo "---"
          yq -n '{"apiVersion":"rbac.authorization.k8s.io/v1","kind":"RoleBinding","metadata":{"name":"jiuwenswarm-agent-runtime-pods-binding","namespace":"'"${DEPLOY_VARS["NAMESPACE"]}"'"},"subjects":[{"kind":"ServiceAccount","name":"jiuwenswarm-agent-runtime-sa","namespace":"'"${DEPLOY_VARS["NAMESPACE"]}"'"}],"roleRef":{"apiGroup":"rbac.authorization.k8s.io","kind":"Role","name":"jiuwenswarm-agent-runtime-pods"}}'
          echo "---"
          cat "$CONF_DIR/$f"
        } > "$CONF_DIR/$f.tmp" && mv -f "$CONF_DIR/$f.tmp" "$CONF_DIR/$f"
        yq -i '(select(.kind == "Deployment").spec.template.spec) |= (.serviceAccountName = "jiuwenswarm-agent-runtime-sa")' "$CONF_DIR/$f"
        info "runtime 模式后处理：full → 注入 Pod 操作 RBAC"
        ;;
      manager-server)
        [ "$mode" = routing ] && continue
        [ -f "$CONF_DIR/$f" ] || continue
        yq -i '(select(.kind == "Deployment").spec.template.spec.containers[0].env) |= map(select(.name != "AGENTSERVER_POD_DNS_LIST"))' "$CONF_DIR/$f"
        info "manager-server 模式后处理：$mode → 剥除静态注册清单 env"
        ;;
    esac
  done
}

# ---------- 沙箱类型后处理（SANDBOX_TYPE） ----------

# 按沙箱类型修整渲染产物（SANDBOX_TYPE 语义见 .env.example 与 resolve_sandbox_type_vars）：
# - off：no-op（保持原样）
# - kata：AgentServer 沙箱 Pod 注入 runtimeClassName: kata（Kata 轻量虚机隔离；
#   集群须预装 Kata Containers 并已创建 RuntimeClass kata，否则 Pod 无法启动）：
#   · agentserver.yaml → StatefulSet Pod 模板（off/routing 模式预启动的沙箱 Pod）
#   · agentserver.json → config_sync 载荷 rawdata.templates（full 模式 runtime 按需
#     创建的沙箱 Pod；runtime 侧按模板级白名单解析，未知键暂忽略——消费生效待
#     runtime 打通 runtimeClassName 字段后自然解锁）
# - jiuwenbox：AgentServer Pod 注入 jiuwenbox 沙箱 sidecar 容器（root + SYS_ADMIN/
#   NET_ADMIN + unconfined seccomp + /sys/fs/cgroup hostPath——容器隔离强依赖）。
#   OCP 前置：Pod 的 ServiceAccount 须绑定 anyuid/privileged SCC，否则沙箱起不来
#   · agentserver.yaml → StatefulSet Pod 模板（off/routing 模式预启动的沙箱 Pod）
#   · agentserver.json → config_sync 载荷 rawdata.containers + sidecar_container_ids
#     （full 模式 runtime 按需创建的沙箱 Pod）
apply_sandbox_type_extras() { # $@=本次渲染的清单文件名
  [ "${DEPLOY_VARS["SANDBOX_TYPE"]}" = kata ] || return 0
  local f
  for f in "$@"; do
    case "$f" in
      agentserver.yaml)
        [ -f "$CONF_DIR/$f" ] || continue
        # 更新运算符写法：不匹配的文档（headless/ClusterIP Service）原样保留
        yq -i '(select(.kind == "StatefulSet" and .metadata.name == "jiuwenswarm-agentserver").spec.template.spec) |= (.runtimeClassName = "kata")' "$CONF_DIR/$f"
        [ "$(yq 'select(.kind == "StatefulSet").spec.template.spec.runtimeClassName' "$CONF_DIR/$f")" = "kata" ] \
          || error "runtimeClassName 注入校验失败: $f"
        info "沙箱后处理：agentserver.yaml 注入 runtimeClassName: kata";;
      agentserver.json)
        [ -f "$CONF_DIR/$f" ] || continue
        yq -i -o=json '.rawdata.templates[].runtimeClassName = "kata"' "$CONF_DIR/$f"
        [ "$(yq -r '.rawdata.templates[0].runtimeClassName' "$CONF_DIR/$f")" = "kata" ] \
          || error "runtimeClassName 注入校验失败: $f"
        info "沙箱后处理：agentserver.json 注入 runtimeClassName: kata";;
    esac
  done
}

# jiuwenbox 沙箱 sidecar 注入（SANDBOX_TYPE=jiuwenbox）：
# - agentserver.yaml → StatefulSet 追加 sidecar 容器 + cgroup hostPath 卷；
#   sidecar 与主容器挂载完全一致（工作区根 + run/ 遮蔽），保证与主进程文件级 IPC 同径
# - agentserver.json → config_sync 载荷追加 c-jiuwenbox 容器 + sidecar_container_ids
#   + hp-cgroup 卷（与 ee 部署的 config_sync 载荷同规格）
_apply_jiuwenbox_sidecar_yaml() { # $1=agentserver.yaml
  local img="${DEPLOY_VARS["JIUWENBOX_IMAGE"]}" port="${DEPLOY_VARS["JIUWENBOX_PORT"]:-8321}"
  yq -i '(select(.kind == "StatefulSet").spec.template.spec.containers) |= (. + [{
    "name": "jiuwenbox",
    "image": "'"${img}"'",
    "imagePullPolicy": "IfNotPresent",
    "ports": [{"containerPort": '"${port}"'}],
    "env": [
      {"name": "JIUWENBOX_LISTEN", "value": "http://0.0.0.0:'"${port}"'"},
      {"name": "JIUWENBOX_POLICY_PATH", "value": "/app/jiuwenswarm/jiuwenbox/src/jiuwenbox/configs/enterprise-policy.yaml"},
      {"name": "JIUWENSWARM_DATA_DIR", "value": "/home/app/.jiuwenswarm"},
      {"name": "POD_NAME", "valueFrom": {"fieldRef": {"fieldPath": "metadata.name"}}},
      {"name": "TZ", "value": "Asia/Shanghai"}
    ],
    "securityContext": {"runAsUser": 0, "runAsGroup": 0,
      "capabilities": {"add": ["SYS_ADMIN", "NET_ADMIN"]},
      "seccompProfile": {"type": "Unconfined"}},
    "readinessProbe": {"tcpSocket": {"port": '"${port}"'}, "initialDelaySeconds": 10, "periodSeconds": 5},
    "volumeMounts": [
      {"name": "agentserver-data", "mountPath": "/home/app/.jiuwenswarm"},
      {"name": "agentserver-data", "mountPath": "/home/app/.jiuwenswarm/run", "subPathExpr": "run/$(POD_NAME)"},
      {"name": "hp-cgroup", "mountPath": "/sys/fs/cgroup"}
    ]
  }])' "$1"
  yq -i '(select(.kind == "StatefulSet").spec.template.spec.volumes) |= (. + [{"name": "hp-cgroup", "hostPath": {"path": "/sys/fs/cgroup", "type": "Directory"}}])' "$1"
  # 注入校验：StatefulSet 须有 jiuwenbox 容器 + hp-cgroup 卷
  [ "$(yq 'select(.kind == "StatefulSet").spec.template.spec.containers[] | select(.name == "jiuwenbox") | .name' "$1")" = "jiuwenbox" ]     || error "jiuwenbox sidecar 注入校验失败: $1"
  [ "$(yq 'select(.kind == "StatefulSet").spec.template.spec.volumes[] | select(.name == "hp-cgroup") | .name' "$1")" = "hp-cgroup" ]     || error "hp-cgroup 卷注入校验失败: $1"
}

_apply_jiuwenbox_sidecar_json() { # $1=agentserver.json
  local img="${DEPLOY_VARS["JIUWENBOX_IMAGE"]}" port="${DEPLOY_VARS["JIUWENBOX_PORT"]:-8321}"
  yq -i -o=json '
    .rawdata.containers += [{
      "container_id": "c-jiuwenbox",
      "name": "jiuwenbox",
      "image": "'"${img}"'",
      "imagePullPolicy": "IfNotPresent",
      "ports": [{"containerPort": '"${port}"'}],
      "env": [
        {"name": "JIUWENBOX_LISTEN", "value": "http://0.0.0.0:'"${port}"'"},
        {"name": "JIUWENSWARM_DATA_DIR", "value": "/home/app/.jiuwenswarm"},
        {"name": "TZ", "value": "Asia/Shanghai"}
      ],
      "securityContext": {"runAsUser": 0, "runAsGroup": 0,
        "capabilities": {"add": ["SYS_ADMIN", "NET_ADMIN"]},
        "seccompProfile": {"type": "Unconfined"}},
      "readinessProbe": {"tcpSocket": {"port": '"${port}"'}, "initialDelaySeconds": 10, "periodSeconds": 5},
      "volumeMounts": [
        {"name": "data", "mountPath": "/home/app/.jiuwenswarm"},
        {"name": "hp-cgroup", "mountPath": "/sys/fs/cgroup"}
      ]
    }] |
    .rawdata.templates[].sidecar_container_ids = ["c-jiuwenbox"] |
    .rawdata.templates[].volumes += [{"name": "hp-cgroup", "hostPath": {"path": "/sys/fs/cgroup", "type": "Directory"}}]
  ' "$1"
  # 注入校验：config_sync 载荷须含 c-jiuwenbox 容器 + sidecar_container_ids
  [ "$(yq -r '.rawdata.containers[].container_id' "$1" | grep -c c-jiuwenbox)" -ge 1 ]     || error "jiuwenbox sidecar 注入校验失败: $1"
  [ "$(yq -r '.rawdata.templates[0].sidecar_container_ids[]' "$1" | grep -c c-jiuwenbox)" -ge 1 ]     || error "sidecar_container_ids 注入校验失败: $1"
}

apply_jiuwenbox_extras() { # $@=本次渲染的清单文件名
  [ "${DEPLOY_VARS["SANDBOX_TYPE"]}" = jiuwenbox ] || return 0
  local f
  for f in "$@"; do
    case "$f" in
      agentserver.yaml)
        [ -f "$CONF_DIR/$f" ] || continue
        _apply_jiuwenbox_sidecar_yaml "$CONF_DIR/$f"
        info "沙箱后处理：agentserver.yaml 注入 jiuwenbox sidecar";;
      agentserver.json)
        [ -f "$CONF_DIR/$f" ] || continue
        _apply_jiuwenbox_sidecar_json "$CONF_DIR/$f"
        info "沙箱后处理：agentserver.json 注入 jiuwenbox sidecar";;
    esac
  done
}

# ---------- dev 模式代码挂载（MODE=dev） ----------
# hostPath 把宿主机仓库代码挂载到镜像内代码路径，改代码无需重建镜像；Pod 固定到代码所在节点
# 仅 CLUSTER_TYPE=k8s 支持（openshift SCC 禁止 hostPath）

# 给清单内工作负载（Deployment/StatefulSet，如 agentserver）的首个容器追加一个 hostPath
# 代码挂载（volume + volumeMount）
# 注：select(...).path |= 是更新运算符（非管道 select），yq 对不匹配的文档原样保留，
# 多文档清单（工作负载 + Service）不会丢文档——勿改成 select(...) | .path = 值 的管道形式
add_code_mount() { # $1=清单文件 $2=卷名 $3=宿主机路径 $4=容器内路径 $5=hostPath.type(默认 Directory)
  local file="$1" name="$2" host_path="$3" mount_path="$4" host_type="${5:-Directory}"
  yq -i '
    select(.kind == "Deployment" or .kind == "StatefulSet").spec.template.spec.containers[0].volumeMounts
      |= ((. // []) + [{"name": "'"${name}"'", "mountPath": "'"${mount_path}"'"}])
    | select(.kind == "Deployment" or .kind == "StatefulSet").spec.template.spec.volumes
      |= ((. // []) + [{"name": "'"${name}"'", "hostPath": {"path": "'"${host_path}"'", "type": "'"${host_type}"'"}}])
  ' "$file"
}

mount_swarm_code() { # jiuwenswarm 仓库 → 镜像内代码根
  [ -n "${DEPLOY_VARS["SWARM_CODE_PATH"]:-}" ] || return 0
  add_code_mount "$1" "swarm-code" "${DEPLOY_VARS["SWARM_CODE_PATH"]}" "${DEPLOY_VARS["SWARM_POD_CODE_PATH"]}"
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
    "${DEPLOY_VARS["SWARM_CODE_PATH"]}/docker/web.nginx.conf.template" \
    "/etc/nginx/templates/default.conf.template" "File"
  add_code_mount "$1" "web-optional-upstreams" \
    "${DEPLOY_VARS["SWARM_CODE_PATH"]}/docker/web-optional-upstreams.sh" \
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
  [ -n "${DEPLOY_VARS["CURRENT_NODE_NAME"]:-}" ] && {
    info "CURRENT_NODE_NAME: ${DEPLOY_VARS["CURRENT_NODE_NAME"]}"
    return 0
  }
  local host ip
  host=$(hostname)
  DEPLOY_VARS["CURRENT_NODE_NAME"]=$(kubectl get nodes -o json 2>/dev/null \
    | HOST="$host" yq -r '.items[] | select(.status.addresses[]? | select(.type == "Hostname") | .address == strenv(HOST)) | .metadata.name' 2>/dev/null | head -1)
  [ -n "${DEPLOY_VARS["CURRENT_NODE_NAME"]}" ] && {
    info "CURRENT_NODE_NAME: ${DEPLOY_VARS["CURRENT_NODE_NAME"]}（按 hostname 识别）"
    return 0
  }
  for ip in $(hostname -I 2>/dev/null); do
    DEPLOY_VARS["CURRENT_NODE_NAME"]=$(kubectl get nodes -o json 2>/dev/null \
      | IP="$ip" yq -r '.items[] | select(.status.addresses[]? | select(.type == "InternalIP") | .address == strenv(IP)) | .metadata.name' 2>/dev/null | head -1)
    [ -n "${DEPLOY_VARS["CURRENT_NODE_NAME"]}" ] && {
      info "CURRENT_NODE_NAME: ${DEPLOY_VARS["CURRENT_NODE_NAME"]}（按 IP 识别）"
      return 0
    }
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
        mount_swarm_code "$CONF_DIR/$f"; mount_runtime_pkg "$CONF_DIR/$f"; mount_core_pkg "$CONF_DIR/$f";;
      web)
        [ "${DEPLOY_VARS["IS_MOUNT_WEB_CODE"]}" = "true" ] || continue
        mount_swarm_code "$CONF_DIR/$f"; mount_web_nginx_template "$CONF_DIR/$f";;
      identity|manager-server)
        mount_runtime_code "$CONF_DIR/$f";;
      manager-web)
        [ "${DEPLOY_VARS["IS_MOUNT_MANAGER_WEB_CODE"]}" = "true" ] || continue
        mount_runtime_code "$CONF_DIR/$f"; mount_manager_web_nginx_template "$CONF_DIR/$f";;
      *) continue;;   # mariadb/minio/redis/secret 等基础服务不挂代码
    esac
    # hostPath 是节点本地的，工作负载（Deployment/StatefulSet，如 agentserver）固定到代码所在节点
    # （更新运算符写法不丢多文档中的 Service）
    yq -i 'select(.kind == "Deployment" or .kind == "StatefulSet").spec.template.spec.nodeName = "'"${DEPLOY_VARS["CURRENT_NODE_NAME"]}"'"' "$CONF_DIR/$f"
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
resolve_dev_mode_vars() {
  is_k8s_cluster || return 0
  [ "${CMD:-}" = "up" ] || return 0
  local mode="${DEPLOY_VARS["MODE"]}"
  [[ "$mode" =~ ^(product|dev)$ ]] || error "MODE 非法（product | dev）: $mode"
  [ "$mode" = "dev" ] || return 0

  # --render-only 不做节点自动识别（nodeName 要写进清单），节点名必须显式给出
  if [ "${DEPLOY_VARS["RENDER_ONLY"]}" = "true" ] && [ -z "${DEPLOY_VARS["CURRENT_NODE_NAME"]}" ]; then
    error "MODE=dev + --render-only 需在 .env.custom 显式配置 CURRENT_NODE_NAME"
  fi

  [ -n "${DEPLOY_VARS["SWARM_CODE_PATH"]}" ] && require_abs_dir SWARM_CODE_PATH
  local v
  for v in RUNTIME_CODE_PATH CORE_CODE_PATH; do
    [ -n "${DEPLOY_VARS[$v]}" ] && require_abs_dir "$v"
  done
  return 0
}

# k8s 形态：up 前置——目标 namespace 不存在时自动创建（down 不调用本函数）
# 由统一入口 kctl_ensure_namespace（kubectl.sh）按集群形态分流调用；
# openshift 形态走 ProjectRequest API（_kctl_oc_ensure_namespace，同在 kubectl.sh），不经本函数
# get 退出码非 0 不等于不存在（无 get namespaces 权限/网络故障同样非 0）：
# 以 create 结果为准，仅 AlreadyExists 视为已存在放行，其余错误连同 kubectl stderr 报出
_kctl_k8s_ensure_namespace() { # $1=namespace
  is_k8s_cluster || return 0   # openshift：不探测、不自动创建
  local ns="$1" err code
  [ -n "$ns" ] || return 0
  if kubectl get namespace "$ns" >/dev/null 2>&1; then
    return 0
  fi
  err="$(kctl_tmpdir)/ns-err"
  info "namespace $ns 不存在，自动创建"
  if kubectl create namespace "$ns" >/dev/null 2>"$err"; then
    info "namespace $ns 已创建"
    return 0
  fi
  code=$(_kctl_k8s_err_code "$(cat "$err")")
  [ "$code" = 409 ] && { info "namespace $ns 已存在"; return 0; }
  error "namespace $ns 创建失败 → HTTP $code（若 ns 已存在则是缺 get namespaces 权限所致）: $(cat "$err")"
}
