from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

from unchain.agent import Agent, ToolDiscoveryModule
from unchain.kernel import KernelLoop, ModelTurnResult
from unchain.kernel.types import ToolCall as KernelToolCall
from unchain.tools import ToolDiscoveryConfig, ToolDiscoveryRuntime, Toolkit


def _write_icon(path: Path) -> None:
    path.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16">'
        '<rect width="16" height="16" rx="3" fill="#111827"/>'
        '<circle cx="8" cy="8" r="4" fill="#f9fafb"/>'
        "</svg>",
        encoding="utf-8",
    )


def _write_deferred_toolkit_package(root: Path) -> str:
    package_name = "lazy_demo_toolkit_pkg"
    package_root = root / package_name
    package_root.mkdir(parents=True, exist_ok=True)
    (package_root / "README.md").write_text("# Demo Toolkit\n", encoding="utf-8")
    _write_icon(package_root / "icon.svg")
    (package_root / "__init__.py").write_text(
        "from .demo import DemoToolkit, shutdown_calls\n",
        encoding="utf-8",
    )
    (package_root / "demo.py").write_text(
        "from unchain.tools import Toolkit\n\n"
        "shutdown_calls = 0\n\n"
        "class DemoToolkit(Toolkit):\n"
        "    def __init__(self):\n"
        "        super().__init__()\n"
        "        self.register(lambda: 'discovered', name='discover', description='Deferred demo tool.')\n\n"
        "    def shutdown(self):\n"
        "        global shutdown_calls\n"
        "        shutdown_calls += 1\n",
        encoding="utf-8",
    )
    (package_root / "toolkit.toml").write_text(
        f"""
[toolkit]
id = "demo"
name = "Demo Toolkit"
description = "Deferred tools for discovery tests."
factory = "{package_name}:DemoToolkit"
version = "0.1.0"
readme = "README.md"
icon = "icon.svg"
color = "#0f172a"
backgroundcolor = "#dbeafe"
tags = ["deferred", "demo"]

[display]
category = "local"
order = 1
hidden = false

[compat]
python = ">=3.9"
legacy = ">=0"

[[tools]]
name = "discover"
title = "Discover"
description = "Deferred demo tool."
observe = false
requires_confirmation = false
""".strip()
        + "\n",
        encoding="utf-8",
    )
    for module_name in [package_name, f"{package_name}.demo"]:
        sys.modules.pop(module_name, None)
    return package_name


def _build_discovery_config(root: Path) -> ToolDiscoveryConfig:
    _write_deferred_toolkit_package(root)
    return ToolDiscoveryConfig(
        managed_toolkit_ids=("demo",),
        registry={
            "local_roots": [str(root)],
            "include_builtin": False,
        },
    )


def test_tool_discovery_runtime_can_search_load_and_list_tools(tmp_path):
    config = _build_discovery_config(tmp_path)
    runtime_toolkit = Toolkit()
    runtime = ToolDiscoveryRuntime(config=config, runtime_toolkit=runtime_toolkit)

    search = runtime.tool_search("discover")
    assert search["matches"][0]["handle"] == "demo:discover"
    assert search["matches"][0]["tool_name"] == "discover"

    load = runtime.tool_load(["demo:discover"])
    assert [item["handle"] for item in load["loaded"]] == ["demo:discover"]
    assert load["failed"] == []
    assert runtime_toolkit.execute("discover", {}) == {"result": "discovered"}
    assert runtime.tool_list_loaded()["loaded"][0]["handle"] == "demo:discover"

    runtime.shutdown()
    imported = importlib.import_module("lazy_demo_toolkit_pkg.demo")
    assert imported.shutdown_calls == 1


def test_tool_discovery_runtime_reports_name_conflicts(tmp_path):
    config = _build_discovery_config(tmp_path)
    runtime_toolkit = Toolkit()
    runtime_toolkit.register(lambda: "active", name="discover", description="Already active tool.")
    runtime = ToolDiscoveryRuntime(config=config, runtime_toolkit=runtime_toolkit)

    load = runtime.tool_load(["demo:discover"])

    assert load["loaded"] == []
    assert load["already_loaded"] == []
    assert load["failed"][0]["error"] == "tool name conflict: discover"


def test_tool_prompt_harness_inserts_and_replaces_tools_block():
    loop = KernelLoop()
    state = loop.seed_state(
        [
            {"role": "system", "content": "base system"},
            {"role": "user", "content": "hello"},
        ],
        provider="openai",
        model="gpt-4.1",
    )
    loop._ensure_runtime_harnesses()

    toolkit_one = Toolkit()
    toolkit_one.register(lambda text=None: {"text": text}, name="alpha", description="Alpha tool.")
    loop.dispatch_phase(state, phase="before_model", event={"toolkit": toolkit_one})
    first_messages = state.latest_messages()
    tools_blocks = [
        message for message in first_messages if message.get("role") == "system" and "<tools>" in str(message.get("content"))
    ]
    assert len(tools_blocks) == 1
    assert "alpha" in tools_blocks[0]["content"]
    assert first_messages[0] == {"role": "system", "content": "base system"}

    loop.dispatch_phase(state, phase="before_model", event={"toolkit": toolkit_one})
    repeated_messages = state.latest_messages()
    assert sum(1 for message in repeated_messages if message.get("role") == "system" and "<tools>" in str(message.get("content"))) == 1

    toolkit_two = Toolkit()
    toolkit_two.register(lambda text=None: {"text": text}, name="beta", description="Beta tool.")
    loop.dispatch_phase(state, phase="before_model", event={"toolkit": toolkit_two})
    replaced_block = next(
        message["content"]
        for message in state.latest_messages()
        if message.get("role") == "system" and "<tools>" in str(message.get("content"))
    )
    assert "beta" in replaced_block
    assert "alpha" not in replaced_block


def test_tool_discovery_module_loads_tools_into_later_turns_and_shuts_down(tmp_path):
    package_name = _write_deferred_toolkit_package(tmp_path)
    config = ToolDiscoveryConfig(
        managed_toolkit_ids=("demo",),
        registry={
            "local_roots": [str(tmp_path)],
            "include_builtin": False,
        },
    )

    class FakeModelIO:
        provider = "openai"
        model = "gpt-4.1"

        def __init__(self):
            self.calls = 0
            self.requests = []

        def fetch_turn(self, request):
            self.calls += 1
            self.requests.append(request)

            if self.calls == 1:
                assert "tool_search" in request.toolkit.tools
                assert "tool_load" in request.toolkit.tools
                assert "tool_list_loaded" in request.toolkit.tools
                assert "discover" not in request.toolkit.tools
                assert any(
                    message.get("role") == "system"
                    and "tool_search" in str(message.get("content"))
                    for message in request.messages
                )
                return ModelTurnResult(
                    assistant_messages=[
                        {
                            "type": "function_call",
                            "call_id": "call_search",
                            "name": "tool_search",
                            "arguments": json.dumps({"query": "discover"}),
                        }
                    ],
                    tool_calls=[KernelToolCall(call_id="call_search", name="tool_search", arguments={"query": "discover"})],
                    response_id="resp_1",
                )

            if self.calls == 2:
                assert "discover" not in request.toolkit.tools
                return ModelTurnResult(
                    assistant_messages=[
                        {
                            "type": "function_call",
                            "call_id": "call_load",
                            "name": "tool_load",
                            "arguments": json.dumps({"handles": ["demo:discover"]}),
                        }
                    ],
                    tool_calls=[
                        KernelToolCall(call_id="call_load", name="tool_load", arguments={"handles": ["demo:discover"]})
                    ],
                    response_id="resp_2",
                )

            assert self.calls == 3
            assert "discover" in request.toolkit.tools
            assert any(
                message.get("role") == "system"
                and "discover" in str(message.get("content"))
                for message in request.messages
            )
            return ModelTurnResult(
                assistant_messages=[{"role": "assistant", "content": "loaded"}],
                tool_calls=[],
                final_text="loaded",
                response_id="resp_3",
            )

    model_io = FakeModelIO()
    agent = Agent(
        name="tool-discovery-demo",
        provider="openai",
        model="gpt-4.1",
        modules=(ToolDiscoveryModule(config=config),),
        model_io_factory=lambda spec, ctx: model_io,
    )

    result = agent.run("load a deferred tool", max_iterations=4, payload={"store": False})

    assert result.status == "completed"
    assert result.messages[-1] == {"role": "assistant", "content": "loaded"}
    imported = importlib.import_module(f"{package_name}.demo")
    assert imported.shutdown_calls == 1
