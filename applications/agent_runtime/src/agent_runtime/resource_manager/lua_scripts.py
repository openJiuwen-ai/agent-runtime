# coding: utf-8
"""Resource Manager 的 6 个 Lua 脚本（所有编排态变更，原子）。

约定同 SM：``ARGV[1]`` 恒为键前缀（``{resource_manager}:``，hash tag 使 cluster
下全键域同槽）；返回扁平字符串数组。
脚本清单（语义见 RM 设计 §5.1）：
- LUA_ACQUIRE   取暖 Pod 复用（deploy_ver + generation 过滤）/ 判 max_pods（含 deploying 占位）/ 占位
- LUA_PLACEHOLDER  autoscale 专用占位（判 max_pods + 占位，不碰 idle 池）
- LUA_REGISTER  deploy 成功登记（info / scope:pods / pods:all，清占位；热备入 idle；
                generation 服务端烙印注册时刻 scope 当前代次）
- LUA_RELEASE   idle_consider：转 idle 暖池 + 起 pod_ttl 计时（幂等）
- LUA_PURGE     Pod 死亡 / reclaim 后清全部 RM key（幂等）
- LUA_DEPLOY_FOLLOWER_GATE  deploy 锁输家的等待室原子准入（ZSET+deadline，
  上限 pod_concurrency-1；先清过期成员再 ZADD 先行+超限自退——同
  LUA_WAITER_GATE 纪律，禁止先查后加）

占位（deploying）与等待队列同为 **ZSET + deadline** 语义：score = 占位过期
时间戳，闸门先 ZREMRANGEBYSCORE 清崩溃遗留（进程硬崩时进程内清理不存在，
必须由下一次闸门/周期任务自愈），再 ZADD 先行 + ZCARD 超限自退。
"""

from __future__ import annotations

# Argv: prefix, scope_id, deploy_ver, deploy_token, deadline, now
LUA_ACQUIRE = r"""
local pfx = ARGV[1]
local scope = ARGV[2]
local want_ver = ARGV[3]
local token = ARGV[4]
local deadline = tonumber(ARGV[5])
local now = tonumber(ARGV[6])

local cfg_key = pfx .. 'resource:scope:' .. scope .. ':config'
local max_pods = tonumber(redis.call('HGET', cfg_key, 'max_pods'))
if max_pods == nil then
  return {'no_config', '', ''}
end

local dep_key = pfx .. 'resource:scope:' .. scope .. ':deploying'
redis.call('ZREMRANGEBYSCORE', dep_key, '-inf', now)

-- 1. 取该 scope 暖 Pod 复用；跳过 deploy_ver 或 generation 不匹配的（A 类变更
--    后老版本暖 Pod、config_refresh 后老代次暖 Pod 均由 reclaim 按版本/代次
--    感知回收，不外发给新流量，场景 M / M-R）
local cfg_gen = redis.call('HGET', cfg_key, 'generation') or ''
local idle_key = pfx .. 'resource:scope:' .. scope .. ':idle'
local idle = redis.call('SMEMBERS', idle_key)
for _, pod in ipairs(idle) do
  local info_key = pfx .. 'resource:pod:' .. pod .. ':info'
  local ver = redis.call('HGET', info_key, 'deploy_ver')
  local gen = redis.call('HGET', info_key, 'generation') or ''
  if ver == want_ver and gen == cfg_gen then
    redis.call('SREM', idle_key, pod)
    redis.call('DEL', pfx .. 'resource:pod:' .. pod .. ':idle_since')
    local url = redis.call('HGET', pfx .. 'resource:pod:' .. pod .. ':info', 'pod_sse_url')
    return {'reuse', pod, url}
  end
end

-- 2. 无匹配暖 Pod：判 max_pods（含 deploying 占位，防并发超配）
local total = redis.call('ZCARD', pfx .. 'resource:scope:' .. scope .. ':pods')
           + redis.call('ZCARD', dep_key)
if total >= max_pods then
  return {'max_reached', '', ''}
end

-- 3. 占位 deploy_token（score=deadline：崩溃遗留由下一拍闸门自清）
redis.call('ZADD', dep_key, deadline, token)
return {'need_deploy', '', ''}
"""

# Argv: prefix, scope_id, deploy_token, deadline, now
# autoscale 专用占位：判 max_pods（含 deploying 占位）+ 占位，**不碰 idle 池**
# （LUA_ACQUIRE 的 reuse 分支会把匹配暖 Pod 弹出 idle，热备补位不该消耗暖池）。
LUA_PLACEHOLDER = r"""
local pfx = ARGV[1]
local scope = ARGV[2]
local token = ARGV[3]
local deadline = tonumber(ARGV[4])
local now = tonumber(ARGV[5])
local max_pods = tonumber(redis.call('HGET', pfx .. 'resource:scope:' .. scope .. ':config', 'max_pods'))
if max_pods == nil then
  return {'no_config'}
end
local dep_key = pfx .. 'resource:scope:' .. scope .. ':deploying'
redis.call('ZREMRANGEBYSCORE', dep_key, '-inf', now)
local total = redis.call('ZCARD', pfx .. 'resource:scope:' .. scope .. ':pods')
           + redis.call('ZCARD', dep_key)
if total >= max_pods then
  return {'max_reached'}
end
redis.call('ZADD', dep_key, deadline, token)
return {'need_deploy'}
"""

# Argv: prefix, pod_id, scope_id, pod_sse_url, pod_ip, namespace, deploy_ver,
#       deploy_token, idle_flag(0/1), now, sse_port, health_path
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
local sse_port = ARGV[11]
local health_path = ARGV[12]

-- sse_port/health_path 随 Pod 烘焙记录：A 类变更后 scope 当前配置已换代，
-- 健康探测（场景 N）必须用 Pod 自己的契约参数打它，否则存量老 Pod 会被
-- 探错路径误判半死（违背日落「存量会话不受影响」承诺）
-- generation 服务端烙印（注册时刻 scope:config 的当前代次，config_refresh
-- 的 HINCRBY 日落标记）：注册与 bump 在 Redis 单线程上原子排队——deploy
-- 中途发生刷新时，晚于 bump 注册的 Pod 天然属新代，不会被误日落（刷新不
-- 改配置，该 Pod 的 pod_spec 本就与当前配置一致）
local gen = redis.call('HGET', pfx .. 'resource:scope:' .. scope .. ':config',
                       'generation') or ''
redis.call('HSET', pfx .. 'resource:pod:' .. pod .. ':info',
           'scope_id', scope, 'pod_sse_url', url, 'pod_ip', ip,
           'namespace', ns, 'deploy_ver', ver, 'phase', 'created',
           'created_ts', now, 'sse_port', sse_port, 'health_path', health_path,
           'generation', gen)
redis.call('ZADD', pfx .. 'resource:scope:' .. scope .. ':pods', now, pod)
redis.call('SADD', pfx .. 'resource:pods:all', pod)
redis.call('ZREM', pfx .. 'resource:scope:' .. scope .. ':deploying', token)
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

-- 已 PURGE 的 Pod（info 已清）不得被重放 release 复活成 idle 幽灵成员：
-- reconcile stale 的 view 快照与 PURGE 存在 TOCTOU（先枚举、后被回收、再重放
-- SADD），idle_consider 的 fire-and-forget 同理。幽灵成员会虚增 idle 计数
-- （autoscale 少预热）且永不被回收（idle_since 缺失，reclaim 跳过）。
if redis.call('EXISTS', pfx .. 'resource:pod:' .. pod .. ':info') == 0 then
  return {'false'}
end

-- 幂等：仅在**首次转入** idle 池时起 pod_ttl 计时（SADD 返回 1 = 新转入）。
-- 重复/延迟抵达的 release（reconcile stale 周期重放、idle_consider 去重重发）
-- 不得刷新 idle_since——否则 reclaim 的 aged≥pod_ttl 永不达成，空闲 Pod 永不回收。
-- 被 acquire 弹出（SREM 出 idle）后再转 idle 属新空闲期，重新计时（语义不变）。
if redis.call('SADD', pfx .. 'resource:scope:' .. scope .. ':idle', pod) == 1 then
  redis.call('SET', pfx .. 'resource:pod:' .. pod .. ':idle_since', now)
end
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

# Argv: prefix, scope_id, follower_id, max_followers, deadline, now
# deploy 锁输家（follower）等待室原子准入。ZSET 以 deadline（秒级时间戳）为
# score：先 ZREMRANGEBYSCORE 清过期成员（等待进程崩溃的兜底，不泄漏），
# 再 ZADD 先行 + ZCARD 超限自退（并发同时到达也不会超收）。
LUA_DEPLOY_FOLLOWER_GATE = r"""
local pfx = ARGV[1]
local scope = ARGV[2]
local follower = ARGV[3]
local max = tonumber(ARGV[4])
local deadline = tonumber(ARGV[5])
local now = tonumber(ARGV[6])

local key = pfx .. 'resource:scope:' .. scope .. ':deploy_followers'
redis.call('ZREMRANGEBYSCORE', key, '-inf', now)
redis.call('ZADD', key, deadline, follower)
if redis.call('ZCARD', key) > max then
  redis.call('ZREM', key, follower)
  return {'false'}
end
return {'true'}
"""
