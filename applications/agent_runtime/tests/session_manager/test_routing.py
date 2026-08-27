# coding: utf-8
"""routing.py 纯函数测试:表达式解析与求值 / wire 校验 / 快照 roundtrip / first-fit 匹配。"""

from __future__ import annotations

import pytest

from agent_runtime.errors import InvalidParams
from agent_runtime.session_manager.models import Template
from agent_runtime.session_manager.routing import (
    MAX_EXPR_DEPTH,
    MAX_EXPR_LEN,
    AndNode,
    MatchExpression,
    OrNode,
    RoutingScopeDef,
    build_snapshot,
    has_wildcard_scope,
    match_scope,
    parse_routing_expr,
    parse_scope,
    snapshot_from_json,
    snapshot_to_json,
    template_from_json,
    template_to_json,
)


def _tpl(template_id: str, **overrides) -> Template:
    return Template(template_id=template_id, **overrides)


def _scope(scope_id: str, index: int, template_id: str = "tpl",
           expr: str = "") -> RoutingScopeDef:
    """expr → RoutingScopeDef(与生产构造路径一致:解析串并同时保存原串)。"""
    return RoutingScopeDef(
        scope_id=scope_id, index=index, template_id=template_id, expr=expr,
        rule=parse_routing_expr(expr) if expr.strip() else None,
    )


def _attrs(user_id: str = "u1", group_id: str = "g1", bot_id: str = "b1") -> dict:
    return {"user_id": user_id, "group_id": group_id, "bot_id": bot_id}


# -------------------------------------------------------------- 条件叶子求值

def test_expression_in_and_not_in():
    expr = MatchExpression("user_id", "in", frozenset({"u1", "u2"}))
    assert expr.matches(_attrs())
    assert not MatchExpression("user_id", "in", frozenset({"u2"})).matches(_attrs())
    assert MatchExpression("group_id", "not_in", frozenset({"g2"})).matches(_attrs())
    assert not MatchExpression("bot_id", "not_in", frozenset({"b1"})).matches(_attrs())


def test_expression_empty_values():
    """空 values():in 恒假、not_in 恒真。"""
    assert not parse_routing_expr("user_id in ()").matches(_attrs(user_id="u1"))
    assert parse_routing_expr("user_id not in ()").matches(_attrs(user_id="u1"))


def test_missing_field_evaluates_as_empty_string():
    """attrs 缺字段 → ""(求值层防御)。"""
    assert parse_routing_expr("user_id in ('')").matches({})
    assert not parse_routing_expr("user_id in ('u1')").matches({})


# -------------------------------------------------------------- 表达式解析与求值

def test_parse_real_world_expression():
    """下发示例原样解析:and + 括号内 or + not in。"""
    expr = (
        "group_id not in ('57f108dd-2bba-4f4e-aa50-120fb1bb1414', "
        "'704f0800-f23c-4cd6-8e7e-bf6007e34fd9') "
        "and (user_id in ('admin', 'user1') or bot_id in ('c53cef2f-...'))"
    )
    tree = parse_routing_expr(expr)
    assert tree.matches(_attrs(user_id="admin", group_id="g-other", bot_id="x"))
    assert tree.matches(_attrs(user_id="nobody", group_id="g-other",
                               bot_id="c53cef2f-..."))          # 括号内 or 的 bot 分支
    assert not tree.matches(_attrs(user_id="nobody", group_id="g-other", bot_id="x"))
    assert not tree.matches(_attrs(user_id="admin",
                                   group_id="57f108dd-2bba-4f4e-aa50-120fb1bb1414"))


def test_precedence_and_binds_tighter_than_or():
    """`a or b and c` = `a or (b and c)`。"""
    tree = parse_routing_expr("user_id in ('a') or user_id in ('b') and group_id in ('g')")
    assert tree == OrNode(children=(
        MatchExpression("user_id", "in", frozenset({"a"})),
        AndNode(children=(
            MatchExpression("user_id", "in", frozenset({"b"})),
            MatchExpression("group_id", "in", frozenset({"g"})),
        )),
    ))
    assert tree.matches(_attrs(user_id="a", group_id="x"))      # 单 a 即真
    assert not tree.matches(_attrs(user_id="b", group_id="x"))  # b 需 and g


def test_parens_override_precedence():
    """`(a or b) and c` ≠ `a or (b and c)`。"""
    tree = parse_routing_expr("(user_id in ('a') or user_id in ('b')) and group_id in ('g')")
    assert not tree.matches(_attrs(user_id="a", group_id="x"))
    assert tree.matches(_attrs(user_id="b", group_id="g"))


def test_and_chain_and_or_chain():
    ands = parse_routing_expr(
        "user_id in ('u1') and group_id not in ('bad') and bot_id in ('b1')"
    )
    assert isinstance(ands, AndNode) and len(ands.children) == 3
    assert ands.matches(_attrs())
    assert not ands.matches(_attrs(group_id="bad"))
    ors = parse_routing_expr("user_id in ('a') or user_id in ('b') or bot_id in ('c')")
    assert isinstance(ors, OrNode) and len(ors.children) == 3
    assert ors.matches(_attrs(bot_id="c"))


def test_single_condition_is_bare_leaf():
    assert parse_routing_expr("user_id in ('u1')") == MatchExpression(
        "user_id", "in", frozenset({"u1"})
    )
    assert parse_routing_expr("((user_id in ('u1')))") == MatchExpression(
        "user_id", "in", frozenset({"u1"})
    )   # 冗余括号不产生分支节点


def test_keywords_case_insensitive_and_flexible_whitespace():
    """关键字 and/or/in/not 大小写不敏感、空白随意;字段名是固定枚举,须小写。"""
    tree = parse_routing_expr("user_id\tIN\n('u1')  AND\tbot_id NOT IN('b2')")
    assert tree.matches(_attrs())
    assert not tree.matches(_attrs(bot_id="b2"))
    with pytest.raises(InvalidParams, match="unknown field 'USER_ID'"):
        parse_routing_expr("USER_ID IN ('u1')")


def test_value_quoting_and_escapes():
    assert parse_routing_expr(r"user_id in ('a\'b')").matches(_attrs(user_id="a'b"))
    assert parse_routing_expr(r"user_id in ('c''d')").matches(_attrs(user_id="c'd"))
    assert parse_routing_expr(r"user_id in ('e\\f')").matches(_attrs(user_id="e\\f"))
    assert parse_routing_expr("user_id in (' spaced ',)").matches(_attrs(user_id=" spaced "))  # 尾逗号
    assert parse_routing_expr("user_id in ('u1','u2')").matches(_attrs(user_id="u2"))          # 无空格


def test_scope_matches_wildcard_and_none_user():
    """空表达式 = 通配;user_id None → ""。"""
    assert _scope("fallback", 100).matches(None, "", "")
    scoped = _scope("s", 0, expr="user_id in ('')")
    assert scoped.matches(None, "g", "b")
    assert not scoped.matches("u1", "g", "b")


# -------------------------------------------------------------- 解析拒绝矩阵

@pytest.mark.parametrize("bad", [
    "",                                     # 空串(应由 parse_scope 层作通配,直接解析拒绝)
    "   ",
    "user_id in ('a'",                      # 括号未闭合
    "(user_id in ('a')",                    # 外括号未闭合
    "user_id in ('a'))",                    # 多余右括号
    "user_id in ('a') bot_id in ('b')",     # 缺 and/or(不支持隐式 AND)
    "user_id in ('a') and",                 # 悬空 and
    "and user_id in ('a')",
    "or",
    "not (user_id in ('a'))",               # 一元 not 不支持(仅 not in)
    "user_id and in ('a')",
    "user_id in ('a') or not bot_id in ('b')",  # not 只能紧跟字段
    "chat_id in ('a')",                     # 未知字段
    "user_id nin ('a')",
    "user_id == 'a'",
    "user_id in (a)",                       # 值必须单引号
    'user_id in ("a")',                     # 双引号非法
    "user_id in ('a' 'b')",                 # 缺逗号
    "user_id in ('a',, 'b')",
    "user_id in 'a'",                       # 值列表必须带括号
    "'u1' in ('u1')",                       # 字符串不能当字段
    "in ('a')",
    "user_id in () and",                    # 空列表本身合法,悬空 and 非法
    "user_id  in ('a') or or bot_id in ('b')",
])
def test_parse_routing_expr_rejects(bad):
    with pytest.raises(InvalidParams):
        parse_routing_expr(bad)


def test_parse_routing_expr_rejects_runaway_input():
    with pytest.raises(InvalidParams):   # 超长
        parse_routing_expr("user_id in ('a') and " * 1000 + "user_id in ('b')")
    with pytest.raises(InvalidParams):   # 括号嵌套超限
        parse_routing_expr("(" * (MAX_EXPR_DEPTH + 1) + "user_id in ('a')" + ")" * (MAX_EXPR_DEPTH + 1))
    # 边界内必须可解析(不是拍脑袋上限)
    ok = "(" * MAX_EXPR_DEPTH + "user_id in ('a')" + ")" * MAX_EXPR_DEPTH
    assert parse_routing_expr(ok) is not None
    ok_len = "or ".join(["user_id in ('a')"] * (MAX_EXPR_LEN // 20))
    assert len(ok_len) < MAX_EXPR_LEN and parse_routing_expr(ok_len) is not None


def test_error_messages_carry_expr_context():
    with pytest.raises(InvalidParams, match="unknown field 'chat_id'"):
        parse_routing_expr("chat_id in ('a')")
    with pytest.raises(InvalidParams, match=r"expr="):
        parse_routing_expr("user_id in (")


# -------------------------------------------------------------- 匹配(first-fit)

def test_match_scope_first_fit_by_index_then_scope_id():
    """index 升序;并列 index 按 scope_id 字典序;首个命中即止。"""
    low = _scope("z-low", 0, "tpl-a")                        # 通配,index 最小 → 恒先命中
    high = _scope("a-exact", 10, "tpl-b", expr="group_id in ('g1')")
    tie_a = _scope("a-tie", 5, "tpl-a", expr="user_id in ('u1')")
    tie_b = _scope("b-tie", 5, "tpl-b", expr="user_id in ('u1')")
    snap = build_snapshot([high, tie_b, low, tie_a], [_tpl("tpl-a"), _tpl("tpl-b")], 1)
    assert match_scope(snap, "any", "any", "any").scope_id == "z-low"
    snap2 = build_snapshot([high, tie_b, tie_a], [_tpl("tpl-a"), _tpl("tpl-b")], 2)
    assert match_scope(snap2, "u1", "g", "b").scope_id == "a-tie"


def test_match_scope_skips_disabled_and_missing_template():
    """模板缺失/禁用的 scope 不命中,继续落下一个。"""
    disabled = _scope("s-off", 0, "tpl-off")
    missing = _scope("s-miss", 1, "tpl-none")
    fallback = _scope("s-ok", 2, "tpl-ok")
    snap = build_snapshot(
        [disabled, missing, fallback],
        [_tpl("tpl-off", enabled=False), _tpl("tpl-ok")],
        1,
    )
    hit = match_scope(snap, "u", "g", "b")
    assert hit is not None and hit.scope_id == "s-ok"


def test_match_scope_no_match_returns_none():
    scoped = _scope("s", 0, expr="group_id in ('g1')")
    snap = build_snapshot([scoped], [_tpl("tpl")], 1)
    assert match_scope(snap, "u", "g2", "b") is None


# -------------------------------------------------------------- wire 校验(parse_scope)

def test_parse_scope_valid_and_invalid_scope_id():
    tids = {"tpl"}
    ok = parse_scope({"scope_id": "vip.group-1", "index": 3,
                      "template_id": "tpl", "routing_rules": ""}, tids)
    assert ok.scope_id == "vip.group-1" and ok.index == 3 and ok.rule is None
    for bad in ("", "a:b", "a b", "a*b", "中文", "x" * 129, None):
        with pytest.raises(InvalidParams):
            parse_scope({"scope_id": bad, "index": 0, "template_id": "tpl"}, tids)


def test_parse_scope_rejects_bad_index_and_unknown_template():
    tids = {"tpl"}
    with pytest.raises(InvalidParams):   # index 缺失
        parse_scope({"scope_id": "s", "template_id": "tpl"}, tids)
    with pytest.raises(InvalidParams):   # index 是 bool(bool 是 int 子类,显式排除)
        parse_scope({"scope_id": "s", "index": True, "template_id": "tpl"}, tids)
    with pytest.raises(InvalidParams):   # index 非整数
        parse_scope({"scope_id": "s", "index": "0", "template_id": "tpl"}, tids)
    with pytest.raises(InvalidParams):   # 引用不在本批模板集
        parse_scope({"scope_id": "s", "index": 0, "template_id": "tpl-x"}, tids)


def test_parse_scope_routing_rules_must_be_string():
    """旧结构化格式(list of {expressions})已废弃 → 400。"""
    tids = {"tpl"}
    with pytest.raises(InvalidParams, match="boolean expression string"):
        parse_scope({"scope_id": "s", "index": 0, "template_id": "tpl",
                     "routing_rules": [{"expressions": [
                         {"field": "user_id", "op": "in", "values": ["a"]}]}]}, tids)
    with pytest.raises(InvalidParams):
        parse_scope({"scope_id": "s", "index": 0, "template_id": "tpl",
                     "routing_rules": 42}, tids)


def test_parse_scope_keeps_original_expr_and_parsed_rule():
    tids = {"tpl"}
    raw = "user_id in ('u1')  and  group_id not in ('g2')"   # 保留原始串(非规范化)
    scope = parse_scope({"scope_id": "s", "index": 0, "template_id": "tpl",
                         "routing_rules": raw}, tids)
    assert scope.expr == raw
    assert scope.to_payload()["routing_rules"] == raw
    assert scope.matches("u1", "g1", "b1") and not scope.matches("u1", "g2", "b1")


def test_parse_scope_error_includes_scope_id():
    tids = {"tpl"}
    with pytest.raises(InvalidParams, match=r"scope 's'"):
        parse_scope({"scope_id": "s", "index": 0, "template_id": "tpl",
                     "routing_rules": "oops"}, tids)


def test_parse_scope_null_or_blank_means_wildcard():
    tids = {"tpl"}
    for blank in (None, "", "   "):
        scope = parse_scope({"scope_id": "s", "index": 0, "template_id": "tpl",
                             "routing_rules": blank}, tids)
        assert scope.rule is None and scope.expr == (blank or "")
        assert scope.matches("any", "any", "any")


def test_has_wildcard_scope():
    assert not has_wildcard_scope([_scope("s", 0, expr="group_id in ('g')")])
    assert has_wildcard_scope([_scope("s", 0, expr="group_id in ('g')"), _scope("fb", 1)])


# -------------------------------------------------------------- Template/快照 roundtrip

def test_template_json_roundtrip():
    t = _tpl("tpl-1", agent_image="img:1", min_idle_pods=2, nfs_server=None,
             data={"k": "v"})
    restored = template_from_json(template_to_json(t))
    assert restored == t
    # int/bool 矫正 + 未知键忽略
    mixed = {**template_to_json(t), "container_port": "9090",
             "enabled": 0, "unknown_key": "x"}
    restored2 = template_from_json(mixed)
    assert restored2.container_port == 9090 and restored2.enabled is False


def test_snapshot_json_roundtrip_preserves_order_and_expr():
    scope = _scope("s", 0, "tpl", expr="user_id in ('u2','u1') and bot_id not in ('b1')")
    snap = build_snapshot([_scope("fb", 9, "tpl"), scope], [_tpl("tpl")], 42)
    text = snapshot_to_json(snap)
    restored = snapshot_from_json(text)
    assert restored.ver == 42
    assert [s.scope_id for s in restored.scopes] == ["s", "fb"]   # index 序保持
    # 原始表达式串与解析产物均保持;roundtrip 后匹配行为一致
    assert restored.scopes[0].expr == scope.expr
    assert restored.scopes[0].matches("u1", "g", "b2")
    assert not restored.scopes[0].matches("u1", "g", "b1")
    # 同一表达式串 → 快照串确定(同配置同串)
    assert snapshot_to_json(build_snapshot(
        [_scope("fb", 9, "tpl"), scope], [_tpl("tpl")], 42)) == text


def test_snapshot_from_json_corrupt_raises_value_error():
    with pytest.raises(ValueError):
        snapshot_from_json("{not json")
    with pytest.raises(ValueError):
        snapshot_from_json('{"ver": 1}')            # 缺 templates/scopes
    with pytest.raises(ValueError):
        snapshot_from_json('{"ver": 1, "templates": {}, "scopes": [{}]}')  # 缺字段
    with pytest.raises(ValueError):                 # 表达式非法(自有数据损坏)
        snapshot_from_json(
            '{"ver":1,"templates":{},"scopes":[{"scope_id":"s","index":0,'
            '"template_id":"t","routing_rules":"oops"}]}'
        )
    with pytest.raises(ValueError):                 # routing_rules 非字符串(旧结构残留)
        snapshot_from_json(
            '{"ver":1,"templates":{},"scopes":[{"scope_id":"s","index":0,'
            '"template_id":"t","routing_rules":[{"expressions":[]}]}]}'
        )
