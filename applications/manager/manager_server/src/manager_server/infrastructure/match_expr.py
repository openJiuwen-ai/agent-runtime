"""``match_expr`` 写入校验与求值（与配置生效策略 / Gateway 约定同构）。

仅允许引用 ``user_id`` / ``group_id`` / ``bot_id``；支持 ``==`` / ``!=`` / ``in`` / ``not in`` 运算符。
空值 / 空数组 / 不含比较符的字符串视为全匹配。
语法错误在**写入**时拒绝；**求值**失败视为不命中。
"""

from __future__ import annotations

import ast
import json
from typing import Any, Iterator

ALLOWED_MATCH_NAMES = frozenset({"user_id", "group_id", "bot_id"})

_INVALID_PREFIX = "invalid match_expr: "


def canonicalize_match_expr(value: Any) -> Any:
    """规范为可入库的 JSON 值：全匹配 → ``[]``；单表达式 → ``str``；多条件 OR → ``list[str]``。"""
    if value is None:
        return []
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if item is None:
                continue
            text = str(item).strip()
            if text:
                parts.append(text)
        if not parts:
            return []
        if len(parts) == 1:
            return _canonicalize_string(parts[0])
        return parts
    if isinstance(value, str):
        return _canonicalize_string(value.strip())
    text = str(value).strip()
    return _canonicalize_string(text) if text else []


def _canonicalize_string(text: str) -> Any:
    if not text:
        return []
    if text.startswith("["):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = None
        else:
            if isinstance(parsed, list):
                return canonicalize_match_expr(parsed)
    return text


def match_expr_is_unconditional(value: Any) -> bool:
    canon = canonicalize_match_expr(value)
    if canon == []:
        return True
    if not isinstance(canon, str):
        return False
    return not any(marker in canon for marker in ("==", "!=", " in "))


def validate_match_expr(value: Any) -> Any:
    """校验并返回规范后的 ``match_expr``；不合规则抛 ``ValueError``（``invalid match_expr: …``）。"""
    canon = canonicalize_match_expr(value)
    if canon == []:
        return []
    if isinstance(canon, list):
        for item in canon:
            _validate_expr_string(item)
        return canon
    _validate_expr_string(str(canon))
    return canon


def evaluate_match_expr(
    value: Any,
    *,
    user_id: str = "",
    group_id: str = "",
    bot_id: str = "",
) -> bool:
    """对当前请求上下文求值；失败或不命中返回 ``False``。"""
    try:
        canon = canonicalize_match_expr(value)
        if match_expr_is_unconditional(canon):
            return True
        ctx = {
            "user_id": str(user_id or ""),
            "group_id": str(group_id or ""),
            "bot_id": str(bot_id or ""),
        }
        if isinstance(canon, list):
            return any(_eval_expr_string(item, ctx) for item in canon)
        return _eval_expr_string(str(canon), ctx)
    except Exception:
        return False


def iter_equality_binds(value: Any) -> Iterator[tuple[str, str]]:
    """抽出 ``user_id == '…'`` / ``group_id == '…'`` 字面量，供授权时自动补绑实例准入。"""
    canon = canonicalize_match_expr(value)
    texts: list[str]
    if isinstance(canon, list):
        texts = [str(x) for x in canon]
    elif isinstance(canon, str) and canon:
        texts = [canon]
    else:
        return
    for text in texts:
        try:
            tree = ast.parse(text, mode="eval")
        except SyntaxError:
            continue
        yield from _iter_eq_binds(tree.body)


def _validate_expr_string(text: str) -> None:
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"{_INVALID_PREFIX}syntax error") from exc
    try:
        _Validator().visit(tree)
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"{_INVALID_PREFIX}{exc}") from exc


def _eval_expr_string(text: str, ctx: dict[str, str]) -> bool:
    tree = ast.parse(text, mode="eval")
    _Validator().visit(tree)
    return bool(_eval_node(tree.body, ctx))


class _Validator(ast.NodeVisitor):
    # ast.NodeVisitor 约定方法名为 visit_<NodeType>（非 snake_case），不可改名。
    # pylint: disable=huawei-invalid-name

    def generic_visit(self, node: ast.AST) -> None:
        if not isinstance(
            node,
            (
                ast.Expression,
                ast.BoolOp,
                ast.And,
                ast.Or,
                ast.Compare,
                ast.Eq,
                ast.NotEq,
                ast.In,
                ast.NotIn,
                ast.Name,
                ast.Load,
                ast.Constant,
                ast.Tuple,
                ast.List,
            ),
        ):
            raise ValueError(_error_for_node(node))
        super().generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:  # noqa: N802
        if node.id not in ALLOWED_MATCH_NAMES:
            raise ValueError(f"{_INVALID_PREFIX}unknown name")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802  # pragma: no cover - generic_visit 已拦截
        raise ValueError(f"{_INVALID_PREFIX}function calls are not supported")

    def visit_Compare(self, node: ast.Compare) -> None:  # noqa: N802
        for op in node.ops:
            if not isinstance(op, (ast.Eq, ast.NotEq, ast.In, ast.NotIn)):
                raise ValueError(f"{_INVALID_PREFIX}relational operators are not allowed")
        self.generic_visit(node)

    # pylint: enable=huawei-invalid-name


def _error_for_node(node: ast.AST) -> str:
    if isinstance(node, ast.Call):
        return f"{_INVALID_PREFIX}function calls are not supported"
    if isinstance(node, (ast.Lt, ast.Gt, ast.LtE, ast.GtE)):
        return f"{_INVALID_PREFIX}relational operators are not allowed"
    return f"{_INVALID_PREFIX}unsupported syntax"


def _eval_node(node: ast.AST, ctx: dict[str, str]) -> Any:
    if isinstance(node, ast.BoolOp):
        values = [_eval_node(v, ctx) for v in node.values]
        if isinstance(node.op, ast.And):
            return all(values)
        if isinstance(node.op, ast.Or):
            return any(values)
        return False
    if isinstance(node, ast.Compare):
        left = _eval_node(node.left, ctx)
        for op, comparator in zip(node.ops, node.comparators):
            right = _eval_node(comparator, ctx)
            if isinstance(op, ast.Eq):
                ok = left == right
            elif isinstance(op, ast.NotEq):
                ok = left != right
            elif isinstance(op, ast.In):
                ok = left in (right if isinstance(right, (list, tuple, set)) else [right])
            elif isinstance(op, ast.NotIn):
                ok = left not in (right if isinstance(right, (list, tuple, set)) else [right])
            else:
                return False
            if not ok:
                return False
            left = right
        return True
    if isinstance(node, ast.Name):
        return ctx.get(node.id, "")
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Tuple):
        return tuple(_eval_node(elt, ctx) for elt in node.elts)
    if isinstance(node, ast.List):
        return [_eval_node(elt, ctx) for elt in node.elts]
    return False


def _iter_eq_binds(node: ast.AST) -> Iterator[tuple[str, str]]:
    if isinstance(node, ast.BoolOp):
        for child in node.values:
            yield from _iter_eq_binds(child)
        return
    if isinstance(node, ast.Compare) and len(node.ops) == 1:
        op = node.ops[0]
        left, right = node.left, node.comparators[0]
        if isinstance(op, ast.Eq):
            name: str | None = None
            literal: str | None = None
            if isinstance(left, ast.Name) and left.id in ("user_id", "group_id") and isinstance(right, ast.Constant):
                name, literal = left.id, right.value if isinstance(right.value, str) else None
            elif isinstance(right, ast.Name) and right.id in ("user_id", "group_id") and isinstance(left, ast.Constant):
                name, literal = right.id, left.value if isinstance(left.value, str) else None
            if name and literal:
                yield name, literal
        elif isinstance(op, ast.In):
            if isinstance(left, ast.Name) and left.id in ("user_id", "group_id") and isinstance(right, ast.Tuple):
                for elt in right.elts:
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                        yield left.id, elt.value
        return
