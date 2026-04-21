"""
Tests for a2a_service config.py - CONFIG_PATH support.
"""
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


class TestGetEnvFilePath:
    """Tests for _get_env_file_path function."""

    def test_default_path(self):
        """Test default .env path when CONFIG_PATH is not set."""
        from config import _get_env_file_path
        
        with patch.dict(os.environ, {}, clear=True):
            if "CONFIG_PATH" in os.environ:
                del os.environ["CONFIG_PATH"]
            
            path = _get_env_file_path()
            
            assert path == Path(__file__).parent.parent / ".env"

    def test_config_path_override(self):
        """Test CONFIG_PATH overrides default path."""
        from config import _get_env_file_path
        
        custom_path = "/etc/edp/custom.env"
        
        with patch.dict(os.environ, {"CONFIG_PATH": custom_path}):
            path = _get_env_file_path()
            
            assert path == Path(custom_path)

    def test_relative_config_path(self):
        """Test CONFIG_PATH with relative path."""
        from config import _get_env_file_path
        
        relative_path = "./config/.env"
        
        with patch.dict(os.environ, {"CONFIG_PATH": relative_path}):
            path = _get_env_file_path()
            
            assert path == Path(relative_path)


class TestSettings:
    """Tests for Settings class."""

    def test_settings_has_redis_url_property(self):
        """Test Settings has redis_url computed property."""
        from config import Settings
        
        settings = Settings(
            redis_host="localhost",
            redis_port=6379,
            redis_db=0,
        )
        
        assert settings.redis_url == "redis://localhost:6379/0"

    def test_redis_url_with_password(self):
        """Test redis_url with password includes encoded credentials."""
        from config import Settings
        
        settings = Settings(
            redis_host="localhost",
            redis_port=6379,
            redis_db=0,
            redis_password="p@ss:word",
        )
        
        assert settings.redis_url.startswith("redis://:")
        assert "localhost:6379" in settings.redis_url

    def test_settings_extra_ignore(self):
        """Test Settings ignores extra fields."""
        from config import Settings
        
        settings = Settings(redis_host="test")
        
        assert not hasattr(settings, 'unknown_var')


class TestGetSettingsCached:
    """Tests for get_settings caching behavior."""

    def test_get_settings_returns_settings(self):
        """Test get_settings returns Settings instance."""
        from config import get_settings
        
        settings = get_settings()
        
        assert settings is not None

    def test_get_settings_is_cached(self):
        """Test get_settings returns cached instance."""
        from config import get_settings
        
        first = get_settings()
        second = get_settings()
        
        assert first is second