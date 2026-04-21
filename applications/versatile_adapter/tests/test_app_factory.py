"""
Tests for versatile_adapter app_factory.py - create_adapter_app() factory function.
"""
import os
import tempfile
from unittest.mock import patch

import pytest
from fastapi import FastAPI


class TestCreateAdapterApp:
    """Tests for create_adapter_app factory function."""

    def test_create_adapter_app_returns_fastapi_instance(self):
        """Test create_adapter_app returns a FastAPI application."""
        from app_factory import create_adapter_app
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.env', delete=False) as f:
            f.write("VERSATILE_URL_TEMPLATE=https://test.com/api/{id}\n")
            temp_path = f.name
        
        try:
            with patch.dict(os.environ, {"CONFIG_PATH": temp_path}, clear=True):
                app = create_adapter_app()
                
                assert isinstance(app, FastAPI)
                assert app.title == "VersatileAdapter"
        finally:
            os.unlink(temp_path)

    def test_create_adapter_app_with_url_template(self):
        """Test create_adapter_app accepts custom url_template."""
        from app_factory import create_adapter_app
        
        app = create_adapter_app(
            url_template="https://custom.com/api/{conversation_id}",
        )
        
        assert isinstance(app, FastAPI)

    def test_create_adapter_app_with_timeout(self):
        """Test create_adapter_app accepts custom timeout."""
        from app_factory import create_adapter_app
        
        app = create_adapter_app(
            timeout=600,
        )
        
        assert isinstance(app, FastAPI)

    def test_create_adapter_app_with_both_params(self):
        """Test create_adapter_app with both url_template and timeout."""
        from app_factory import create_adapter_app
        
        app = create_adapter_app(
            url_template="https://test.com/api/{id}",
            timeout=300,
        )
        
        assert isinstance(app, FastAPI)


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
            f.write("LOG_FILE=/tmp/va-test.log\n")
            temp_path = f.name
        
        try:
            with patch.dict(os.environ, {"CONFIG_PATH": temp_path}, clear=True):
                setup_logging()
        finally:
            os.unlink(temp_path)