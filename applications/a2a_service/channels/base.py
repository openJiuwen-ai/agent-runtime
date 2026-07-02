from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from a2a.types.a2a_pb2 import SendMessageRequest


@dataclass(frozen=True)
class ParsedRequest:
    conversation_id: str
    agent_id: str
    query: str
    body: dict[str, Any]
    headers: dict[str, Any] = field(default_factory=dict)
    params: dict[str, Any] = field(default_factory=dict)
    stream: bool = True
    trace_id: str = ""


class Channel(ABC):
    name: str = ""

    @abstractmethod
    def parse_request(
        self,
        body: dict[str, Any],
        *,
        path_params: dict[str, Any],
        headers: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> ParsedRequest:
        """Parse a northbound HTTP request into the channel-neutral request model."""

    @abstractmethod
    def build_message(self, parsed: ParsedRequest) -> SendMessageRequest:
        """Build the A2A SendMessageRequest consumed by the orchestrator."""

    @abstractmethod
    def format_event(
        self,
        event: dict[str, Any],
        *,
        agent_id: str,
        conversation_id: str,
        elapsed: float,
    ) -> dict[str, Any] | None:
        """Format a normalized {type, data} event for the northbound API."""
