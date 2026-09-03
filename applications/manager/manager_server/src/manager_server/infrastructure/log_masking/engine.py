# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""日志脱敏：内置规则种子与 CRUD 校验（Manager 不执行运行时脱敏）。

内置规则 / 校验常量需与 Gateway
``jiuwenswarm.infrastructure.log_masking.engine`` 保持一致，否则 seed 下发后
Gateway 回退 defaults 或热更前后行为会漂移。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

DEFAULT_REPLACEMENT = "******"
MAX_PATTERN_LENGTH = 512
MAX_REPLACEMENT_LENGTH = 64
_RULE_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_PATTERN_PERF_SAMPLE_LIMIT_SEC = 0.05
_PATTERN_PERF_PROBE_SAMPLES: tuple[str, ...] = (
    ("x" * 19) + "z",
    ("a" * 19) + "!",
    "abc sample line with token=value",
    ("b" * 28) + "!",
    ('x="' * 30) + "password=",
)
_UNSAFE_WILDCARD_QUANTIFIER_RE = re.compile(
    r"\(\.\*\)\*|\(\.\+\)\+|\(\.\*\)\+|\(\.\+\)\*"
)

_SENSITIVE_KW = (
    # token/credential 用 (?![a-z0-9]) 避免误伤 tokens_used / credentialing 等。
    r"password|passwd|pwd|secret|token(?![a-z0-9])|api[_-]?key|access[_-]?token|"
    r"refresh[_-]?token|authorization|credentials?|private[_-]?key|user[_-]?id"
)

_KV_SENSITIVE_PATTERN = re.compile(
    rf'(?i)(?P<prefix>["\']?[\w.-]{{0,128}}(?:{_SENSITIVE_KW})[\w.-]{{0,128}}["\']?\s*[:=]\s*)'
    # 值：引号串，或非 JSON 结构起始的标量（避免把 `"credentials": {` 整段吃掉）。
    r'(?P<val>"[^"]*"|\'[^\']*\'|[^,\s"\'\}\]\{\[]+)'
)
_KV_SENSITIVE_REPLACEMENT = rf"\g<prefix>{DEFAULT_REPLACEMENT}"


@dataclass(frozen=True)
class CompiledMaskingRule:
    rule_id: str
    pattern: re.Pattern[str]
    replacement: str
    name: str = ""
    priority: int = 0
    # True：命中后走指纹替换；False：使用 replacement 静态串（可含 \\g 回引）。
    with_fingerprint: bool = False


# priority 越大越先执行。顺序：大体积 data-uri → PII（无指纹）→ 敏感 KV（有指纹）。
_BUILTIN_RULES: list[CompiledMaskingRule] = [
    CompiledMaskingRule(
        "builtin_data_image",
        re.compile(r"data:image/[A-Za-z0-9.+-]+;base64,[A-Za-z0-9+/=]+"),
        "data:image/*;base64,******",
        name="DataURI图片",
        priority=50,
    ),
    # --- PII（邮箱 / 手机 / 身份证）：纯掩码，不附指纹 ---
    CompiledMaskingRule(
        "builtin_email",
        re.compile(r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[A-Za-z]{2,}\b"),
        DEFAULT_REPLACEMENT,
        name="邮箱",
        priority=40,
    ),
    CompiledMaskingRule(
        "builtin_cn_mobile",
        re.compile(r"(?<!\d)(?:\+?86[-\s]?)?1[3-9]\d{9}(?!\d)"),
        DEFAULT_REPLACEMENT,
        name="手机号",
        priority=30,
    ),
    CompiledMaskingRule(
        "builtin_cn_id_card",
        re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"),
        DEFAULT_REPLACEMENT,
        name="身份证号",
        priority=20,
    ),
    CompiledMaskingRule(
        "builtin_kv_sensitive",
        _KV_SENSITIVE_PATTERN,
        _KV_SENSITIVE_REPLACEMENT,
        name="敏感KV",
        priority=10,
        with_fingerprint=True,
    ),
]


def compiled_default_rules() -> list[CompiledMaskingRule]:
    """返回内置规则列表（priority 越大越先执行），供实例 seed 使用。"""
    return list(_BUILTIN_RULES)


def normalize_rule_id(rule_id: str) -> str:
    normalized = str(rule_id or "").strip()
    if not normalized or not _RULE_ID_RE.fullmatch(normalized):
        raise ValueError(
            "rule_id must be 1-64 chars of [A-Za-z0-9_.-]"
        )
    return normalized


def normalize_replacement(value: str | None) -> str:
    text = str(value or "").strip() or DEFAULT_REPLACEMENT
    if "\n" in text or "\r" in text:
        raise ValueError("replacement must not contain newlines")
    if len(text) > MAX_REPLACEMENT_LENGTH:
        raise ValueError(f"replacement must be at most {MAX_REPLACEMENT_LENGTH} chars")
    return text


def validate_pattern_structure(pattern: str) -> None:
    """静态拒绝明显 ReDoS 结构（如 ``(.*)*``）。"""
    if _UNSAFE_WILDCARD_QUANTIFIER_RE.search(pattern):
        raise ValueError(
            "pattern contains unsafe nested wildcard quantifiers like (.*)*"
        )


def validate_pattern_performance(
    pattern: re.Pattern[str],
    *,
    limit_sec: float = _PATTERN_PERF_SAMPLE_LIMIT_SEC,
) -> None:
    """拒绝在探测样例上过慢的自定义 pattern（防 ReDoS）。"""
    for sample in _PATTERN_PERF_PROBE_SAMPLES:
        t0 = time.perf_counter()
        pattern.sub("***", sample)
        elapsed = time.perf_counter() - t0
        if elapsed > limit_sec:
            raise ValueError(
                "pattern too slow "
                f"(>{limit_sec * 1000:.0f}ms on probe sample len={len(sample)})"
            )


def validate_pattern(
    pattern: str,
    *,
    check_structure: bool = True,
    check_performance: bool = True,
) -> str:
    text = str(pattern or "").strip()
    if not text:
        raise ValueError("pattern is required")
    if len(text) > MAX_PATTERN_LENGTH:
        raise ValueError(f"pattern must be at most {MAX_PATTERN_LENGTH} chars")
    try:
        compiled = re.compile(text)
    except re.error as exc:
        raise ValueError(f"invalid regex pattern: {exc}") from exc
    if check_structure:
        validate_pattern_structure(text)
    if check_performance:
        validate_pattern_performance(compiled)
    return text
