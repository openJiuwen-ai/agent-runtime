# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""日志脱敏：内置规则种子与 CRUD 校验（运行时脱敏在 Gateway，不在 Manager）。"""

from .engine import (
    DEFAULT_REPLACEMENT,
    MAX_PATTERN_LENGTH,
    MAX_REPLACEMENT_LENGTH,
    CompiledMaskingRule,
    compiled_default_rules,
    normalize_replacement,
    normalize_rule_id,
    validate_pattern,
    validate_pattern_performance,
    validate_pattern_structure,
)

__all__ = (
    "DEFAULT_REPLACEMENT",
    "MAX_PATTERN_LENGTH",
    "MAX_REPLACEMENT_LENGTH",
    "CompiledMaskingRule",
    "compiled_default_rules",
    "normalize_replacement",
    "normalize_rule_id",
    "validate_pattern",
    "validate_pattern_performance",
    "validate_pattern_structure",
)
