"""Keep the frontend serializer and backend template-ref contracts aligned."""

from __future__ import annotations

import re
from pathlib import Path

from manager_server.schemas.template_slot_schemas import (
    SINGLE_VALUE_TEMPLATE_REF_SLOTS,
    TEMPLATE_REF_SLOTS,
)


def _typescript_string_set(source: str, constant: str) -> set[str]:
    match = re.search(
        rf"export const {constant} = (?:new Set<string>\()?\[(.*?)\](?:\)| as const);",
        source,
        flags=re.DOTALL,
    )
    assert match is not None, f"could not parse {constant}"
    return set(re.findall(r"['\"]([^'\"]+)['\"]", match.group(1)))


def test_frontend_and_backend_template_ref_slots_match():
    repository_root = Path(__file__).resolve().parents[6]
    source = (
        repository_root / "applications/manager/manager_web/src/utils/templateRef.ts"
    ).read_text(encoding="utf-8")

    assert _typescript_string_set(source, "TEMPLATE_REF_SLOTS") == set(TEMPLATE_REF_SLOTS)
    assert _typescript_string_set(source, "SINGLE_VALUE_TEMPLATE_REF_SLOTS") == set(
        SINGLE_VALUE_TEMPLATE_REF_SLOTS
    )
