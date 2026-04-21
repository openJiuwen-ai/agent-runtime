"""
Tests for a2a_service app_factory.py - create_app() factory function.
"""
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI


class TestCreateApp:
    """Tests for create_app factory function."""

    def test_create_app_returns_fastapi_instance(self):
        """Test create_app returns a FastAPI application."""
        from app_factory import create_app
        
        mock_init = AsyncMock()
        mock_stream = MagicMock()
        
        app = create_app(
            agent_initializer=mock_init,
            agent_stream_func=mock_stream,
        )
        
        assert isinstance(app, FastAPI)
        assert app.title == "A2A Service"

    def test_create_app_with_custom_agent_name(self):
        """Test create_app accepts custom agent name."""
        from app_factory import create_app
        
        mock_init = AsyncMock()
        mock_stream = MagicMock()
        
        app = create_app(
            agent_initializer=mock_init,
            agent_stream_func=mock_stream,
            agent_name="CustomAgent",
        )
        
        assert "CustomAgent" in app.description

    def test_create_app_without_test_routes(self):
        """Test create_app excludes test routes by default."""
        from app_factory import create_app
        
        mock_init = AsyncMock()
        mock_stream = MagicMock()
        
        app = create_app(
            agent_initializer=mock_init,
            agent_stream_func=mock_stream,
            include_test_routes=False,
        )
        
        routes = [route.path for route in app.routes]
        assert "/simulate" not in routes

    def test_create_app_with_test_routes(self):
        """Test create_app includes test routes when enabled."""
        from app_factory import create_app
        
        mock_init = AsyncMock()
        mock_stream = MagicMock()
        
        app = create_app(
            agent_initializer=mock_init,
            agent_stream_func=mock_stream,
            include_test_routes=True,
        )
        
        assert app is not None

    @pytest.mark.asyncio
    async def test_create_app_lifespan_calls_initializer(self):
        from app_factory import create_app
        
        mock_init = AsyncMock()
        mock_stream = MagicMock()
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.env', delete=False) as f:
            f.write("REDIS_HOST=localhost\n")
            f.write("REDIS_PORT=6379\n")
            f.write("REDIS_DB=0\n")
            f.write("VERSATILE_ADAPTER_URL=http://localhost:8091\n")
            temp_path = f.name
        
        try:
            with patch.dict(os.environ, {"CONFIG_PATH": temp_path}, clear=True):
                with patch('common.redis_client.Redis.from_url') as mock_from_url:
                    mock_redis_instance = MagicMock()
                    mock_redis_instance.ping = AsyncMock()
                    mock_redis_instance.aclose = AsyncMock()
                    mock_from_url.return_value = mock_redis_instance
                    
                    app = create_app(
                        agent_initializer=mock_init,
                        agent_stream_func=mock_stream,
                    )
                    
                    async with app.router.lifespan_context(app):
                        pass
                    
                    mock_init.assert_called_once()
        finally:
            os.unlink(temp_path)


class TestSetupLogging:
    """Tests for setup_logging function."""

    def test_setup_logging_creates_stderr_logger(self):
        """Test setup_logging adds stderr logger."""
        from app_factory import setup_logging
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.env', delete=False) as f:
            f.write("LOG_LEVEL=INFO\n")
            temp_path = f.name
        
        try:
            with patch.dict(os.environ, {"CONFIG_PATH": temp_path}, clear=True):
                setup_logging()
        finally:
            os.unlink(temp_path)

    def test_setup_logging_with_log_file(self):
        """Test setup_logging adds file logger when log_file is set."""
        from app_factory import setup_logging
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.env', delete=False) as f:
            f.write("LOG_LEVEL=INFO\n")
            f.write("LOG_FILE=/tmp/test.log\n")
            temp_path = f.name
        
        try:
            with patch.dict(os.environ, {"CONFIG_PATH": temp_path}, clear=True):
                setup_logging()
        finally:
            os.unlink(temp_path)


class TestBuildCards:
    """Tests for card building functions."""

    def test_build_va_card(self):
        """Test _build_va_card creates valid AgentCard."""
        from app_factory import _build_va_card
        
        card = _build_va_card("http://localhost:8091")
        
        assert card.name == "VersatileAdapter"
        assert len(card.supported_interfaces) == 1

    def test_build_dpa_card(self):
        """Test _build_dpa_card creates valid AgentCard."""
        from app_factory import _build_dpa_card
        
        card = _build_dpa_card("localhost", 8090)
        
        assert card.name == "DPA Service"
        assert len(card.supported_interfaces) == 1

    def test_build_dpa_card_normalizes_host(self):
        """Test _build_dpa_card normalizes 0.0.0.0 to localhost."""
        from app_factory import _build_dpa_card
        
        card = _build_dpa_card("0.0.0.0", 8090)
        
        for interface in card.supported_interfaces:
            assert "localhost" in interface.url

    def test_build_dpa_card_with_custom_name(self):
        """Test _build_dpa_card accepts custom agent name."""
        from app_factory import _build_dpa_card
        
        card = _build_dpa_card("localhost", 8090, "MyCustomAgent")
        
        assert card.name == "MyCustomAgent"