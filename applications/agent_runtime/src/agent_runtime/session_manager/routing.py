# coding: utf-8
"""路由匹配纯函数层(scope 重构):routing_rules 表达式解析/scope 定义 + 快照(反)序列化。

匹配语义(权威定义,见 docs/spec/session-manager.md):
- routing_rules = 布尔表达式**字符串**,条件经 and/or 与括号任意组合:
  ``group_id not in ('g1', 'g2') and (user_id in ('admin') or bot_id in ('b1'))``;
  优先级:条件 > and > or(与 SQL/Python 一致),括号显式分组;
  关键字 and/or/in/not 大小写不敏感;字段名是固定小写枚举;
- 条件 = field(user_id|bot_id|group_id) op(in|not in) ('v1', 'v2', ...);
  ``in`` → 值 ∈ values;``not_in`` → 值 ∉ values;空 values ():in 恒假、not_in 恒真;
- scope 命中 = 空 routing_rules(null/空串/纯空白 → 通配兜底)或表达式为真;
- 遍历:scopes 按 (index ASC, scope_id ASC) 排序,first-fit 首个命中即止;
- 引用模板缺失/禁用的 scope 视为不命中,继续落下一个(防御,正常不该发生);
- scope 自身 ``enabled=False`` 或 ``expires_at`` 已过期视为不命中,继续落下一个;
- 无匹配 → ConfigNotFound(503)。

解析失败(未知字段/裸 `not`/悬空括号/未引号值……)在 config_sync 下发校验时
即拒(InvalidParams 400);快照反序列化(snapshot_from_json)针对运行时自有
数据,损坏抛 ValueError。表达式上限:长度 8000、括号嵌套 32(防御性,正常
scope 表达式远小于此)。

本模块零 Redis/DB 依赖(仅 import Template),可独立单测。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, fields
from datetime import datetime
from typing import Any, Iterable

from ..errors import InvalidParams
from ..util import as_utc_naive, parse_datetime, utc_now
from .models import Template

VALID_FIELDS = frozenset({"user_id", "group_id", "bot_id"})

# scope_id 内嵌 Redis 键名(scope:{sid}:*)与 pods:registered 的 "{scope}:{pod}"
# 条目(按首个 ':' 切分),因此禁 ':';禁 '*'(SCAN 通配符)、空格与任何非 ASCII。
SCOPE_ID_RE = re.compile(r"^[0-9A-Za-z._-]{1,128}$")

# Template 全字段(dataclass 顺序即序列化顺序;int/bool 类型按默认值实例矫正)
_TEMPLATE_FIELDS: tuple[str, ...] = tuple(f.name for f in fields(Template))
_TEMPLATE_DEFAULTS = Template(template_id="")


@dataclass(frozen=True)
class MatchExpression:
    """条件叶子:field op values(结构求值用;wire 载体是表达式字符串)。"""

    field: str
    op: str
    values: frozenset[str]

    def matches(self, attrs: dict[str, str]) -> bool:
        value = attrs.get(self.field, "")
        if self.op == "in":
            return value in self.values
        return value not in self.values  # not_in


@dataclass(frozen=True)
class AndNode:
    """AND 分支:children 全真才真。"""

    children: tuple["BoolNode", ...]

    def matches(self, attrs: dict[str, str]) -> bool:
        return all(c.matches(attrs) for c in self.children)


@dataclass(frozen=True)
class OrNode:
    """OR 分支:任一 child 为真即真。"""

    children: tuple["BoolNode", ...]

    def matches(self, attrs: dict[str, str]) -> bool:
        return any(c.matches(attrs) for c in self.children)


# 表达式树节点:条件叶子或 and/or 分支(解析产物,scope 上以 expr 原串存储)
BoolNode = MatchExpression | AndNode | OrNode


@dataclass(frozen=True)
class RoutingScopeDef:
    """一个下发 scope:scope_id / index / 引用模板 / routing_rules 表达式。

    ``expr`` 是 wire/DB/快照的存储载体(原始字符串,空 = 通配);
    ``rule`` 是它的解析产物(通配时 None)——二者由构造方(解析入口)保证一致。
    ``enabled`` / ``expires_at`` 控制生效:禁用或过期的 scope 不参与匹配与预热。
    """

    scope_id: str
    index: int
    template_id: str
    expr: str
    rule: BoolNode | None
    enabled: bool = True
    expires_at: datetime | None = None

    def is_active(self, now: datetime | None = None) -> bool:
        """enabled 且未过期才生效(expires_at=None = 永不过期)。"""
        if not self.enabled:
            return False
        if self.expires_at is None:
            return True
        current = as_utc_naive(now) if now is not None else utc_now()
        return self.expires_at > current

    def matches(self, user_id: str | None, group_id: str | None, bot_id: str | None) -> bool:
        """空表达式 = 通配;否则求值表达式树。user_id None → ""(防御)。"""
        if self.rule is None:
            return True
        attrs = {
            "user_id": user_id or "",
            "group_id": group_id or "",
            "bot_id": bot_id or "",
        }
        return self.rule.matches(attrs)

    def to_payload(self) -> dict[str, Any]:
        """wire 格式视图(/visualization 与快照序列化共用)。"""
        return {
            "scope_id": self.scope_id,
            "index": self.index,
            "template_id": self.template_id,
            "routing_rules": self.expr,
            "enabled": self.enabled,
            "expires_at": (
                self.expires_at.isoformat() if self.expires_at is not None else None
            ),
        }


@dataclass(frozen=True)
class RoutingSnapshot:
    """路由快照:全部模板 + 已按 (index, scope_id) 排序的 scope 集。

    config_sync 写 DB 后整体重建并原子 SET 到
    ``{session_manager}:routing:snapshot``;route 热路径读它求值,零 DB。
    """

    ver: int
    templates: dict[str, Template]
    scopes: tuple[RoutingScopeDef, ...]


# -------------------------------------------------------------- 表达式字符串解析(config_sync 入口)

# 防御上限:正常 scope 表达式(几十个条件)远小于此
MAX_EXPR_LEN = 8000
MAX_EXPR_DEPTH = 32

_KEYWORDS = frozenset({"and", "or", "not", "in"})

# 词法:空白 | 括号/逗号 | 裸词(字段名/关键字,大小写不敏感) | 单引号串
# (串内 ``''`` 加倍与 ``\'``/``\\`` 反斜杠转义;其他 ``\x`` 保留字面)
_TOKEN_RE = re.compile(
    r"\s+"
    r"|(?P<lparen>\()"
    r"|(?P<rparen>\))"
    r"|(?P<comma>,)"
    r"|(?P<word>[A-Za-z_][A-Za-z0-9_-]*)"
    r"|(?P<str>'(?:[^'\\]|\\.|'')*')"
)


def _unquote(token: str) -> str:
    r"""剥外层引号并解转义('' → ';\' 与 \\ → 字面;其他反斜杠组合保留原样)。"""
    body = token[1:-1]
    out: list[str] = []
    i, n = 0, len(body)
    while i < n:
        ch = body[i]
        if ch == "\\" and i + 1 < n and body[i + 1] in ("\\", "'"):
            out.append(body[i + 1])
            i += 2
        elif ch == "'":  # '' 加倍(词法保证成对出现)
            out.append("'")
            i += 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


class _Parser:
    """递归下降:or_expr → and_expr → primary(括号|条件);and 绑定紧于 or。"""

    def __init__(self, text: str) -> None:
        self.text = text
        self.tokens = self._tokenize(text)
        self.pos = 0
        self.depth = 0

    @staticmethod
    def _tokenize(text: str) -> list[tuple[str, Any]]:
        tokens: list[tuple[str, Any]] = []
        i = 0
        while i < len(text):
            m = _TOKEN_RE.match(text, i)
            if m is None:
                raise InvalidParams(
                    f"routing_rules invalid character {text[i]!r} at offset {i}: {text!r}"
                )
            i = m.end()
            kind = m.lastgroup
            if kind is None:
                continue  # 空白
            tokens.append(
                ("value", _unquote(m.group())) if kind == "str" else (kind, m.group())
            )
        return tokens

    # ---- 基础设施

    def _peek(self) -> tuple[str, Any]:
        if self.pos < len(self.tokens):
            return self.tokens[self.pos]
        return ("end", None)

    def _fail(self, reason: str) -> InvalidParams:
        return InvalidParams(f"routing_rules parse error: {reason} (expr={self.text!r})")

    def _take_keyword(self, word: str) -> bool:
        """消费大小写不敏感的关键字;不匹配则不动。"""
        kind, value = self._peek()
        if kind == "word" and str(value).lower() == word:
            self.pos += 1
            return True
        return False

    # ---- 文法

    def parse(self) -> BoolNode:
        node = self._or_expr()
        kind, value = self._peek()
        if kind != "end":
            raise self._fail(f"unexpected {value!r} after complete expression")
        return node

    def _or_expr(self) -> BoolNode:
        children = [self._and_expr()]
        while self._take_keyword("or"):
            children.append(self._and_expr())
        return children[0] if len(children) == 1 else OrNode(tuple(children))

    def _and_expr(self) -> BoolNode:
        children = [self._primary()]
        while self._take_keyword("and"):
            children.append(self._primary())
        return children[0] if len(children) == 1 else AndNode(tuple(children))

    def _primary(self) -> BoolNode:
        kind, value = self._peek()
        if kind == "lparen":
            self.pos += 1
            self.depth += 1
            if self.depth > MAX_EXPR_DEPTH:
                raise self._fail(f"parentheses nested deeper than {MAX_EXPR_DEPTH}")
            node = self._or_expr()
            self.depth -= 1
            kind, value = self._peek()
            if kind != "rparen":
                raise self._fail(f"expected ')' but got {value!r}")
            self.pos += 1
            return node
        if kind == "word":
            return self._condition()
        raise self._fail(f"expected a condition or '(' but got {value!r}")

    def _condition(self) -> MatchExpression:
        _, field_name = self._peek()
        self.pos += 1
        lowered = str(field_name).lower()
        if lowered in _KEYWORDS:
            raise self._fail(
                f"keyword {field_name!r} cannot start a condition "
                "(only 'not in' is supported, not unary 'not')"
            )
        if field_name not in VALID_FIELDS:
            raise self._fail(
                f"unknown field {field_name!r} (valid fields: {sorted(VALID_FIELDS)})"
            )
        op = "not_in" if self._take_keyword("not") else "in"
        if not self._take_keyword("in"):
            kind, value = self._peek()
            raise self._fail(
                f"expected 'in' or 'not in' after field {field_name!r} but got {value!r}"
            )
        kind, value = self._peek()
        if kind != "lparen":
            raise self._fail(f"expected '(' value list after {field_name!r} but got {value!r}")
        self.pos += 1
        values: list[str] = []
        kind, value = self._peek()
        if kind == "rparen":  # 空列表 ():in 恒假、not_in 恒真
            self.pos += 1
            return MatchExpression(field_name, op, frozenset())
        while True:
            kind, value = self._peek()
            if kind != "value":
                raise self._fail(f"expected a quoted string value but got {value!r}")
            self.pos += 1
            values.append(str(value))
            kind, value = self._peek()
            if kind == "rparen":
                self.pos += 1
                return MatchExpression(field_name, op, frozenset(values))
            if kind != "comma":
                raise self._fail(f"expected ',' or ')' in value list but got {value!r}")
            self.pos += 1
            if self._peek()[0] == "rparen":  # 容忍尾逗号
                self.pos += 1
                return MatchExpression(field_name, op, frozenset(values))


def parse_routing_expr(text: str) -> BoolNode:
    """routing_rules 表达式字符串 → 表达式树;非法 → InvalidParams。

    非空校验由调用方负责(空串 = 通配,不是解析错误)。
    """
    if len(text) > MAX_EXPR_LEN:
        raise InvalidParams(
            f"routing_rules longer than {MAX_EXPR_LEN} chars: {len(text)}"
        )
    return _Parser(text).parse()


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
    expr_raw = payload.get("routing_rules")
    if expr_raw is None:
        expr_raw = ""
    if not isinstance(expr_raw, str):
        raise InvalidParams(
            f"scope {scope_id!r} routing_rules must be a boolean expression string "
            "(or null/empty for the wildcard scope), e.g. "
            "\"user_id in ('admin') or group_id not in ('g1')\", got "
            f"{type(expr_raw).__name__}: {expr_raw!r}"
        )
    try:
        rule = parse_routing_expr(expr_raw) if expr_raw.strip() else None
    except InvalidParams as exc:
        raise InvalidParams(f"scope {scope_id!r}: {exc}") from exc
    enabled = payload.get("enabled", True)
    if not isinstance(enabled, bool):
        raise InvalidParams(
            f"scope {scope_id!r} enabled must be a boolean, got {enabled!r}"
        )
    try:
        expires_at = parse_datetime(
            payload.get("expires_at"), field=f"scope {scope_id!r}.expires_at"
        )
    except ValueError as exc:
        raise InvalidParams(str(exc)) from exc
    return RoutingScopeDef(
        scope_id=scope_id,
        index=index,
        template_id=template_id,
        expr=expr_raw,
        rule=rule,
        enabled=enabled,
        expires_at=expires_at,
    )


def has_wildcard_scope(scopes: Iterable[RoutingScopeDef]) -> bool:
    """是否存在生效中的空表达式(通配)scope——下发方应保证有,服务端缺失仅告警。"""
    return any(s.rule is None and s.is_active() for s in scopes)


# -------------------------------------------------------------- 匹配(route 热路径)

def match_scope(
    snapshot: RoutingSnapshot,
    user_id: str | None,
    group_id: str | None,
    bot_id: str | None,
    *,
    now: datetime | None = None,
) -> RoutingScopeDef | None:
    """按 (index, scope_id) 序 first-fit;禁用/过期 scope 与模板缺失/禁用跳过。"""
    for scope in snapshot.scopes:
        if not scope.is_active(now):
            continue
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
    """快照 → 确定性 JSON(sort_keys;routing_rules 按原始串存储,同配置同串)。"""
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

    def scope_item(item: Any) -> RoutingScopeDef:
        expr = item.get("routing_rules")
        if expr is None:
            expr = ""
        if not isinstance(expr, str):
            raise ValueError(f"routing_rules must be a string, got {type(expr).__name__}")
        enabled = item.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ValueError(f"enabled must be a boolean, got {type(enabled).__name__}")
        expires_at = parse_datetime(item.get("expires_at"))
        return RoutingScopeDef(
            scope_id=str(item["scope_id"]),
            index=int(item["index"]),
            template_id=str(item["template_id"]),
            expr=expr,
            rule=parse_routing_expr(expr) if expr.strip() else None,
            enabled=enabled,
            expires_at=expires_at,
        )

    try:
        data = json.loads(text)
        ver = int(data["ver"])
        templates = {
            str(tid): template_from_json(t)
            for tid, t in dict(data["templates"]).items()
        }
        scopes = tuple(sorted(
            (scope_item(item) for item in list(data["scopes"])),
            key=lambda s: (s.index, s.scope_id),
        ))
    except Exception as exc:  # noqa: BLE001 - 结构损坏统一转 ValueError
        raise ValueError(f"routing snapshot corrupt: {exc}") from exc
    return RoutingSnapshot(ver=ver, templates=templates, scopes=scopes)
