# A2A Service Changes Log

## 2026-04-20 - Dependency Inversion Architecture

### Summary
Modified a2a_service to support dependency inversion pattern, where Agent implementation
can be injected externally instead of hardcoded import. This enables:
- Agent owns startup and deployment
- Runtime provides service framework
- Clean separation of responsibilities

### Files Modified

| File | Changes |
|------|---------|
| `config.py` | Added CONFIG_PATH environment variable support for externalized configuration |
| `protocol.py` | NEW - AgentProtocol interface definition for dependency inversion |
| `app_factory.py` | NEW - create_app() factory function that accepts agent implementation |
| `orchestrator/executor.py` | Added agent_stream_func parameter injection |
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
CONFIG_PATH=/etc/edp/a2a.env python main.py

# Docker
docker run -e CONFIG_PATH=/app/config/.env ...
```

### Factory Mode Usage

New mode (dependency inversion):
```python
from agent_runtime.a2a_service.app_factory import create_app
from my_agent import initialize, agent_stream

app = create_app(
    agent_initializer=initialize,
    agent_stream_func=agent_stream,
)
uvicorn.run(app, host="0.0.0.0", port=8090)
```

Legacy mode (backward compatible):
```bash
python main.py  # Uses agents.EDPAgent by default
```

Enable new mode:
```bash
USE_FACTORY_MODE=true python main.py
```

### Executor Changes

Executor now accepts agent_stream_func as parameter:
```python
executor = Executor(
    va_client=va_client,
    redis=redis,
    task_store=task_store,
    agent_stream_func=agent_stream,  # NEW parameter
)
```

### Breaking Changes

None - backward compatible by default. Legacy mode uses original hardcoded imports.

### Migration Path

1. Phase 1: Use legacy mode (current behavior unchanged)
2. Phase 2: Set USE_FACTORY_MODE=true, inject agent from external package
3. Phase 3: Deprecate agents/EDPAgent/ directory, use community EDPAgent only