# coding: utf-8
"""Session Manager 的 6 个 Lua 脚本（所有 runtime 状态变更，原子）。

约定：
- 脚本不传 KEYS（键在脚本内由 ``prefix`` 动态拼出）；前缀自带 hash tag
  （``{session_manager}:``），Redis Cluster 下整个 SM 键域同槽——脚本访问的
  必是本节点键，多键原子语义在 cluster 分片下依然成立；
- ``ARGV[1]`` 恒为键前缀（如 ``{session_manager}:``）；
- 返回值为扁平字符串数组（真实 client 返回 bytes，由调用方解码）。

脚本清单（语义见 SM 设计 §5.1，逐条对齐）：
- LUA_ROUTE_PLACE        route 原子核心：亲和续期 / rebind 重解 / 闸门 / first-fit / 提交
- LUA_EVICT              session 移除唯一原语（四处同删）
- LUA_REBIND             临时路由 key 原子改绑（session.create 真实 id 回填，见下）
- LUA_TOUCH              保活续期（惰性 evict 兜底；ttl 就地读 session HASH）
- LUA_SWEEP_IDLE_NOTIFY  空 Pod pass 原子核心：SCARD==0 判定 + NX 去重 + ZREM 退出候选
- LUA_REGISTER_POD       acquire 成功后登记新 Pod（三处注册同写 + 接入序）
- LUA_CLEANUP_POD        notify_pod_dead 清该 (scope,pod) 的全部注册

LUA_REBIND 背景：gateway 的 session.create 以临时 key（sess_*/webhttp_*/身份组合串）
route 占槽，AgentServer 返回真实 session id 后，gateway 在把 create 响应交还调用方
之前调用本脚本把槽位原子搬给真实 id——修复双计数（临时 key 残留至 session_ttl
过期）与亲和断裂（真实 id 全新 placement 可能落在别的 Pod，而会话元数据已写在
create 实际服务的 Pod 上）。返回 action：
- noop        from 不存在（已过期被 sweeper 收走 / 已搬过）——幂等
- rubble      残骸自卫（同 LUA_EVICT）
- evicted     to 为空（create 失败驱逐）或绑定 Pod 注册已消失——仅清 from
- overtaken   to 已有自己的绑定（首条 chat.send 抢先放置）——仅清 from，保留 to
- rebound     搬移成功：四处不变量整体从 from 迁至 to，TTL 刷新
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
  -- 2. 残骸自卫（同 LUA_EVICT）：哈希存在但缺 scope_id/pod_id/expiry（外部
  --    直改键的半成品）→ 自清两处后**落穿走全新放置**（亲和信息已不可信，
  --    不 return rubble——下方 tonumber(m['expiry']) 对 nil 的比较是 Lua
  --    runtime error，会让该会话 route 永久 500）。
  if m['scope_id'] == nil or m['pod_id'] == nil or m['expiry'] == nil then
    redis.call('ZREM', pfx .. 'session_expiry', sid)
    redis.call('DEL', skey)
  -- 3. 亲和命中且未过期 → 仅续期，不重抢额度、不换 Pod（场景 A）。
  --    前提：Pod 注册仍在（info 存在）。notify_pod_dead 的清理窗口内新落的
  --    会话若继续 refresh，只会对着已删的 sse_url 无限自旋——且每圈续期
  --    expiry，sweeper 永远收不走。判死绑定 → 惰性回收，走重新放置。
  -- 4. 其余（已过期 / Pod 注册已消失）→ 惰性回收旧绑定（内联 EVICT；不触发
  --    idle_consider，空 Pod 回收统一交 sweeper 空 Pod pass）后返回 rebind，
  --    由 handler 换 first-fit 结果重试——**本调用内不再落放置**。放置只发生
  --    在无绑定的调用里（scope 亲和保持，2026-09-scope-affinity-hold）：
  --    传入 scope 即调用方要维持的绑定 scope（持有续期）或新会话的 first-fit
  --    结果（全新放置）；曾见绑定的调用若直接按传入 scope 放置，会把会话
  --    部署进已被禁用/删除的 scope。
  elseif m['scope_id'] == scope and tonumber(m['expiry']) > now
     and redis.call('EXISTS', pfx .. 'pod:' .. scope .. ':' .. m['pod_id'] .. ':info') == 1 then
    redis.call('HSET', skey, 'expiry', expiry, 'session_ttl', sttl)
    redis.call('ZADD', pfx .. 'session_expiry', expiry, sid)
    return {'refresh', m['pod_id']}
  else
    local old_scope, old_pod = m['scope_id'], m['pod_id']
    redis.call('SREM', pfx .. 'scope:' .. old_scope .. ':sessions', sid)
    redis.call('SREM', pfx .. 'pod:' .. old_scope .. ':' .. old_pod .. ':sessions', sid)
    redis.call('ZREM', pfx .. 'session_expiry', sid)
    redis.call('DEL', skey)
    return {'rebind', ''}
  end
end

-- 5. scope 闸门：SCARD 即活跃 chat_session 数
if redis.call('SCARD', pfx .. 'scope:' .. scope .. ':sessions') >= scope_cc then
  return {'scope_full', ''}
end

-- 6. first-fit 按接入序取首个有空位的 Pod
local pods = redis.call('ZRANGE', pfx .. 'scope:' .. scope .. ':pods', 0, -1)
local chosen = ''
for _, pod in ipairs(pods) do
  if redis.call('SCARD', pfx .. 'pod:' .. scope .. ':' .. pod .. ':sessions') < pod_cc then
    chosen = pod
    break
  end
end

-- 7. 现有 Pod 都满：达 max_pods → scope_full；否则 need_acquire（handler 调 RM）
if chosen == '' then
  if #pods >= max_pods then
    return {'scope_full', ''}
  end
  return {'need_acquire', ''}
end

-- 8. 原子提交：同写四处；复用 Pod 时清 idle_notified（空标记失效）
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

-- 残骸自卫：哈希存在但缺 scope_id/pod_id（外部直改键造出的半成品）→ 只能
-- 自清自身两处（无法定位 scope/pod 集合）。绝不能上抛——单坏键会让到期 pass
-- 每 tick 死在同一 sid 上（崩溃循环 = 会话永不过期、空 Pod 永不转 idle）。
if scope == nil or pod == nil then
  redis.call('ZREM', pfx .. 'session_expiry', sid)
  redis.call('DEL', skey)
  return {'rubble', '', '', '0'}
end

-- 四处同删（不变量 1）
redis.call('SREM', pfx .. 'scope:' .. scope .. ':sessions', sid)
redis.call('SREM', pfx .. 'pod:' .. scope .. ':' .. pod .. ':sessions', sid)
redis.call('ZREM', pfx .. 'session_expiry', sid)
redis.call('DEL', skey)

local remaining = redis.call('SCARD', pfx .. 'pod:' .. scope .. ':' .. pod .. ':sessions')
return {'evicted', scope, pod, tostring(remaining)}
"""

# Argv: prefix, from_session_id, to_session_id（空串=驱逐）, now, default_session_ttl
LUA_REBIND = r"""
local pfx     = ARGV[1]
local from    = ARGV[2]
local to      = ARGV[3]
local now     = tonumber(ARGV[4])
local def_ttl = tonumber(ARGV[5])

local fkey = pfx .. 'session:' .. from

-- 读 from 绑定
local flat = redis.call('HGETALL', fkey)
if #flat == 0 then
  return {'noop', '', '', '0'}        -- 已被 sweeper 收走 / 已搬过，幂等
end
local m = {}
for i = 1, #flat, 2 do m[flat[i]] = flat[i + 1] end

-- 残骸自卫（同 LUA_EVICT）：缺 scope_id/pod_id 的半成品哈希 → 自清两处，
-- 不上抛（上抛会让 create 响应路径对同一坏键永久失败）。
if m['scope_id'] == nil or m['pod_id'] == nil then
  redis.call('ZREM', pfx .. 'session_expiry', from)
  redis.call('DEL', fkey)
  return {'rubble', '', '', '0'}
end

local scope, pod = m['scope_id'], m['pod_id']

-- to 为空 = 驱逐（create 失败，立即释放槽位，不等 TTL）
-- to == from = 无可搬，幂等
if to == '' or to == from then
  if to == '' then
    redis.call('SREM', pfx .. 'scope:' .. scope .. ':sessions', from)
    redis.call('SREM', pfx .. 'pod:' .. scope .. ':' .. pod .. ':sessions', from)
    redis.call('ZREM', pfx .. 'session_expiry', from)
    redis.call('DEL', fkey)
    local remaining = redis.call('SCARD', pfx .. 'pod:' .. scope .. ':' .. pod .. ':sessions')
    return {'evicted', scope, pod, tostring(remaining)}
  end
  return {'noop', scope, pod, '0'}
end

local tkey = pfx .. 'session:' .. to

-- to 已有自己的绑定（首条 chat.send 抢先 route 过）：保留 to，仅清 from。
-- 绑定可能指向别的 Pod——那是已生效的亲和，不得覆盖。
if redis.call('EXISTS', tkey) == 1 then
  redis.call('SREM', pfx .. 'scope:' .. scope .. ':sessions', from)
  redis.call('SREM', pfx .. 'pod:' .. scope .. ':' .. pod .. ':sessions', from)
  redis.call('ZREM', pfx .. 'session_expiry', from)
  redis.call('DEL', fkey)
  local remaining = redis.call('SCARD', pfx .. 'pod:' .. scope .. ':' .. pod .. ':sessions')
  return {'overtaken', scope, pod, tostring(remaining)}
end

-- 绑定 Pod 注册已消失（notify_pod_dead 清理窗口）：亲和信息失效，仅清 from，
-- 真实 id 由后续 chat.send 的 route 全新放置。
if redis.call('EXISTS', pfx .. 'pod:' .. scope .. ':' .. pod .. ':info') == 0 then
  redis.call('SREM', pfx .. 'scope:' .. scope .. ':sessions', from)
  redis.call('SREM', pfx .. 'pod:' .. scope .. ':' .. pod .. ':sessions', from)
  redis.call('ZREM', pfx .. 'session_expiry', from)
  redis.call('DEL', fkey)
  return {'evicted', scope, pod, '0'}
end

-- 搬移：四处不变量整体 from → to，TTL 刷新（含 from 已过期未扫的复活场景——
-- 慢 create（冷启动）下亲和仍应成立；scope/pod 槽位计数不变（一删一加））。
local ttl = tonumber(m['session_ttl']) or def_ttl
local expiry = now + ttl
redis.call('SADD', pfx .. 'scope:' .. scope .. ':sessions', to)
redis.call('SREM', pfx .. 'scope:' .. scope .. ':sessions', from)
redis.call('SADD', pfx .. 'pod:' .. scope .. ':' .. pod .. ':sessions', to)
redis.call('SREM', pfx .. 'pod:' .. scope .. ':' .. pod .. ':sessions', from)
redis.call('HSET', tkey, 'scope_id', scope, 'pod_id', pod,
           'expiry', tostring(expiry), 'session_ttl', tostring(ttl))
redis.call('DEL', fkey)
redis.call('ZREM', pfx .. 'session_expiry', from)
redis.call('ZADD', pfx .. 'session_expiry', expiry, to)
-- 复用 Pod 时清 idle_notified（同 ROUTE_PLACE 提交步；空标记失效）
redis.call('DEL', pfx .. 'pod:' .. scope .. ':' .. pod .. ':idle_notified')
local remaining = redis.call('SCARD', pfx .. 'pod:' .. scope .. ':' .. pod .. ':sessions')
return {'rebound', scope, pod, tostring(remaining)}
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

-- 残骸自卫（同 LUA_EVICT）：缺 scope_id/pod_id/expiry 的半成品哈希（外部直改
-- 键）→ 自清两处并返回不存在（gateway 回退重新 route）。绝不能上抛——下方
-- tonumber(m['expiry']) <= now 对 nil 的比较是 Lua runtime error（EVAL 抛
-- ResponseError），该会话 touch 将永久 500。
if m['scope_id'] == nil or m['pod_id'] == nil or m['expiry'] == nil then
  redis.call('ZREM', pfx .. 'session_expiry', sid)
  redis.call('DEL', skey)
  return {'false', ''}
end

-- 惰性兜底：已过期则当场 evict，不等 sweeper
if tonumber(m['expiry']) <= now then
  redis.call('SREM', pfx .. 'scope:' .. m['scope_id'] .. ':sessions', sid)
  redis.call('SREM', pfx .. 'pod:' .. m['scope_id'] .. ':' .. m['pod_id'] .. ':sessions', sid)
  redis.call('ZREM', pfx .. 'session_expiry', sid)
  redis.call('DEL', skey)
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
