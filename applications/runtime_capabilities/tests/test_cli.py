# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Tests for the application command-line launcher."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from runtime_capabilities.cli import main


@pytest.mark.unit
def test_cli_loads_environment_and_starts_uvicorn(tmp_path: Path):
    env_file = tmp_path / "runtime-capabilities.env"
    env_file.write_text(
        "\n".join(
            [
                "RUNTIME_CAPABILITIES_SERVICE_LABEL=cli-test",
                "RUNTIME_CAPABILITIES_KUBERNETES_NAMESPACE=cli-test",
                "RUNTIME_CAPABILITIES_KUBERNETES_POD_IMAGE=example:test",
                f"RUNTIME_CAPABILITIES_SQLITE_PATH={tmp_path / 'cli.db'}",
            ]
        ),
        encoding="utf-8",
    )

    with (
        patch.dict(os.environ, {}, clear=True),
        patch("uvicorn.run") as run,
    ):
        main(
            [
                "--mode",
                "local",
                "--env-file",
                str(env_file),
                "--host",
                "127.0.0.2",
                "--port",
                "18090",
            ]
        )

    run.assert_called_once()
    assert run.call_args.kwargs == {
        "host": "127.0.0.2",
        "port": 18090,
        "workers": 1,
    }


@pytest.mark.unit
def test_cli_rejects_missing_environment_file(tmp_path: Path):
    with pytest.raises(FileNotFoundError, match="environment file does not exist"):
        main(
            [
                "--mode",
                "local",
                "--env-file",
                str(tmp_path / "missing.env"),
            ]
        )
