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
# redis 在外挂模式（REDIS_HOST 已配置）下不自动补齐）
component_autoprovision() {
  case "$1" in
    secret) return 0;;
    redis)  [ -z "${DEPLOY_VARS["REDIS_HOST"]:-}" ] && return 0;;
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
    runtime)        echo "runtime.yaml";;
    agentserver)    if is_k8s_cluster; then
                      echo "agentserver-env.configmap.yaml agentserver.yaml"
                    else
                      echo "agentserver-env.configmap.yaml claw-pvc.yaml agentserver.yaml"
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
    redis)          echo "app=redis";;
    runtime)        echo "app=jiuwenclaw-agent-runtime";;
    agentserver)    echo "app=jiuwenclaw-agentserver";;
    gateway)        echo "app=jiuwenclaw-gateway";;
    web)            echo "app=jiuwenclaw-web";;
    identity)       echo "app=jiuwenclaw-identity";;
    manager-server) echo "app=jiuwenclaw-manager-server";;
    manager-web)    echo "app=jiuwenclaw-manager-web";;
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
#   基础服务（mariadb/mysql/postgresql/minio）→ BASE_SVC_NS
#   （k8s 形态=default 集群共享实例；openshift 形态=本 ns，两者同值，行为不变）
#   其余组件 → 本命名空间（KCTL_NS）
component_ns() {
  case "$1" in
    mariadb|mysql|postgresql|minio) echo "${DEPLOY_VARS["BASE_SVC_NS"]:-$KCTL_NS}";;
    *)                              echo "$KCTL_NS";;
  esac
}

# 组件 → 存在性探测用的 kind/name（依赖预检与卸载存活检查共用，须覆盖全部组件）。
# kind 与本工具模板一致：基础服务均为 StatefulSet（minio 模板已从 Deployment 改造），
# agentserver 为 StatefulSet（稳定 DNS + 每 Pod workspace 隔离）；
# redis/gateway/web 等其余产品组件为 Deployment；k8s 形态共享实例（default）亦为同款形态
component_probe() {
  case "$1" in
    secret)         echo "Secret/jiuwenclaw-secret-configmap";;
    mariadb)        echo "StatefulSet/mariadb";;
    mysql)          echo "StatefulSet/mysql";;
    postgresql)     echo "StatefulSet/postgresql";;
    minio)          echo "StatefulSet/minio";;
    redis)          echo "Deployment/redis";;
    runtime)        echo "Deployment/jiuwenclaw-agent-runtime";;
    agentserver)    echo "StatefulSet/jiuwenclaw-agentserver";;
    gateway)        echo "Deployment/jiuwenclaw-gateway";;
    web)            echo "Deployment/jiuwenclaw-web";;
    identity)       echo "Deployment/jiuwenclaw-identity";;
    manager-server) echo "Deployment/jiuwenclaw-manager-server";;
    manager-web)    echo "Deployment/jiuwenclaw-manager-web";;
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
        echo "k8s 形态复用 default 命名空间的共享实例（本次探测 ns: ${DEPLOY_VARS["BASE_SVC_NS"]:-default}），缺失请在该命名空间先行部署或联系管理员"
      else
        echo "先执行 ./deploy.sh up $1"
      fi;;
    *)      echo "先执行 ./deploy.sh up $1";;
  esac
}
