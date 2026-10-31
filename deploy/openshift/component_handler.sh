#!/usr/bin/env bash
[ -n "${_DEPLOY_COMPONENT_HANDLER_SH:-}" ] && return 0
_DEPLOY_COMPONENT_HANDLER_SH=1

# component_handler.sh —— 组件映射表（组件 → 清单/标签/依赖/探测）

# 组件是否可由本脚本部署（清单唯一来源：global_vars.sh 的 ALL_COMPONENTS；
# 本文件其余 case 表为组件属性映射，新增组件仍需逐表补充）
component_managed() {
  in_array "$1" "${ALL_COMPONENTS[@]}"
}

# 组件缺失时是否由 resolve_targets 自动补齐（仅轻量共享依赖；
# 基础服务较重——PVC/建库初始化，缺失走 preflight 报错由人工决策；
# redis 以外挂模式（ENABLE_EXTERNAL_REDIS）为判据：外挂不自动补齐/不参与回收，
# 内置模式 REDIS_HOST 虽由 resolve_redis_vars 派生为非空，仍视为可自动补齐）
component_autoprovision() {
  case "$1" in
    secret) return 0;;
    redis)  [ "${DEPLOY_VARS["ENABLE_EXTERNAL_REDIS"]}" != "true" ] && return 0;;
    *) return 1;;
  esac
}

# 组件 → conf/ 下 yaml 清单（依赖序：envfile/secret 在前，PVC 次之，工作负载最后）
component_files() {
  case "$1" in
    mariadb)        echo "mariadb.yaml";;
    mysql)          echo "mysql.yaml";;
    postgresql)     echo "postgresql.yaml";;
    minio)          echo "minio.yaml";;
    secret)         echo "configmap-secret.yaml";;
    redis)          echo "redis.yaml";;
    runtime)        if [ "${DEPLOY_VARS["AGENT_RUNTIME_DEPLOY_MODE"]}" = full ]; then
                      # full：AgentServer 由 runtime 按需创建——预创建其 env ConfigMap 与
                      # 工作区存储（openshift 形态 PVC；k8s 形态走 NFS 直连，卷在
                      # agentserver.json 内由 apply_nfs_direct_mounts 改写），并渲染
                      # config_sync 载荷（manager-web 导入运行时模板用，无沙箱容器）
                      if is_k8s_cluster; then
                        echo "agentserver-env.configmap.yaml runtime.yaml agentserver.json"
                      else
                        echo "agentserver-env.configmap.yaml swarm-pvc.yaml runtime.yaml agentserver.json"
                      fi
                    else
                      # routing：预启动 AgentServer（StatefulSet）+ config_sync 载荷
                      # （scope/模板契约 + 预建 Pod 注册的模板来源）
                      echo "runtime.yaml agentserver.json"
                    fi;;
    agentserver)    if is_k8s_cluster; then
                      echo "agentserver-env.configmap.yaml agentserver.yaml"
                    else
                      echo "agentserver-env.configmap.yaml swarm-pvc.yaml agentserver.yaml"
                    fi;;
    gateway)        echo "gateway-env.configmap.yaml gateway.yaml";;
    web)            echo "web.yaml";;
    identity)       echo "identity.yaml";;
    manager-server) echo "manager-server.yaml";;
    manager-web)    echo "manager-web.yaml";;
    *)              return 1;;
  esac
}

# 组件 → Pod 就绪探测标签
component_label() {
  case "$1" in
    mariadb)        echo "app=mariadb";;
    mysql)          echo "app=mysql";;
    postgresql)     echo "app=postgresql";;
    minio)          echo "app=minio";;
    redis)          echo "app=jiuwenswarm-redis";;
    runtime)        echo "app=jiuwenswarm-agent-runtime";;
    agentserver)    echo "app=jiuwenswarm-agentserver";;
    gateway)        echo "app=jiuwenswarm-gateway";;
    web)            echo "app=jiuwenswarm-web";;
    identity)       echo "app=jiuwenswarm-identity";;
    manager-server) echo "app=jiuwenswarm-manager-server";;
    manager-web)    echo "app=jiuwenswarm-manager-web";;
  esac
}

# 组件 → Pod 就绪等待策略：secret 非工作负载（apply 成功即就绪）；
# 其余工作负载（含 gateway distributed 多副本）等全部副本 Ready
component_wait_mode() {
  case "$1" in secret) echo "none";; *) echo "all";; esac
}

# 组件 → 运行期硬依赖（up 预检：前置组件不在本次目标里就必须已在集群）
component_deps() {
  case "$1" in
    mariadb)        echo "secret";;
    mysql)          echo "secret";;
    postgresql)     echo "secret";;
    minio)          echo "secret";;
    agentserver)    echo "secret ${DEPLOY_VARS["DB_TYPE"]} minio";;
    gateway)        if [ "${DEPLOY_VARS["AGENT_RUNTIME_DEPLOY_MODE"]}" = full ]; then
                      echo "secret ${DEPLOY_VARS["DB_TYPE"]} redis minio"
                    else
                      echo "secret ${DEPLOY_VARS["DB_TYPE"]} redis minio agentserver"
                    fi;;
    web)            echo "secret minio";;
    identity)       echo "secret ${DEPLOY_VARS["DB_TYPE"]}";;
    manager-server) echo "secret ${DEPLOY_VARS["DB_TYPE"]} redis";;
    runtime)        echo "secret ${DEPLOY_VARS["DB_TYPE"]} redis";;
  esac
}

# 组件 → 存在性探测所在命名空间：
#   基础服务（mariadb/mysql/postgresql/minio）：
#     本次部署目标内 → KCTL_NS（实例将由本次部署落到本 ns，跳过探测）
#     否则按内置实例定位：本 ns → default，均未命中兜底 BASE_SVC_NS；
#     探测结果缓存 _SVC_NS_<svc>，进程内只探测一次
#   其余组件 → 本命名空间（KCTL_NS）
component_ns() {
  case "$1" in
    mariadb|mysql|postgresql|minio)
      if in_array "$1" "${TARGETS[@]:-}"; then
        echo "$KCTL_NS"
        return 0
      fi
      local cache_key="_SVC_NS_$1" ns
      ns="${DEPLOY_VARS[$cache_key]:-}"
      if [ -z "$ns" ]; then
        ns=$(_resolve_builtin_svc_ns "$1")
        DEPLOY_VARS[$cache_key]="$ns"
      fi
      echo "$ns";;
    *)                              echo "$KCTL_NS";;
  esac
}

# 内置基础服务实例定位：本 ns 命中优先，其次 default 共享实例；均未命中输出
# BASE_SVC_NS（部署预期位置：k8s=default / openshift=本 ns）。
# 探测需集群通道（kctl_check_vars 之后可用）；探测失败视同未命中，走兜底
_resolve_builtin_svc_ns() { # $1=svc（mariadb|mysql|postgresql|minio）→ stdout: ns
  local svc="$1" p code
  if ! p=$(kctl_kind_path StatefulSet "$KCTL_NS"); then
    echo "${DEPLOY_VARS["BASE_SVC_NS"]}"
    return 0
  fi
  code=$(kctl_http GET "$p/$svc" /dev/null)
  if [ "$code" = 200 ]; then
    echo "$KCTL_NS"
    return 0
  fi
  if [ "$KCTL_NS" != "default" ]; then
    p=$(kctl_kind_path StatefulSet "default")
    code=$(kctl_http GET "$p/$svc" /dev/null)
    if [ "$code" = 200 ]; then
      echo "default"
      return 0
    fi
  fi
  echo "${DEPLOY_VARS["BASE_SVC_NS"]}"
}

# 组件 → 存在性探测用的 kind/name（依赖预检与卸载存活检查共用，须覆盖全部组件）。
# kind 与本工具模板一致：基础服务均为 StatefulSet（minio 模板已从 Deployment 改造），
# agentserver 为 StatefulSet（稳定 DNS + 每 Pod workspace 隔离）；
# redis/gateway/web 等其余产品组件为 Deployment；k8s 形态共享实例（default）亦为同款形态
component_probe() {
  case "$1" in
    secret)         echo "Secret/jiuwenswarm-secret-configmap";;
    mariadb)        echo "StatefulSet/mariadb";;
    mysql)          echo "StatefulSet/mysql";;
    postgresql)     echo "StatefulSet/postgresql";;
    minio)          echo "StatefulSet/minio";;
    redis)          echo "Deployment/jiuwenswarm-redis";;
    runtime)        echo "Deployment/jiuwenswarm-agent-runtime";;
    agentserver)    echo "StatefulSet/jiuwenswarm-agentserver";;
    gateway)        echo "Deployment/jiuwenswarm-gateway";;
    web)            echo "Deployment/jiuwenswarm-web";;
    identity)       echo "Deployment/jiuwenswarm-identity";;
    manager-server) echo "Deployment/jiuwenswarm-manager-server";;
    manager-web)    echo "Deployment/jiuwenswarm-manager-web";;
    *)              return 1;;
  esac
}

# 缺依赖时的操作提示
dep_hint() {
  case "$1" in
    secret) echo "随 ./deploy.sh up 自动拉起（各组件共用的配置 Secret）";;
    redis)  echo "随 ./deploy.sh up 自动拉起";;
    mariadb|mysql|postgresql|minio)
      if is_k8s_cluster; then
        echo "内置实例消费顺序: 本 ns → default；缺失可在本 ns 执行 ./deploy.sh up $1"
      else
        echo "先执行 ./deploy.sh up $1（部署为本 ns 实例，消费时优先于 default）"
      fi;;
    *)      echo "先执行 ./deploy.sh up $1";;
  esac
}
