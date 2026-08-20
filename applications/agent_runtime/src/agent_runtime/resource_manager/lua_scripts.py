# coding: utf-8
"""Resource Manager 的 4 个 Lua 脚本（所有编排态变更，原子）。

约定同 SM：``ARGV[1]`` 恒为键前缀（``resource_manager:``）；返回扁平字符串数组。
脚本清单（语义见 RM 设计 §5.1）：
- LUA_ACQUIRE   取暖 Pod 复用（deploy_ver 过滤）/ 判 max_pods（含 deploying 占位）/ 占位
- LUA_REGISTER  deploy 成功登记（info / scope:pods / pods:all，清占位；热备入 idle）
- LUA_RELEASE   idle_consider：转 idle 暖池 + 起 pod_ttl 计时（幂等）
- LUA_PURGE     Pod 死亡 / reclaim 后清全部 RM key（幂等）
"""

from __future__ import annotations

# Argv: prefix, scope_id, deploy_ver, deploy_token
LUA_ACQUIRE = r"""
local pfx = ARGV[1]
local scope = ARGV[2]
local want_ver = ARGV[3]
local token = ARGV[4]

local cfg_key = pfx .. 'resource:scope:' .. scope .. ':config'
local max_pods = tonumber(redis.call('HGET', cfg_key, 'max_pods'))
if max_pods == nil then
  return {'no_config', '', ''}
end

-- 1. 取该 scope 暖 Pod 复用；跳过 deploy_ver 不匹配的（A 类变更后老版本暖 Pod
--    留在 idle 池按 pod_ttl 回收，不外发给新流量，场景 M）
local idle_key = pfx .. 'resource:scope:' .. scope .. ':idle'
local idle = redis.call('SMEMBERS', idle_key)
for _, pod in ipairs(idle) do
  local ver = redis.call('HGET', pfx .. 'resource:pod:' .. pod .. ':info', 'deploy_ver')
  if ver == want_ver then
    redis.call('SREM', idle_key, pod)
    redis.call('DEL', pfx .. 'resource:pod:' .. pod .. ':idle_since')
    local url = redis.call('HGET', pfx .. 'resource:pod:' .. pod .. ':info', 'pod_sse_url')
    return {'reuse', pod, url}
  end
end

-- 2. 无匹配暖 Pod：判 max_pods（含 deploying 占位，防并发超配）
local total = redis.call('ZCARD', pfx .. 'resource:scope:' .. scope .. ':pods')
           + redis.call('SCARD', pfx .. 'resource:scope:' .. scope .. ':deploying')
if total >= max_pods then
  return {'max_reached', '', ''}
end

-- 3. 占位 deploy_token（register / 失败时清）
redis.call('SADD', pfx .. 'resource:scope:' .. scope .. ':deploying', token)
return {'need_deploy', '', ''}
"""

# Argv: prefix, scope_id, deploy_token
# autoscale 专用占位：判 max_pods（含 deploying 占位）+ SADD 占位，**不碰 idle 池**
# （LUA_ACQUIRE 的 reuse 分支会把匹配暖 Pod 弹出 idle，热备补位不该消耗暖池）。
LUA_PLACEHOLDER = r"""
local pfx = ARGV[1]
local scope = ARGV[2]
local token = ARGV[3]
local max_pods = tonumber(redis.call('HGET', pfx .. 'resource:scope:' .. scope .. ':config', 'max_pods'))
if max_pods == nil then
  return {'no_config'}
end
local total = redis.call('ZCARD', pfx .. 'resource:scope:' .. scope .. ':pods')
           + redis.call('SCARD', pfx .. 'resource:scope:' .. scope .. ':deploying')
if total >= max_pods then
  return {'max_reached'}
end
redis.call('SADD', pfx .. 'resource:scope:' .. scope .. ':deploying', token)
return {'need_deploy'}
"""

# Argv: prefix, pod_id, scope_id, pod_sse_url, pod_ip, namespace, deploy_ver,
#       deploy_token, idle_flag(0/1), now
LUA_REGISTER = r"""
local pfx = ARGV[1]
local pod = ARGV[2]
local scope = ARGV[3]
local url = ARGV[4]
local ip = ARGV[5]
local ns = ARGV[6]
local ver = ARGV[7]
local token = ARGV[8]
local idle_flag = ARGV[9]
local now = ARGV[10]

redis.call('HSET', pfx .. 'resource:pod:' .. pod .. ':info',
           'scope_id', scope, 'pod_sse_url', url, 'pod_ip', ip,
           'namespace', ns, 'deploy_ver', ver, 'phase', 'created',
           'created_ts', now)
redis.call('ZADD', pfx .. 'resource:scope:' .. scope .. ':pods', now, pod)
redis.call('SADD', pfx .. 'resource:pods:all', pod)
redis.call('SREM', pfx .. 'resource:scope:' .. scope .. ':deploying', token)
if idle_flag == '1' then
  -- 热备 Pod：入 idle 池（满足不变量 5；reclaim 以 min_idle 底数保护，不起回收计时判定）
  redis.call('SADD', pfx .. 'resource:scope:' .. scope .. ':idle', pod)
  redis.call('SET', pfx .. 'resource:pod:' .. pod .. ':idle_since', now)
end
return {'ok'}
"""

# Argv: prefix, pod_id, scope_id, now
LUA_RELEASE = r"""
local pfx = ARGV[1]
local pod = ARGV[2]
local scope = ARGV[3]
local now = ARGV[4]

-- 幂等：SADD/SET 天然幂等；重复/延迟抵达无副作用
redis.call('SADD', pfx .. 'resource:scope:' .. scope .. ':idle', pod)
redis.call('SET', pfx .. 'resource:pod:' .. pod .. ':idle_since', now)
return {'true'}
"""

# Argv: prefix, pod_id
LUA_PURGE = r"""
local pfx = ARGV[1]
local pod = ARGV[2]
local scope = redis.call('HGET', pfx .. 'resource:pod:' .. pod .. ':info', 'scope_id')

redis.call('DEL', pfx .. 'resource:pod:' .. pod .. ':info')
redis.call('DEL', pfx .. 'resource:pod:' .. pod .. ':idle_since')
redis.call('DEL', pfx .. 'resource:pod:' .. pod .. ':health_fails')
if scope then
  redis.call('ZREM', pfx .. 'resource:scope:' .. scope .. ':pods', pod)
  redis.call('SREM', pfx .. 'resource:scope:' .. scope .. ':idle', pod)
end
redis.call('SREM', pfx .. 'resource:pods:all', pod)
return {'ok', scope or ''}
"""
