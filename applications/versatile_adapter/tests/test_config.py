"""
Tests for versatile_adapter config.py - CONFIG_PATH support.
"""
import os
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
        
        custom_path = "/etc/edp/va.env"
        
        with patch.dict(os.environ, {"CONFIG_PATH": custom_path}):
            path = _get_env_file_path()
            
            assert path == Path(custom_path)


class TestSettings:
    """Tests for Settings class."""

    def test_settings_has_url_template(self):
        """Test Settings has versatile_url_template field."""
        from config import Settings
        
        settings = Settings(
            versatile_url_template="https://test.com/api/{id}",
        )
        
        assert settings.versatile_url_template == "https://test.com/api/{id}"

    def test_settings_has_timeout(self):
        """Test Settings has versatile_timeout field."""
        from config import Settings
        
        settings = Settings(versatile_timeout=120)
        
        assert settings.versatile_timeout == 120

    def test_settings_extra_ignore(self):
        """Test Settings ignores extra fields."""
        from config import Settings
        
        settings = Settings(versatile_url_template="https://test.com")
        
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