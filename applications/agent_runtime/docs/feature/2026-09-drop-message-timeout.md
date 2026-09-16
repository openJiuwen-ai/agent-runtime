# 删除模板字段 message_timeout(全链路死参数)

- 日期:2026-09-16
- 涉及模块:session_manager / 测试 / 文档

## 背景与动机

`message_timeout`(int 秒,默认 600)自 M 期起存在于模板契约,文档语义写的是「数据面 SSE 读写超时(gateway 侧使用)」。2026-09-16 联调排查(王明琦实测)确认它是**全链路死参数**:

- **Runtime 只收不吐**:config_sync 接收 → int 校验 → 落库 `service_config_template.message_timeout`,到此为止。它不在 `Template.pool_config()`(RM 只收 min_idle_pods/max_pods/pod_ttl/pod_concurrency)、不在 route 响应(只有 `{pod_sse_url, pod_id}`)、不在任何 Redis 键、不在诊断接口 `list_templates`——**没有任何出口把值送达 Gateway**。
- **连 B 类判定都不进**:不在 `spec_fields.POLICY_FIELDS` 也不在 `DEPLOY_VER_FIELDS`,单独改它 `_diff_class` 返回 `"none"`,连 `affected_scopes` 都不列入;config-plane-api.md 把它写进 B 类「RM 池参数重推」属文档失真。
- **实测佐证**:分别配 10s/90s,经 Gateway 跑 ~40s 真实慢工具调用均完成、无超时、客户端数据间隔不受影响——与「无人消费」自洽。数据面 SSE 超时实为 Gateway 自身配置。

不删的代价:契约撒谎(配置方以为配了会生效)、DB 死列、文档失真误导排障(本次排查本身就被它带偏了一轮)。

## 方案(与需求方确认过)

- **硬删**:wire 契约(`TEMPLATE_LEVEL_FIELDS` 白名单)、DB 列(TABLE_DEF)、`Template` 字段、文档(HLD 模板表 + 参数表、config-plane-api.md B 类清单 + 参数表、session-manager-design.md 关键列)。
- **静默忽略而非 400**:载荷残留 `message_timeout` 键按既有未知键语义丢弃(与 `max_pods` 同款),老发送方在 Manager 侧同步删字段前继续发送不炸;`_LEGACY_INLINE_CONTAINER_KEYS` 400 黑名单**不加**它(那是内联容器键专用)。
- **HLD §数据面超时契约改写**:超时参数为 **gateway 自身配置**,与本服务模板契约无关;「超时/断流必须给用户明确错误、自愈 = 重新 route」的行为契约不变,只是不再由本服务代言超时数值。
- 被否备选:**runtime 在 route 响应里透出该值给 Gateway**——否决,理由:Gateway 若需要该参数应走自身配置管理;runtime 造透传通道等于给死参数续命,且 route 响应契约要动。

## 实现

- `session_manager/config_store.py`:删 `ColumnDefinition("message_timeout", ...)`、`_COLUMN_OF` 条目、`_INT_FIELDS`、`TEMPLATE_LEVEL_FIELDS` 四处。
- `session_manager/models.py`:删 `Template.message_timeout` 字段。
- 测试:`tests/conftest.py` 三段式转换器键过滤、`test_config_store.py` 幽灵容器直插行删该列;新增 `test_config_sync_message_timeout_dropped_silently`(载荷残留键 → sync ok 不变、`Template` 无该属性;`ok=True` 同时证明写路径无该列——SQLite 按新 TABLE_DEF 建表,残留写列即 INSERT 失败)。

## 验证

- 单测:`uv run pytest` **535 passed, 9 skipped**(新增 1 用例)。
- 载荷兼容:老发送方携带 `message_timeout` 的 config_sync 不 400(单测固化)。

## 存量库升级(先发版后 DROP;框架不 DROP)

部署顺序:**先发本版本再执行 DROP**——新代码不再写该列,列若仍在(NOT NULL DEFAULT 600)则 INSERT 缺列走默认值,兼容;反之先 DROP 老代码还在跑会 INSERT 报缺列。

```sql
-- MySQL(无 DROP COLUMN IF EXISTS;重复执行报 1091,忽略即可)
ALTER TABLE service_config_template DROP COLUMN message_timeout;
-- PostgreSQL
ALTER TABLE service_config_template DROP COLUMN IF EXISTS message_timeout;
```

e2e/联调库(mysql-headless `runtime_wmq`)同款执行。Manager/外层 jiuwenclaw 侧若模板下发仍有该字段,属其自身变更;本服务静默忽略,不阻塞联调。

## 影响面

- 文档:HLD(模板字段表、§数据面超时契约——保留删除指针)、config-plane-api.md(B 类清单、templates 参数表)、session-manager-design.md(关键列清单)。
- spec 文档无涉及(`docs/spec/` 未提及该字段)。
- 遗留:无。数据面 SSE 超时的实际生效路径归 Gateway 自身配置,后续排障方向不再指向本服务。
