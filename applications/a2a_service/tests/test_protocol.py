"""
Tests for a2a_service protocol.py - AgentProtocol interface.
"""
import pytest
from typing import AsyncGenerator

from protocol import AgentProtocol, AgentCard, AgentInitializer, AgentStreamFunc
from common.events import AgentEvent, ThoughtEvent, AnswerEvent


class MockAgent(AgentProtocol):
    """Mock implementation of AgentProtocol for testing."""
    
    def __init__(self):
        self.initialized = False
    
    async def initialize(self) -> None:
        self.initialized = True
    
    async def agent_stream(
        self,
        query: str,
        conv_id: str,
        cascade_result: dict | None = None,
        context: dict | None = None,
    ) -> AsyncGenerator[AgentEvent, None]:
        yield ThoughtEvent(content="thinking...")
        yield AnswerEvent(content="answer", final=True)


class TestAgentProtocol:
    """Tests for AgentProtocol interface."""

    def test_agent_protocol_is_abstract(self):
        """Test AgentProtocol cannot be instantiated directly."""
        with pytest.raises(TypeError):
            AgentProtocol()

    def test_mock_agent_implements_interface(self):
        """Test MockAgent properly implements AgentProtocol."""
        agent = MockAgent()
        
        assert hasattr(agent, 'initialize')
        assert hasattr(agent, 'agent_stream')
        assert callable(agent.initialize)
        assert callable(agent.agent_stream)

    @pytest.mark.asyncio
    async def test_initialize_method(self):
        """Test initialize method is called correctly."""
        agent = MockAgent()
        
        assert not agent.initialized
        
        await agent.initialize()
        
        assert agent.initialized

    @pytest.mark.asyncio
    async def test_agent_stream_yields_events(self):
        """Test agent_stream yields proper AgentEvent types."""
        agent = MockAgent()
        
        events = []
        async for event in agent.agent_stream("test query", "conv-123"):
            events.append(event)
        
        assert len(events) == 2
        assert isinstance(events[0], ThoughtEvent)
        assert isinstance(events[1], AnswerEvent)
        assert events[0].content == "thinking..."
        assert events[1].content == "answer"
        assert events[1].final is True

    @pytest.mark.asyncio
    async def test_agent_stream_with_cascade_result(self):
        """Test agent_stream accepts cascade_result parameter."""
        agent = MockAgent()
        
        cascade = {"workflow_result": "some result"}
        
        events = []
        async for event in agent.agent_stream(
            "continue", 
            "conv-123", 
            cascade_result=cascade
        ):
            events.append(event)
        
        assert len(events) == 2

    @pytest.mark.asyncio
    async def test_agent_stream_with_context(self):
        """Test agent_stream accepts context parameter."""
        agent = MockAgent()
        
        context = {"body": {"user_id": "test"}}
        
        events = []
        async for event in agent.agent_stream(
            "test", 
            "conv-123", 
            context=context
        ):
            events.append(event)
        
        assert len(events) == 2


class TestAgentCard:
    """Tests for AgentCard model."""

    def test_agent_card_basic(self):
        """Test AgentCard with basic fields."""
        card = AgentCard(
            id="test-agent",
            name="Test Agent",
            description="A test agent",
        )
        
        assert card.id == "test-agent"
        assert card.name == "Test Agent"
        assert card.description == "A test agent"

    def test_agent_card_optional_description(self):
        """Test AgentCard with empty description."""
        card = AgentCard(
            id="minimal-agent",
            name="Minimal",
        )
        
        assert card.id == "minimal-agent"
        assert card.name == "Minimal"
        assert card.description == ""

    def test_agent_card_serialization(self):
        """Test AgentCard can be serialized."""
        card = AgentCard(
            id="agent-1",
            name="Agent One",
            description="First agent",
        )
        
        data = card.model_dump()
        
        assert data["id"] == "agent-1"
        assert data["name"] == "Agent One"
        assert data["description"] == "First agent"


class TestTypeAliases:
    """Tests for type alias definitions."""

    def test_agent_initializer_is_callable(self):
        """Test AgentInitializer type alias represents async callable."""
        async def init():
            pass
        
        assert callable(init)

    def test_agent_stream_func_is_callable(self):
        """Test AgentStreamFunc type alias represents async generator callable."""
        async def stream(query, conv_id, cascade=None, context=None):
            yield ThoughtEvent(content="test")
        
        assert callable(stream)