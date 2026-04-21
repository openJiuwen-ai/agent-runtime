# VersatileAdapter Changes Log

## 2026-04-20 - Dependency Inversion Architecture

### Summary
Modified versatile_adapter to support dependency inversion pattern, where
configuration can be externalized and service can be created via factory function.
This enables:
- EDPAgent owns startup and deployment
- Configuration externalization via CONFIG_PATH
- Factory mode for programmatic service creation

### Files Modified

| File | Changes |
|------|---------|
| `config.py` | Added CONFIG_PATH environment variable support |
| `app_factory.py` | NEW - create_adapter_app() factory function |
| `app.py` | Added USE_FACTORY_MODE toggle for backward compatibility |

### CONFIG_PATH Support

Before (hardcoded):
```python
env_file=Path(__file__).parent / ".env"
```

After (externalized):
```python
def _get_env_file_path() -> Path:
    config_path = os.environ.get("CONFIG_PATH")
    if config_path:
        return Path(config_path)
    return Path(__file__).parent / ".env"
```

Usage:
```bash
# Development (default)
python main.py

# Production (external config)
CONFIG_PATH=/etc/edp/versatile.env python main.py

# Docker
docker run -e CONFIG_PATH=/app/config/.env ...
```

### Factory Mode Usage

New mode (dependency inversion):
```python
from agent_runtime.versatile_adapter.app_factory import create_adapter_app

app = create_adapter_app(
    url_template="https://versatile.example/api/{conv_id}",
    timeout=600,
)
uvicorn.run(app, host="0.0.0.0", port=8091)
```

Legacy mode (backward compatible):
```bash
python main.py  # Uses original behavior
```

Enable new mode:
```bash
USE_FACTORY_MODE=true python main.py
```

### Breaking Changes

None - backward compatible by default.

### Integration with EDPAgent

After EDPAgent implements the startup entry point:
```python
# EDPAgent startup/run_edp.py
from agent_runtime.versatile_adapter import create_adapter_app

app = create_adapter_app()
uvicorn.run(app, host="0.0.0.0", port=8091)
```