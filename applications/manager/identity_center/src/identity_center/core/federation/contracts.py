"""Protocol-neutral optional access-token handoff for callback providers."""

from dataclasses import dataclass, field
from typing import Any

from openjiuwen_runtime.service import FederationAuthenticationResult


@dataclass(frozen=True)
class FederationTokenResult(FederationAuthenticationResult):
    token_bundle: dict[str, Any] = field(repr=False)
    external_sid: str | None = None
