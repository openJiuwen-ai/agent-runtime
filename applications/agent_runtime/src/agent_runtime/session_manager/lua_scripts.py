# coding: utf-8
"""Session Manager 的 7 个 Lua 脚本（所有 runtime 状态变更，原子）。

约定：
- 脚本不传 KEYS（键在脚本内由 ``prefix`` 动态拼出；单实例 Redis 无 cluster 限制）；
- ``ARGV[1]`` 恒为键前缀（如 ``session_manager:``）；
- 返回值为扁平字符串数组（真实 client 返回 bytes，由调用方解码）。

脚本清单（语义见 SM 设计 §5.1，逐条对齐）：
- LUA_ROUTE_PLACE        route 原子核心：亲和续期 / 惰性回收 / 闸门 / first-fit / 提交
- LUA_EVICT              session 移除唯一原语（四处同删 + 唤醒等待者）
- LUA_TOUCH              保活续期（惰性 evict 兜底；ttl 就地读 session HASH）
- LUA_SWEEP_IDLE_NOTIFY  空 Pod pass 原子核心：SCARD==0 判定 + NX 去重 + ZREM 退出候选
- LUA_REGISTER_POD       acquire 成功后登记新 Pod（三处注册同写 + 接入序）
- LUA_CLEANUP_POD        notify_pod_dead 清该 (scope,pod) 的全部注册
- LUA_WAITER_GATE        场景 F 等待队列原子入队（ZSET + deadline：先清过期成员
                          再 ZADD 先行 + ZCARD 超限自退；M6 竞态修复 + 崩溃遗留
                          自清，见 SM 设计 §8.2 与 DEPLOY_FOLLOWER_GATE 同款纪律）
"""

from __future__ import annotations

# ARGV: prefix, session_id, scope_id, expiry_ts, session_ttl,
#       scope_concurrency, pod_concurrency, max_pods, now
LUA_ROUTE_PLACE = r"""
local pfx      = ARGV[1]
local sid      = ARGV[2]
local scope    = ARGV[3]
local expiry   = ARGV[4]
local sttl     = ARGV[5]
local scope_cc = tonumber(ARGV[6])
local pod_cc   = tonumber(ARGV[7])
local max_pods = tonumber(ARGV[8])
local now      = tonumber(ARGV[9])

local skey = pfx .. 'session:' .. sid

-- 1. 读现有亲和绑定
local flat = redis.call('HGETALL', skey)
if #flat > 0 then
  local m = {}
  for i = 1, #flat, 2 do m[flat[i]] = flat[i + 1] end
  -- 2. 亲和命中且未过期 → 仅续期，不重抢额度、不换 Pod（场景 A）。
  --    前提：Pod 注册仍在（info 存在）。notify_pod_dead 的清理窗口内新落的
  --    会话若继续 refresh，只会对着已删的 sse_url 无限自旋——且每圈续期
  --    expiry，sweeper 永远收不走。判死绑定 → 惰性回收，走重新放置。
  if m['scope_id'] == scope and tonumber(m['expiry']) > now
     and redis.call('EXISTS', pfx .. 'pod:' .. scope .. ':' .. m['pod_id'] .. ':info') == 1 then
    redis.call('HSET', skey, 'expiry', expiry, 'session_ttl', sttl)
    redis.call('ZADD', pfx .. 'session_expiry', expiry, sid)
    return {'refresh', m['pod_id']}
  end
  -- 3. 已过期 / scope 变化 / Pod 注册已消失 → 惰性回收旧绑定（内联 EVICT；
  --    不触发 idle_consider，空 Pod 回收统一交 sweeper 空 Pod pass）
  local old_scope, old_pod = m['scope_id'], m['pod_id']
  redis.call('SREM', pfx .. 'scope:' .. old_scope .. ':sessions', sid)
  redis.call('SREM', pfx .. 'pod:' .. old_scope .. ':' .. old_pod .. ':sessions', sid)
  redis.call('ZREM', pfx .. 'session_expiry', sid)
  redis.call('DEL', skey)
  redis.call('PUBLISH', pfx .. 'scope:' .. old_scope .. ':free', '1')
end

-- 4. scope 闸门：SCARD 即活跃 chat_session 数
if redis.call('SCARD', pfx .. 'scope:' .. scope .. ':sessions') >= scope_cc then
  return {'scope_full', ''}
end

-- 5. first-fit 按接入序取首个有空位的 Pod
local pods = redis.call('ZRANGE', pfx .. 'scope:' .. scope .. ':pods', 0, -1)
local chosen = ''
for _, pod in ipairs(pods) do
  if redis.call('SCARD', pfx .. 'pod:' .. scope .. ':' .. pod .. ':sessions') < pod_cc then
    chosen = pod
    break
  end
end

-- 6. 现有 Pod 都满：达 max_pods → scope_full；否则 need_acquire（handler 调 RM）
if chosen == '' then
  if #pods >= max_pods then
    return {'scope_full', ''}
  end
  return {'need_acquire', ''}
end

-- 7. 原子提交：同写四处；复用 Pod 时清 idle_notified（空标记失效）
redis.call('SADD', pfx .. 'scope:' .. scope .. ':sessions', sid)
redis.call('SADD', pfx .. 'pod:' .. scope .. ':' .. chosen .. ':sessions', sid)
redis.call('HSET', skey, 'scope_id', scope, 'pod_id', chosen,
           'expiry', expiry, 'session_ttl', sttl)
redis.call('ZADD', pfx .. 'session_expiry', expiry, sid)
redis.call('DEL', pfx .. 'pod:' .. scope .. ':' .. chosen .. ':idle_notified')
return {'placed', chosen}
"""

# Argv: prefix, session_id
LUA_EVICT = r"""
local pfx = ARGV[1]
local sid = ARGV[2]
local skey = pfx .. 'session:' .. sid
local flat = redis.call('HGETALL', skey)
if #flat == 0 then
  return {'noop', '', '', '0'}      -- 已被清理（并发 evict / 双重调用），幂等
end
local m = {}
for i = 1, #flat, 2 do m[flat[i]] = flat[i + 1] end
local scope, pod = m['scope_id'], m['pod_id']

-- 四处同删（不变量 1）
redis.call('SREM', pfx .. 'scope:' .. scope .. ':sessions', sid)
redis.call('SREM', pfx .. 'pod:' .. scope .. ':' .. pod .. ':sessions', sid)
redis.call('ZREM', pfx .. 'session_expiry', sid)
redis.call('DEL', skey)

local remaining = redis.call('SCARD', pfx .. 'pod:' .. scope .. ':' .. pod .. ':sessions')
-- 唤醒该 scope 上因额度满阻塞的 route（不在此触发 idle_consider，交 sweeper）
redis.call('PUBLISH', pfx .. 'scope:' .. scope .. ':free', '1')
return {'evicted', scope, pod, tostring(remaining)}
"""

# Argv: prefix, session_id, now, default_session_ttl
LUA_TOUCH = r"""
local pfx = ARGV[1]
local sid = ARGV[2]
local now = tonumber(ARGV[3])
local def_ttl = tonumber(ARGV[4])
local skey = pfx .. 'session:' .. sid

local flat = redis.call('HGETALL', skey)
if #flat == 0 then
  return {'false', ''}              -- 会话不存在 → gateway 回退重新 route
end
local m = {}
for i = 1, #flat, 2 do m[flat[i]] = flat[i + 1] end

-- 惰性兜底：已过期则当场 evict，不等 sweeper
if tonumber(m['expiry']) <= now then
  redis.call('SREM', pfx .. 'scope:' .. m['scope_id'] .. ':sessions', sid)
  redis.call('SREM', pfx .. 'pod:' .. m['scope_id'] .. ':' .. m['pod_id'] .. ':sessions', sid)
  redis.call('ZREM', pfx .. 'session_expiry', sid)
  redis.call('DEL', skey)
  redis.call('PUBLISH', pfx .. 'scope:' .. m['scope_id'] .. ':free', '1')
  return {'false', ''}
end

-- session_ttl 就地读 session HASH（不依赖 scope:config，避免缓存失效后回退不一致）
local ttl = tonumber(m['session_ttl']) or def_ttl
local new_expiry = now + ttl
redis.call('HSET', skey, 'expiry', tostring(new_expiry))
redis.call('ZADD', pfx .. 'session_expiry', new_expiry, sid)
return {'true', m['pod_id']}
"""

# Argv: prefix, scope_id, pod_id   （idle_notified TTL 固定 60s）
LUA_SWEEP_IDLE_NOTIFY = r"""
local pfx = ARGV[1]
local scope = ARGV[2]
local pod = ARGV[3]

-- 1. 非空 Pod 直接跳过（不通知、不 ZREM）
if redis.call('SCARD', pfx .. 'pod:' .. scope .. ':' .. pod .. ':sessions') ~= 0 then
  return {'false'}
end
-- 2. 60s 去重：同一空 Pod 60s 内只通知一次，过期可重试
if not redis.call('SET', pfx .. 'pod:' .. scope .. ':' .. pod .. ':idle_notified',
                  '1', 'EX', 60, 'NX') then
  return {'false'}
end
-- 3. 原子 ZREM：即刻退出 first-fit 候选（堵 reclaim 窗口内 route 直选，竞态 A）
redis.call('ZREM', pfx .. 'scope:' .. scope .. ':pods', pod)
return {'true'}
"""

# Argv: prefix, scope_id, pod_id, sse_url, deploy_ver
LUA_REGISTER_POD = r"""
local pfx = ARGV[1]
local scope = ARGV[2]
local pod = ARGV[3]
local sse_url = ARGV[4]
local deploy_ver = ARGV[5]

-- 三处注册同写 + 接入序 score（不变量 5：注册是入 scope:pods 的唯一路径）
local seq = redis.call('INCR', pfx .. 'scope:' .. scope .. ':pod_seq')
redis.call('ZADD', pfx .. 'scope:' .. scope .. ':pods', seq, pod)
redis.call('HSET', pfx .. 'pod:' .. scope .. ':' .. pod .. ':info',
           'sse_url', sse_url, 'deploy_ver', deploy_ver)
redis.call('SADD', pfx .. 'pods:registered', scope .. ':' .. pod)
redis.call('SADD', pfx .. 'pods:' .. pod .. ':scopes', scope)
redis.call('DEL', pfx .. 'pod:' .. scope .. ':' .. pod .. ':idle_notified')
return {'ok'}
"""

# Argv: prefix, scope_id, pod_id —— notify_pod_dead 清理该 (scope,pod) 的全部注册
# （会话 evict 由调用方先逐个走 LUA_EVICT，此处只清注册三处 + Pod 键）
LUA_CLEANUP_POD = r"""
local pfx = ARGV[1]
local scope = ARGV[2]
local pod = ARGV[3]
redis.call('ZREM', pfx .. 'scope:' .. scope .. ':pods', pod)
redis.call('DEL', pfx .. 'pod:' .. scope .. ':' .. pod .. ':sessions')
redis.call('DEL', pfx .. 'pod:' .. scope .. ':' .. pod .. ':info')
redis.call('DEL', pfx .. 'pod:' .. scope .. ':' .. pod .. ':idle_notified')
redis.call('SREM', pfx .. 'pods:registered', scope .. ':' .. pod)
redis.call('SREM', pfx .. 'pods:' .. pod .. ':scopes', scope)
return {'ok'}
"""

# Argv: prefix, scope_id, request_id, max_waiters, deadline, now
# 场景 F 有界等待队列原子入队。ZSET 以 deadline（秒级时间戳）为 score：
# 先 ZREMRANGEBYSCORE 清过期成员（等待进程崩溃/断连的兜底，名额不泄漏），
# 再 ZADD 先行 + ZCARD 超限自退（堵「先查后加」并发竞态）。
LUA_WAITER_GATE = r"""
local pfx = ARGV[1]
local scope = ARGV[2]
local waiter = ARGV[3]
local max_waiters = tonumber(ARGV[4])
local deadline = tonumber(ARGV[5])
local now = tonumber(ARGV[6])
local key = pfx .. 'scope:' .. scope .. ':waiters'
redis.call('ZREMRANGEBYSCORE', key, '-inf', now)
redis.call('ZADD', key, deadline, waiter)
if redis.call('ZCARD', key) > max_waiters then
  redis.call('ZREM', key, waiter)
  return {'false'}
end
return {'true'}
"""
