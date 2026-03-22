import json

import pytest

from miso import Agent
from miso.runtime import Broth, ProviderTurnResult, ToolCall
from miso.toolkits import AskUserToolkit, TerminalToolkit
from miso.tools import ToolkitCatalogConfig, tool, Toolkit


def _tool_turn(call_id: str, name: str, arguments: dict[str, object]) -> ProviderTurnResult:
    return ProviderTurnResult(
        assistant_messages=[
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": call_id,
                        "function": {
                            "name": name,
                            "arguments": arguments,
                        },
                    }
                ],
            }
        ],
        tool_calls=[ToolCall(call_id=call_id, name=name, arguments=arguments)],
        final_text="",
        consumed_tokens=3,
    )


def _final_turn(text: str = "done") -> ProviderTurnResult:
    return ProviderTurnResult(
        assistant_messages=[{"role": "assistant", "content": text}],
        tool_calls=[],
        final_text=text,
        consumed_tokens=2,
    )


def _tool_payloads(messages: list[dict[str, object]]) -> list[dict[str, object]]:
    payloads: list[dict[str, object]] = []
    for message in messages:
        if isinstance(message, dict) and message.get("role") == "tool":
            payloads.append(json.loads(str(message.get("content", "{}"))))
    return payloads


def _selector_args() -> dict[str, object]:
    return {
        "title": "Confirm",
        "question": "Continue?",
        "selection_mode": "single",
        "options": [
            {"label": "Yes", "value": "yes"},
            {"label": "No", "value": "no"},
        ],
        "allow_other": False,
    }


def test_catalog_mode_is_opt_in_and_default_eager_behavior_is_unchanged():
    agent = Broth()
    agent.provider = "ollama"

    seen_tool_names: list[list[str]] = []

    def fake_fetch_once(**kwargs):
        seen_tool_names.append([item["name"] for item in kwargs["toolkit"].to_json()])
        return _final_turn()

    agent._fetch_once = fake_fetch_once

    _, bundle = agent.run(
        messages=[{"role": "user", "content": "hello"}],
        max_iterations=1,
    )

    assert seen_tool_names == [[]]
    assert bundle["status"] == "completed"


def test_catalog_activate_exposes_managed_tools_on_the_next_iteration():
    agent = Broth(
        toolkit_catalog_config={
            "managed_toolkit_ids": ["workspace"],
        }
    )
    agent.provider = "ollama"

    seen_tool_names: list[list[str]] = []
    state = {"turn": 0}

    def fake_fetch_once(**kwargs):
        seen_tool_names.append([item["name"] for item in kwargs["toolkit"].to_json()])
        state["turn"] += 1
        if state["turn"] == 1:
            return _tool_turn("call_1", "toolkit_activate", {"toolkit_id": "workspace"})
        return _final_turn()

    agent._fetch_once = fake_fetch_once

    messages, bundle = agent.run(
        messages=[{"role": "user", "content": "activate workspace"}],
        max_iterations=3,
    )

    assert "toolkit_activate" in seen_tool_names[0]
    assert "read_files" not in seen_tool_names[0]
    assert "read_files" in seen_tool_names[1]
    assert bundle["status"] == "completed"
    payloads = _tool_payloads(messages)
    assert payloads[0]["ok"] is True
    assert payloads[0]["toolkit_id"] == "workspace"


def test_catalog_deactivate_hides_managed_tools_on_the_next_iteration():
    agent = Broth(
        toolkit_catalog_config={
            "managed_toolkit_ids": ["workspace"],
        }
    )
    agent.provider = "ollama"

    seen_tool_names: list[list[str]] = []
    state = {"turn": 0}

    def fake_fetch_once(**kwargs):
        seen_tool_names.append([item["name"] for item in kwargs["toolkit"].to_json()])
        state["turn"] += 1
        if state["turn"] == 1:
            return _tool_turn("call_1", "toolkit_activate", {"toolkit_id": "workspace"})
        if state["turn"] == 2:
            return _tool_turn("call_2", "toolkit_deactivate", {"toolkit_id": "workspace"})
        return _final_turn()

    agent._fetch_once = fake_fetch_once

    _, bundle = agent.run(
        messages=[{"role": "user", "content": "toggle workspace"}],
        max_iterations=4,
    )

    assert "read_files" not in seen_tool_names[0]
    assert "read_files" in seen_tool_names[1]
    assert "read_files" not in seen_tool_names[2]
    assert bundle["status"] == "completed"


def test_catalog_always_active_toolkits_are_visible_and_cannot_be_deactivated():
    agent = Broth(
        toolkit_catalog_config={
            "managed_toolkit_ids": ["workspace"],
            "always_active_toolkit_ids": ["workspace"],
        }
    )
    agent.provider = "ollama"

    seen_tool_names: list[list[str]] = []
    state = {"turn": 0}

    def fake_fetch_once(**kwargs):
        seen_tool_names.append([item["name"] for item in kwargs["toolkit"].to_json()])
        state["turn"] += 1
        if state["turn"] == 1:
            return _tool_turn("call_1", "toolkit_deactivate", {"toolkit_id": "workspace"})
        return _final_turn()

    agent._fetch_once = fake_fetch_once

    messages, _ = agent.run(
        messages=[{"role": "user", "content": "disable workspace"}],
        max_iterations=3,
    )

    assert "read_files" in seen_tool_names[0]
    assert "read_files" in seen_tool_names[1]
    payloads = _tool_payloads(messages)
    assert "always_active" in payloads[0]["error"]


def test_catalog_activation_rejects_tool_name_collisions_with_eager_tools():
    agent = Broth(
        toolkit_catalog_config={
            "managed_toolkit_ids": ["terminal"],
        }
    )
    agent.provider = "ollama"
    eager_collision = tool(
        name="terminal_exec",
        func=lambda: {"ok": True},
        parameters=[],
    )
    agent.toolkit = Toolkit({eager_collision.name: eager_collision})

    state = {"turn": 0}

    def fake_fetch_once(**kwargs):
        state["turn"] += 1
        if state["turn"] == 1:
            return _tool_turn("call_1", "toolkit_activate", {"toolkit_id": "terminal"})
        return _final_turn()

    agent._fetch_once = fake_fetch_once

    messages, _ = agent.run(
        messages=[{"role": "user", "content": "activate terminal"}],
        max_iterations=3,
    )

    payloads = _tool_payloads(messages)
    assert payloads[0]["ok"] is False
    assert "terminal_exec" in payloads[0]["error"]


def test_catalog_describe_truncates_readme():
    agent = Broth(
        toolkit_catalog_config={
            "managed_toolkit_ids": ["workspace"],
            "readme_max_chars": 24,
        }
    )
    agent.provider = "ollama"

    state = {"turn": 0}

    def fake_fetch_once(**kwargs):
        state["turn"] += 1
        if state["turn"] == 1:
            return _tool_turn("call_1", "toolkit_describe", {"toolkit_id": "workspace"})
        return _final_turn()

    agent._fetch_once = fake_fetch_once

    messages, _ = agent.run(
        messages=[{"role": "user", "content": "describe workspace"}],
        max_iterations=3,
    )

    payloads = _tool_payloads(messages)
    assert payloads[0]["readme_truncated"] is True
    assert len(payloads[0]["readme_markdown"]) == 24


def test_catalog_returns_structured_errors_for_unknown_managed_ids():
    agent = Broth(
        toolkit_catalog_config={
            "managed_toolkit_ids": ["workspace"],
        }
    )
    agent.provider = "ollama"

    state = {"turn": 0}

    def fake_fetch_once(**kwargs):
        state["turn"] += 1
        if state["turn"] == 1:
            return _tool_turn("call_1", "toolkit_activate", {"toolkit_id": "terminal"})
        return _final_turn()

    agent._fetch_once = fake_fetch_once

    messages, _ = agent.run(
        messages=[{"role": "user", "content": "activate terminal"}],
        max_iterations=3,
    )

    payloads = _tool_payloads(messages)
    assert payloads[0]["ok"] is False
    assert payloads[0]["toolkit_id"] == "terminal"
    assert "unknown managed toolkit" in payloads[0]["error"]


def test_catalog_mode_keeps_anonymous_eager_toolkits_callable_but_excludes_them_from_catalog_listing():
    agent = Broth(
        toolkit_catalog_config={
            "managed_toolkit_ids": ["workspace"],
        }
    )
    agent.provider = "ollama"
    manual_tool = tool(name="hello_manual", func=lambda: {"hello": "world"}, parameters=[])
    agent.toolkit = Toolkit({manual_tool.name: manual_tool})

    seen_tool_names: list[list[str]] = []
    state = {"turn": 0}

    def fake_fetch_once(**kwargs):
        seen_tool_names.append([item["name"] for item in kwargs["toolkit"].to_json()])
        state["turn"] += 1
        if state["turn"] == 1:
            return _tool_turn("call_1", "toolkit_list", {})
        if state["turn"] == 2:
            return _tool_turn("call_2", "hello_manual", {})
        return _final_turn()

    agent._fetch_once = fake_fetch_once

    messages, _ = agent.run(
        messages=[{"role": "user", "content": "list and call"}],
        max_iterations=4,
    )

    assert "hello_manual" in seen_tool_names[0]
    payloads = _tool_payloads(messages)
    toolkit_ids = {item["id"] for item in payloads[0]["toolkits"]}
    assert "workspace" in toolkit_ids
    assert "hello_manual" not in toolkit_ids
    assert payloads[1] == {"hello": "world"}


def test_catalog_resume_preserves_cached_run_terminal_toolkit_instances_across_human_input_pause():
    agent = Broth(
        toolkit_catalog_config={
            "managed_toolkit_ids": ["terminal"],
        }
    )
    agent.provider = "ollama"
    agent.toolkit = AskUserToolkit()

    seen_tool_names: list[list[str]] = []
    state = {"turn": 0}
    session = {"id": None}

    def fake_fetch_once(**kwargs):
        seen_tool_names.append([item["name"] for item in kwargs["toolkit"].to_json()])
        state["turn"] += 1

        if state["turn"] == 1:
            return _tool_turn("call_1", "toolkit_activate", {"toolkit_id": "terminal"})

        if state["turn"] == 2:
            return _tool_turn(
                "call_2",
                "terminal_session_open",
                {"shell": "/bin/sh", "cwd": ".", "timeout_seconds": 60},
            )

        if state["turn"] == 3:
            for payload in _tool_payloads(kwargs["messages"]):
                if "session_id" in payload:
                    session["id"] = payload["session_id"]
            return _tool_turn("call_3", "ask_user_question", _selector_args())

        if state["turn"] == 4:
            return _tool_turn(
                "call_4",
                "terminal_session_write",
                {
                    "session_id": session["id"],
                    "input": "echo hi\nexit\n",
                    "yield_time_ms": 300,
                },
            )

        return _final_turn("done")

    agent._fetch_once = fake_fetch_once

    suspended_messages, suspended_bundle = agent.run(
        messages=[{"role": "user", "content": "open a shell"}],
        max_iterations=6,
    )

    assert suspended_bundle["status"] == "awaiting_human_input"
    assert suspended_bundle["continuation"]["toolkit_catalog"]["active_toolkit_ids"] == ["terminal"]
    assert session["id"] is not None

    resumed_messages, resumed_bundle = agent.resume_human_input(
        conversation=suspended_messages,
        continuation=suspended_bundle["continuation"],
        response={"request_id": "call_3", "selected_values": ["yes"]},
    )

    payloads = _tool_payloads(resumed_messages)
    write_payload = next(payload for payload in payloads if payload.get("session_id") == session["id"] and "ok" in payload)
    assert write_payload["ok"] is True
    assert "error" not in write_payload
    assert "terminal_session_write" in seen_tool_names[3]
    assert resumed_bundle["status"] == "completed"


def test_catalog_shutdown_is_called_for_managed_toolkits_on_completion(monkeypatch):
    shutdown_calls: list[str] = []

    def fake_shutdown(self):
        shutdown_calls.append("terminal")

    monkeypatch.setattr(TerminalToolkit, "shutdown", fake_shutdown)

    agent = Broth(
        toolkit_catalog_config={
            "managed_toolkit_ids": ["terminal"],
            "always_active_toolkit_ids": ["terminal"],
        }
    )
    agent.provider = "ollama"
    agent._fetch_once = lambda **kwargs: _final_turn()

    _, bundle = agent.run(
        messages=[{"role": "user", "content": "hello"}],
        max_iterations=1,
    )

    assert bundle["status"] == "completed"
    assert shutdown_calls == ["terminal"]


def test_agent_enable_toolkit_catalog_forwards_config_to_broth(monkeypatch):
    instances = []

    class FakeBroth:
        def __init__(
            self,
            provider=None,
            model=None,
            api_key=None,
            memory_manager=None,
            toolkit_catalog_config=None,
        ):
            self.provider = provider
            self.model = model
            self.api_key = api_key
            self.memory_manager = memory_manager
            self.toolkit_catalog_config = toolkit_catalog_config
            self.toolkit = None
            self.max_iterations = 6
            self.on_tool_confirm = None
            instances.append(self)

        def run(self, **kwargs):
            del kwargs
            return [{"role": "assistant", "content": "done"}], {"status": "completed"}

    monkeypatch.setattr("miso.agents.agent.Broth", FakeBroth)

    agent = Agent(name="planner")
    agent.enable_toolkit_catalog(managed_toolkit_ids=["workspace"])
    messages, bundle = agent.run(messages=[{"role": "user", "content": "hello"}], max_iterations=1)

    assert len(instances) == 1
    assert isinstance(instances[0].toolkit_catalog_config, ToolkitCatalogConfig)
    assert instances[0].toolkit_catalog_config.managed_toolkit_ids == ("workspace",)
    assert messages[-1]["content"] == "done"
    assert bundle["status"] == "completed"


def test_catalog_config_requires_managed_toolkits():
    with pytest.raises(ValueError, match="managed_toolkit_id"):
        ToolkitCatalogConfig(managed_toolkit_ids=[])
