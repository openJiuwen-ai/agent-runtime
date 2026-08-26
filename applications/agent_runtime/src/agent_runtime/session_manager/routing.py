# coding: utf-8
"""路由匹配纯函数层(scope 重构):表达式/规则/scope 定义 + 快照(反)序列化。

匹配语义(权威定义,见 docs/spec/session-manager.md):
- 表达式 = field(user_id|bot_id|group_id) op(in|not_in) values(字符串集合);
  ``in`` → 值 ∈ values;``not_in`` → 值 ∉ values;空 values:in 恒假、not_in 恒真;
- 规则(rule)= expressions 全真(AND);空 expressions 在下发校验时即拒绝;
- scope 命中 = 空 routing_rules(通配兜底)或任一规则为真(OR);
- 遍历:scopes 按 (index ASC, scope_id ASC) 排序,first-fit 首个命中即止;
- 引用模板缺失/禁用的 scope 视为不命中,继续落下一个(防御,正常不该发生);
- 无匹配 → ConfigNotFound(503)。

本模块零 Redis/DB 依赖(仅 import Template),可独立单测;
wire 载荷解析(parse_*)的校验失败抛 InvalidParams(400),
快照反序列化(snapshot_from_json)针对的是运行时自有数据,损坏抛 ValueError。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, fields
from typing import Any, Iterable

from ..errors import InvalidParams
from .models import Template

VALID_FIELDS = frozenset({"user_id", "group_id", "bot_id"})
VALID_OPS = frozenset({"in", "not_in"})

# scope_id 内嵌 Redis 键名(scope:{sid}:*)与 pods:registered 的 "{scope}:{pod}"
# 条目(按首个 ':' 切分),因此禁 ':';禁 '*'(SCAN 通配符)、空格与任何非 ASCII。
SCOPE_ID_RE = re.compile(r"^[0-9A-Za-z._-]{1,128}$")

# Template 全字段(dataclass 顺序即序列化顺序;int/bool 类型按默认值实例矫正)
_TEMPLATE_FIELDS: tuple[str, ...] = tuple(f.name for f in fields(Template))
_TEMPLATE_DEFAULTS = Template(template_id="")


@dataclass(frozen=True)
class MatchExpression:
    """单条匹配表达式:field op values。"""

    field: str
    op: str
    values: frozenset[str]

    def matches(self, attrs: dict[str, str]) -> bool:
        value = attrs.get(self.field, "")
        if self.op == "in":
            return value in self.values
        return value not in self.values  # not_in


@dataclass(frozen=True)
class RoutingRule:
    """一条路由规则:expressions 之间 AND。"""

    expressions: tuple[MatchExpression, ...]

    def matches(self, attrs: dict[str, str]) -> bool:
        return all(e.matches(attrs) for e in self.expressions)


@dataclass(frozen=True)
class RoutingScopeDef:
    """一个下发 scope:scope_id / index / 引用模板 / 规则集(OR)。"""

    scope_id: str
    index: int
    template_id: str
    rules: tuple[RoutingRule, ...]

    def matches(self, user_id: str | None, group_id: str | None, bot_id: str | None) -> bool:
        """空规则 = 通配;否则任一规则为真(OR)。user_id None → ""(防御)。"""
        if not self.rules:
            return True
        attrs = {
            "user_id": user_id or "",
            "group_id": group_id or "",
            "bot_id": bot_id or "",
        }
        return any(r.matches(attrs) for r in self.rules)

    def to_payload(self) -> dict[str, Any]:
        """wire 格式视图(/debug 与快照序列化共用)。"""
        return {
            "scope_id": self.scope_id,
            "index": self.index,
            "template_id": self.template_id,
            "routing_rules": [
                {"expressions": [
                    {"field": e.field, "op": e.op, "values": sorted(e.values)}
                    for e in rule.expressions
                ]}
                for rule in self.rules
            ],
        }


@dataclass(frozen=True)
class RoutingSnapshot:
    """路由快照:全部模板 + 已按 (index, scope_id) 排序的 scope 集。

    config_sync 写 DB 后整体重建并原子 SET 到
    ``session_manager:routing:snapshot``;route 热路径读它求值,零 DB。
    """

    ver: int
    templates: dict[str, Template]
    scopes: tuple[RoutingScopeDef, ...]


# -------------------------------------------------------------- wire 解析(config_sync 入口)

def parse_expression(payload: Any) -> MatchExpression:
    """wire 表达式 → MatchExpression;非法 → InvalidParams。"""
    if not isinstance(payload, dict):
        raise InvalidParams(f"match expression must be an object, got {payload!r}")
    field_name = str(payload.get("field") or "")
    op = str(payload.get("op") or "")
    values = payload.get("values")
    if field_name not in VALID_FIELDS:
        raise InvalidParams(
            f"expression field must be one of {sorted(VALID_FIELDS)}, got {field_name!r}"
        )
    if op not in VALID_OPS:
        raise InvalidParams(
            f"expression op must be one of {sorted(VALID_OPS)}, got {op!r}"
        )
    if not isinstance(values, list) or any(not isinstance(v, str) for v in values):
        raise InvalidParams(
            f"expression values must be a list of strings (field={field_name!r})"
        )
    return MatchExpression(field=field_name, op=op, values=frozenset(values))


def parse_rule(payload: Any) -> RoutingRule:
    """wire 规则 → RoutingRule;空 expressions 拒绝(通配请用空 routing_rules)。"""
    if not isinstance(payload, dict):
        raise InvalidParams(f"routing rule must be an object, got {payload!r}")
    expressions = payload.get("expressions")
    if not isinstance(expressions, list) or not expressions:
        raise InvalidParams(
            "routing rule requires a non-empty 'expressions' list "
            "(use empty routing_rules for the wildcard scope)"
        )
    return RoutingRule(expressions=tuple(parse_expression(e) for e in expressions))


def parse_scope(payload: Any, known_template_ids: set[str]) -> RoutingScopeDef:
    """wire scope → RoutingScopeDef;template 引用必须在本批模板集内。"""
    if not isinstance(payload, dict):
        raise InvalidParams(f"scope item must be an object, got {payload!r}")
    scope_id = str(payload.get("scope_id") or "")
    if not SCOPE_ID_RE.fullmatch(scope_id):
        raise InvalidParams(
            f"invalid scope_id {scope_id!r}: must match {SCOPE_ID_RE.pattern} "
            "(no ':', '*', whitespace or unicode)"
        )
    index = payload.get("index")
    if isinstance(index, bool) or not isinstance(index, int):
        raise InvalidParams(f"scope {scope_id!r} requires an integer index, got {index!r}")
    template_id = str(payload.get("template_id") or "")
    if not template_id:
        raise InvalidParams(f"scope {scope_id!r} requires template_id")
    if template_id not in known_template_ids:
        raise InvalidParams(
            f"scope {scope_id!r} references unknown template {template_id!r} "
            "(template must be in the same payload)"
        )
    rules_raw = payload.get("routing_rules")
    if rules_raw is None:
        rules_raw = []
    if not isinstance(rules_raw, list):
        raise InvalidParams(f"scope {scope_id!r} routing_rules must be a list")
    return RoutingScopeDef(
        scope_id=scope_id,
        index=index,
        template_id=template_id,
        rules=tuple(parse_rule(r) for r in rules_raw),
    )


def has_wildcard_scope(scopes: Iterable[RoutingScopeDef]) -> bool:
    """是否存在空规则(通配)scope——下发方应保证有,服务端缺失仅告警。"""
    return any(not s.rules for s in scopes)


# -------------------------------------------------------------- 匹配(route 热路径)

def match_scope(
    snapshot: RoutingSnapshot,
    user_id: str | None,
    group_id: str | None,
    bot_id: str | None,
) -> RoutingScopeDef | None:
    """按 (index, scope_id) 序 first-fit;模板缺失/禁用的 scope 跳过。"""
    for scope in snapshot.scopes:
        template = snapshot.templates.get(scope.template_id)
        if template is None or not template.enabled:
            continue
        if scope.matches(user_id, group_id, bot_id):
            return scope
    return None


# -------------------------------------------------------------- Template ↔ JSON(快照载体)

def template_to_json(template: Template) -> dict[str, Any]:
    """Template 全字段 → dict(快照存储;含 kubeconfig,仅内部控制面可见)。"""
    return {name: getattr(template, name) for name in _TEMPLATE_FIELDS}


def template_from_json(payload: dict[str, Any]) -> Template:
    """快照 dict → Template;未知键忽略,int/bool 字段按默认值类型矫正。"""
    kwargs: dict[str, Any] = {}
    for name in _TEMPLATE_FIELDS:
        if name not in payload or payload[name] is None:
            continue
        value = payload[name]
        default = getattr(_TEMPLATE_DEFAULTS, name)
        if isinstance(default, bool):
            value = bool(value)
        elif isinstance(default, int):
            value = int(value)
        kwargs[name] = value
    return Template(**kwargs)


# -------------------------------------------------------------- 快照(反)序列化

def build_snapshot(
    scopes: Iterable[RoutingScopeDef],
    templates: Iterable[Template],
    ver: int,
) -> RoutingSnapshot:
    """构造快照:scopes 排序为 (index ASC, scope_id ASC)。"""
    return RoutingSnapshot(
        ver=ver,
        templates={t.template_id: t for t in templates},
        scopes=tuple(sorted(scopes, key=lambda s: (s.index, s.scope_id))),
    )


def snapshot_to_json(snapshot: RoutingSnapshot) -> str:
    """快照 → 确定性 JSON(sort_keys + values 排序,同配置同串)。"""
    return json.dumps(
        {
            "ver": snapshot.ver,
            "templates": {
                tid: template_to_json(t) for tid, t in snapshot.templates.items()
            },
            "scopes": [s.to_payload() for s in snapshot.scopes],
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def snapshot_from_json(text: str) -> RoutingSnapshot:
    """JSON → 快照;结构损坏抛 ValueError(调用方触发 DB 重建)。"""
    try:
        data = json.loads(text)
        ver = int(data["ver"])
        templates = {
            str(tid): template_from_json(t)
            for tid, t in dict(data["templates"]).items()
        }
        scopes = tuple(sorted(
            (
                RoutingScopeDef(
                    scope_id=str(item["scope_id"]),
                    index=int(item["index"]),
                    template_id=str(item["template_id"]),
                    rules=tuple(
                        RoutingRule(expressions=tuple(
                            MatchExpression(
                                field=str(e["field"]),
                                op=str(e["op"]),
                                values=frozenset(str(v) for v in e["values"]),
                            ) for e in rule["expressions"]
                        ))
                        for rule in (item.get("routing_rules") or [])
                    ),
                )
                for item in list(data["scopes"])
            ),
            key=lambda s: (s.index, s.scope_id),
        ))
    except Exception as exc:  # noqa: BLE001 - 结构损坏统一转 ValueError
        raise ValueError(f"routing snapshot corrupt: {exc}") from exc
    return RoutingSnapshot(ver=ver, templates=templates, scopes=scopes)
