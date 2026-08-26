# coding: utf-8
"""routing.py 纯函数测试:表达式求值 / wire 校验 / 快照 roundtrip / first-fit 匹配。"""

from __future__ import annotations

import pytest

from agent_runtime.errors import InvalidParams
from agent_runtime.session_manager.models import Template
from agent_runtime.session_manager.routing import (
    MatchExpression,
    RoutingRule,
    RoutingScopeDef,
    build_snapshot,
    has_wildcard_scope,
    match_scope,
    parse_expression,
    parse_rule,
    parse_scope,
    snapshot_from_json,
    snapshot_to_json,
    template_from_json,
    template_to_json,
)


def _tpl(template_id: str, **overrides) -> Template:
    return Template(template_id=template_id, **overrides)


def _expr(field: str, op: str, values: tuple[str, ...] = ()) -> MatchExpression:
    return MatchExpression(field=field, op=op, values=frozenset(values))


def _scope(scope_id: str, index: int, template_id: str = "tpl",
           rules: tuple[RoutingRule, ...] = ()) -> RoutingScopeDef:
    return RoutingScopeDef(scope_id=scope_id, index=index,
                           template_id=template_id, rules=rules)


# -------------------------------------------------------------- 表达式求值

def test_expression_in_and_not_in():
    attrs = {"user_id": "u1", "group_id": "g1", "bot_id": "b1"}
    assert _expr("user_id", "in", ("u1", "u2")).matches(attrs)
    assert not _expr("user_id", "in", ("u2",)).matches(attrs)
    assert _expr("group_id", "not_in", ("g2",)).matches(attrs)
    assert not _expr("bot_id", "not_in", ("b1",)).matches(attrs)


def test_expression_empty_values():
    """空 values:in 恒假、not_in 恒真。"""
    attrs = {"user_id": "u1", "group_id": "g1", "bot_id": "b1"}
    assert not _expr("user_id", "in", ()).matches(attrs)
    assert _expr("user_id", "not_in", ()).matches(attrs)


def test_rule_and_scope_or_semantics():
    """规则内 AND、scope 内 OR。"""
    rule_a = RoutingRule(expressions=(
        _expr("group_id", "in", ("g1",)),
        _expr("bot_id", "not_in", ("banned",)),
    ))
    rule_b = RoutingRule(expressions=(_expr("user_id", "in", ("admin",)),))
    scope = _scope("s", 0, rules=(rule_a, rule_b))
    # rule_a 命中
    assert scope.matches("u1", "g1", "b1")
    # rule_a bot 被排除,但 rule_b user_id 命中(OR)
    assert scope.matches("admin", "g1", "banned")
    # 两条规则都不命中
    assert not scope.matches("u1", "g2", "b1")
    assert not scope.matches("u1", "g1", "banned")


def test_wildcard_scope_empty_rules_matches_everything():
    scope = _scope("fallback", 100, rules=())
    assert scope.matches(None, "", "")
    assert scope.matches("u", "g", "b")


def test_user_id_none_treated_as_empty_string():
    """求值层防御:user_id None → ""(含 values 含 "" 的边界)。"""
    scope = _scope("s", 0, rules=(
        RoutingRule(expressions=(_expr("user_id", "in", ("",)),)),
    ))
    assert scope.matches(None, "g", "b")
    assert not scope.matches("u1", "g", "b")


# -------------------------------------------------------------- 匹配(first-fit)

def test_match_scope_first_fit_by_index_then_scope_id():
    """index 升序;并列 index 按 scope_id 字典序;首个命中即止。"""
    low = _scope("z-low", 0, "tpl-a")                       # 通配,index 最小 → 恒先命中
    high = _scope("a-exact", 10, "tpl-b", rules=(
        RoutingRule(expressions=(_expr("group_id", "in", ("g1",)),)),
    ))
    tie_a = _scope("a-tie", 5, "tpl-a", rules=(
        RoutingRule(expressions=(_expr("user_id", "in", ("u1",)),)),
    ))
    tie_b = _scope("b-tie", 5, "tpl-b", rules=(
        RoutingRule(expressions=(_expr("user_id", "in", ("u1",)),)),
    ))
    snap = build_snapshot([high, tie_b, low, tie_a], [_tpl("tpl-a"), _tpl("tpl-b")], 1)
    # index 0 通配兜住一切
    assert match_scope(snap, "any", "any", "any").scope_id == "z-low"
    # 去掉通配后:index 5 并列 → scope_id 字典序 a-tie 先
    snap2 = build_snapshot([high, tie_b, tie_a], [_tpl("tpl-a"), _tpl("tpl-b")], 2)
    assert match_scope(snap2, "u1", "g", "b").scope_id == "a-tie"


def test_match_scope_skips_disabled_and_missing_template():
    """模板缺失/禁用的 scope 不命中,继续落下一个。"""
    disabled = _scope("s-off", 0, "tpl-off")               # 模板禁用
    missing = _scope("s-miss", 1, "tpl-none")              # 模板不在快照
    fallback = _scope("s-ok", 2, "tpl-ok")
    snap = build_snapshot(
        [disabled, missing, fallback],
        [_tpl("tpl-off", enabled=False), _tpl("tpl-ok")],
        1,
    )
    hit = match_scope(snap, "u", "g", "b")
    assert hit is not None and hit.scope_id == "s-ok"


def test_match_scope_no_match_returns_none():
    scoped = _scope("s", 0, rules=(
        RoutingRule(expressions=(_expr("group_id", "in", ("g1",)),)),
    ))
    snap = build_snapshot([scoped], [_tpl("tpl")], 1)
    assert match_scope(snap, "u", "g2", "b") is None


# -------------------------------------------------------------- wire 校验

def test_parse_scope_valid_and_invalid_scope_id():
    tids = {"tpl"}
    ok = parse_scope({"scope_id": "vip.group-1", "index": 3,
                      "template_id": "tpl", "routing_rules": []}, tids)
    assert ok.scope_id == "vip.group-1" and ok.index == 3 and ok.rules == ()
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


def test_parse_rule_rejects_empty_expressions():
    with pytest.raises(InvalidParams):
        parse_rule({"expressions": []})
    with pytest.raises(InvalidParams):
        parse_rule({})


def test_parse_expression_validation_matrix():
    assert parse_expression(
        {"field": "user_id", "op": "in", "values": ["a", "b"]}
    ) == MatchExpression("user_id", "in", frozenset({"a", "b"}))
    with pytest.raises(InvalidParams):   # 非法 field
        parse_expression({"field": "chat_id", "op": "in", "values": []})
    with pytest.raises(InvalidParams):   # 非法 op
        parse_expression({"field": "user_id", "op": "==", "values": []})
    with pytest.raises(InvalidParams):   # values 非字符串数组
        parse_expression({"field": "user_id", "op": "in", "values": [1, 2]})
    with pytest.raises(InvalidParams):   # values 非 list
        parse_expression({"field": "user_id", "op": "in", "values": "u1"})


def test_has_wildcard_scope():
    scoped = _scope("s", 0, rules=(
        RoutingRule(expressions=(_expr("group_id", "in", ("g",)),)),
    ))
    assert not has_wildcard_scope([scoped])
    assert has_wildcard_scope([scoped, _scope("fb", 1)])


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


def test_snapshot_json_roundtrip_preserves_order_and_rules():
    scope = _scope("s", 0, "tpl", rules=(
        RoutingRule(expressions=(
            _expr("user_id", "in", ("u2", "u1")),       # frozenset 无序 → 序列化排序
            _expr("bot_id", "not_in", ("b1",)),
        )),
    ))
    snap = build_snapshot([_scope("fb", 9, "tpl"), scope], [_tpl("tpl")], 42)
    text = snapshot_to_json(snap)
    restored = snapshot_from_json(text)
    assert restored.ver == 42
    assert [s.scope_id for s in restored.scopes] == ["s", "fb"]   # index 序保持
    rule = restored.scopes[0].rules[0]
    assert rule.expressions[0].values == frozenset({"u1", "u2"})
    # roundtrip 后匹配行为一致
    assert restored.scopes[0].matches("u1", "g", "b2")


def test_snapshot_from_json_corrupt_raises_value_error():
    with pytest.raises(ValueError):
        snapshot_from_json("{not json")
    with pytest.raises(ValueError):
        snapshot_from_json('{"ver": 1}')            # 缺 templates/scopes
    with pytest.raises(ValueError):
        snapshot_from_json('{"ver": 1, "templates": {}, "scopes": [{}]}')  # 缺字段
