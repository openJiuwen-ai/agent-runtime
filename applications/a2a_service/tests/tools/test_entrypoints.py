# coding: utf-8
from __future__ import annotations

from types import SimpleNamespace

import pytest

import main as main_module
from tools.simulate_router import simulate


def test_main_invokes_uvicorn_with_settings(monkeypatch):
    calls = []
    monkeypatch.setattr(
        main_module,
        "get_settings",
        lambda: SimpleNamespace(
            app_name="A2A",
            fastapi_host="127.0.0.1",
            fastapi_port=18090,
            fastapi_debug=True,
            fastapi_workers=4,
            log_level="INFO",
        ),
    )
    monkeypatch.setattr(main_module.uvicorn, "run", lambda *args, **kwargs: calls.append((args, kwargs)))

    main_module.main()

    assert calls[0][0] == ("app:app",)
    assert calls[0][1]["workers"] == 1
    assert calls[0][1]["reload"] is True
    assert calls[0][1]["log_level"] == "info"


@pytest.mark.asyncio
async def test_simulate_root_reads_html_and_fallback(monkeypatch, tmp_path):
    html = tmp_path / "index.html"
    html.write_text("<h1>ok</h1>", encoding="utf-8")
    monkeypatch.setattr(simulate, "INDEX_HTML_PATH", str(html))

    assert await simulate.root() == "<h1>ok</h1>"

    monkeypatch.setattr(simulate, "INDEX_HTML_PATH", str(tmp_path / "missing.html"))
    assert await simulate.root() == "<h1>Simulate HTML not found.</h1>"
