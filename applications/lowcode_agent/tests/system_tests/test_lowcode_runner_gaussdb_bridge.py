# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

import subprocess
import sys
import textwrap
import unittest
from pathlib import Path


def _run_runner_and_capture_db_type(runtime_db_type: str, studio_db_type: str) -> dict[str, str]:
    repo_root = Path(__file__).resolve().parents[4]
    runner_path = (
        repo_root
        / "applications"
        / "lowcode_agent"
        / "openjiuwen_runtime"
        / "examples"
        / "lowcode_agent"
        / "lowcode_agent_runner.py"
    )

    script = textwrap.dedent(
        f"""
        import os
        import runpy
        import sys
        import types

        class DummyAgentApp:
            def __init__(self, *args, **kwargs):
                self.args = args
                self.kwargs = kwargs

            def __getattr__(self, _name):
                def _decorator(func):
                    return func
                return _decorator

        def install_package(name):
            mod = types.ModuleType(name)
            mod.__path__ = []
            sys.modules[name] = mod
            return mod

        def install_module(name, **attrs):
            mod = types.ModuleType(name)
            for k, v in attrs.items():
                setattr(mod, k, v)
            sys.modules[name] = mod
            return mod

        install_package("openjiuwen")
        install_package("openjiuwen.core")
        install_package("openjiuwen.core.application")
        install_module(
            "openjiuwen.core.application.llm_agent",
            LLMAgent=type("LLMAgent", (), {{}}),
            ReActAgentConfig=type("ReActAgentConfig", (), {{}}),
        )
        install_module(
            "openjiuwen.core.application.workflow_agent",
            WorkflowAgent=type("WorkflowAgent", (), {{}}),
        )
        install_module("openjiuwen.core.runner", Runner=type("Runner", (), {{}}))
        install_package("openjiuwen.core.single_agent")
        install_module(
            "openjiuwen.core.single_agent.legacy",
            WorkflowAgentConfig=type("WorkflowAgentConfig", (), {{}}),
        )

        install_package("openjiuwen_runtime")
        install_package("openjiuwen_runtime.examples")
        install_package("openjiuwen_runtime.examples.lowcode_agent")
        install_module(
            "openjiuwen_runtime.examples.lowcode_agent.agui_converter",
            agui_append_text_and_finish_events=lambda *a, **k: [],
            agui_assistant_text_as_answer_events=lambda *a, **k: [],
            agui_error_events=lambda *a, **k: [],
            agui_trace_context=lambda *a, **k: [],
            convert_chunk_to_agui_events=lambda *a, **k: [],
            finalize_agui_stream=lambda *a, **k: [],
            flush_buffered_agui_text_events=lambda *a, **k: [],
            merge_agui_events_for_stream=lambda *a, **k: [],
        )
        install_module(
            "openjiuwen_runtime.examples.lowcode_agent.workflow_registration",
            normalize_workflow_providers_for_agent=lambda *a, **k: None,
        )

        install_package("openjiuwen_runtime.service")
        install_package("openjiuwen_runtime.service.app")
        install_module(
            "openjiuwen_runtime.service.app.agent_app",
            AgentApp=DummyAgentApp,
        )

        install_package("openjiuwen_studio")
        install_package("openjiuwen_studio.core")
        install_package("openjiuwen_studio.core.executor")
        install_package("openjiuwen_studio.core.executor.component")
        install_package("openjiuwen_studio.core.executor.component.code_runner")
        install_module(
            "openjiuwen_studio.core.executor.component.code_runner.remote",
            remote_code_runner=types.SimpleNamespace(code_sandbox_url=""),
        )

        lowcode_pkg = install_package("openjiuwen_studio.lowcode")
        lowcode_pkg.AgentCompiler = type("AgentCompiler", (), {{}})
        install_module(
            "openjiuwen_studio.lowcode.config_adapter",
            ConfigAdapter=type("ConfigAdapter", (), {{}}),
        )
        install_module(
            "openjiuwen_studio.lowcode.runtime_workflow_runner",
            RuntimeWorkflowRunner=type("RuntimeWorkflowRunner", (), {{}}),
        )

        os.environ["DB_TYPE"] = {runtime_db_type!r}
        os.environ["LOWCODE_STUDIO_DB_TYPE"] = {studio_db_type!r}

        g = runpy.run_path({str(runner_path)!r}, run_name="__lowcode_test__")

        print("RUNTIME_DB_TYPE=" + g.get("_runtime_db_type", ""))
        print("STUDIO_DB_TYPE=" + g.get("_studio_db_type", ""))
        print("FINAL_DB_TYPE=" + os.environ.get("DB_TYPE", ""))
        """
    )

    result = subprocess.run(
        [sys.executable, "-c", script],
        text=True,
        capture_output=True,
    )

    if result.returncode != 0:
        raise AssertionError(
            "lowcode runner subprocess failed\n"
            f"returncode={result.returncode}\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )

    output = {}
    for line in result.stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            output[key.strip()] = value.strip()
    return output


class TestLowcodeRunnerGaussdbBridge(unittest.TestCase):
    def test_gaussdb_runtime_overrides_to_studio_supported_db_type(self):
        data = _run_runner_and_capture_db_type("gaussdb", "mysql")

        self.assertEqual(data["RUNTIME_DB_TYPE"], "gaussdb")
        self.assertEqual(data["STUDIO_DB_TYPE"], "mysql")
        self.assertEqual(data["FINAL_DB_TYPE"], "mysql")

    def test_non_gauss_runtime_keeps_runtime_db_type(self):
        data = _run_runner_and_capture_db_type("mysql", "none")

        self.assertEqual(data["RUNTIME_DB_TYPE"], "mysql")
        self.assertEqual(data["STUDIO_DB_TYPE"], "none")
        self.assertEqual(data["FINAL_DB_TYPE"], "mysql")
