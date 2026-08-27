# routing_rules 改布尔表达式字符串——条件间任意 and/or 组合

- 日期:2026-08-27
- 涉及模块:session_manager(routing/config_store)/ 测试 / 文档

## 背景与动机

scope 重构(2026-08-26)落地的 `routing_rules` 是结构化两级格式:规则间**固定 OR**、
规则内表达式**固定 AND**(`[{expressions: [{field, op, values}]}]`)。Manager 侧配置
模型演进出「排除若干 group 且(用户白名单**或** bot 白名单)」这类逻辑——两级固定
组合表达不了(AND 与 OR 交错必须靠括号)。需求方确认:**下发格式改为单条布尔表达式
字符串**,条件间任意 and/or + 括号:

```
group_id not in ('57f1…', '704f…') and (user_id in ('admin', 'user1') or bot_id in ('c53…'))
```

旧格式上线不足一日且未在生产落库 → **干净切换,不做双格式兼容**(读路径遇旧结构
list 行按坏行跳过并告警,首发 config_sync 全量下发即覆盖)。

## 方案

- **载体与解析产物分离**:`RoutingScopeDef.expr`(原始字符串)是 wire/DB/快照的
  存储载体——DB `routing_scope.routing_rules` JSON 列直接存标量串,快照「同配置同串」
  确定性不变;`rule` 是解析产物表达式树(`MatchExpression` 叶 / `AndNode` / `OrNode`),
  三个构造方(wire `parse_scope`、快照 `snapshot_from_json`、DB 行 `_scope_from_row`)
  都走同一个 `parse_routing_expr`。
- **文法**:递归下降(`_Parser`:or_expr → and_expr → primary),词法单正则
  (`_TOKEN_RE`)。条件 `field ('not'? 'in') '(' 值列表 ')'`;优先级**条件 > and > or**
  (同 SQL/Python),括号显式分组;关键字 and/or/in/not **大小写不敏感**,字段名固定
  小写枚举;值单引号串(`''` 加倍或 `\'`/`\\` 转义,其他反斜杠组合保留字面);空值列表
  `()` → in 恒假、not_in 恒真(沿用原语义);容忍尾逗号。
- **不支持一元 `not`**(需求只有 and/or;保持文法最小,`not (…)` 明确报错,后续需要
  可加)。
- **防御上限**:表达式长度 ≤ 8000、括号嵌套 ≤ 32(防恶意深嵌递归栈;边界值有测试
  钉住,不是拍脑袋)。
- **通配兜底语义不变**:null / 空串 / 纯空白 = 通配 scope。
- **旧结构化 list → 400**,报错文案带新格式示例;错误统一 InvalidParams(400
  VALIDATION),文案含 scope_id、offset 与原表达式。
- 校验位置不变:仍在 `config_sync` **锁外**(纯 CPU,确定性 400,不占串行锁);
  快照反序列化遇坏表达式 → ValueError → 首次 resolve 从 DB 重建(自愈路径不变)。

## 实现

- `routing.py`:删除 `RoutingRule`/`parse_rule`/`parse_expression`/`VALID_OPS`;
  新增 `AndNode`/`OrNode`/`BoolNode`、`parse_routing_expr`、`_Parser`、`_unquote`、
  `MAX_EXPR_LEN/MAX_EXPR_DEPTH`;`to_payload()` 输出原始串。
- `config_store.py`:`_scope_from_row` 解析字符串表达式(坏行跳过+告警不变)、
  `_upsert_scope` 存原始串。
- e2e 脚本载荷全部改字符串;`e2e_hld_acceptance.py` 的 `e2e-main` scope 故意带
  `or user_id in ('e2e-vip')` 支,新增阶段 2 用例 14b(未知 group + 白名单 user 仍
  命中)——新语义有真环境验收锚点。

## 验证

- 单测:pytest **157 → 191 全绿**。`test_routing.py` 重写:解析/求值 16 → 50 用例,
  覆盖用户示例原样解析、优先级(`a or b and c` = `a or (b and c)`,AST 级断言)、括号
  改变分组、关键字大小写、转义(`\'`/`''`/`\\`)、尾逗号、空列表语义、25 条非法输入
  拒绝矩阵、超长/超深拒绝+边界可解析、快照 roundtrip 保序保串、坏表达式快照 →
  ValueError。`test_config_store.py` 端到端覆盖复合 and/or 表达式经 config_sync →
  resolve 命中/兜底。
- 真环境:集成冒烟 75 项(新增 14b),待下次部署回归。

## 影响面

- 文档:HLD §config_sync 契约(routing_scope 字段表/表达式语法/匹配语义/校验清单)、
  `spec/session-manager.md`、`spec/e2e-test-cases.md`(14b,74→75 项)。
- **Manager 侧联动**:下发方须同步改为表达式字符串(本仓库只做服务端)。
- 无 Redis 键/Lua/接口签名变化;`/debug` 的 scope 视图 `routing_rules` 由 list 变 string。
- 前作 [scope 重构](2026-08-scope-based-routing-config-sync.md) 的「规则 OR/表达式
  AND」格式自此作废。
