"""
Agent Protocol Interface for Dependency Inversion.

This module defines the interface contract that Agent implementations must follow.
Runtime (a2a_service) provides this interface, and Agent implementations (like EDPAgent)
implement it, enabling dependency inversion where Agent owns the startup and deployment.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import AsyncGenerator, Callable

from pydantic import BaseModel

from common.events import AgentEvent


class AgentProtocol(ABC):
    """
    Interface contract for Agent implementations.
    
    Any Agent that wants to be used with a2a_service must implement this interface.
    This enables:
    - Dependency inversion: Agent imports runtime, injects its implementation
    - Multiple Agent support: Different Agents can use the same service framework
    - Clean separation: Runtime only handles service layer, Agent handles business logic
    """
    
    @abstractmethod
    async def initialize(self) -> None:
        """
        Called once at application startup.
        
        Responsibilities:
        - Configure Runner (checkpointer, resource manager)
        - Build ReActAgent or other agent instance
        - Register tools and rails
        - Set up any required resources
        
        Raises:
            Exception: If initialization fails, service should exit
        """
        pass
    
    @abstractmethod
    async def agent_stream(
        self,
        query: str,
        conv_id: str,
        cascade_result: dict | None = None,
        context: dict | None = None,
    ) -> AsyncGenerator[AgentEvent, None]:
        """
        Agent processing entry point - yields events for each user request.
        
        Args:
            query: User input text (use "continue" for cascade resume)
            conv_id: Conversation ID (used for checkpoint recovery)
            cascade_result: Workflow result injected for cascade resume
            context: Additional context (usually {"body": original_request_body})
            
        Yields:
            AgentEvent: ThoughtEvent, AnswerEvent, or DelegateRequest
            
        Flow:
            1. First round (cascade_result=None): Start new session, run agent
            2. Cascade resume (cascade_result!=None): Recover from checkpoint,
               inject result, continue execution
        """
        pass


AgentInitializer = Callable[[], None]
AgentStreamFunc = Callable[
    [str, str, dict | None, dict | None],
    AsyncGenerator[AgentEvent, None]
]


class AgentCard(BaseModel):
    """Agent identity card for registration."""
    
    id: str
    name: str
    description: str = ""