def test_agent_surface_exports_runtime_modules():
    from unchain.agent import (
        Agent,
        MemoryModule,
        OptimizersModule,
        PoliciesModule,
        SubagentModule,
        ToolsModule,
    )

    assert Agent.__name__ == "Agent"
    assert ToolsModule.__name__ == "ToolsModule"
    assert MemoryModule.__name__ == "MemoryModule"
    assert PoliciesModule.__name__ == "PoliciesModule"
    assert OptimizersModule.__name__ == "OptimizersModule"
    assert SubagentModule.__name__ == "SubagentModule"


def test_tools_surface_exports_runtime_contracts():
    from unchain.tools import (
        Tool,
        ToolConfirmationRequest,
        ToolConfirmationResponse,
        ToolExecutionContext,
        Toolkit,
    )

    assert Tool.__name__ == "Tool"
    assert Toolkit.__name__ == "Toolkit"
    assert ToolExecutionContext.__name__ == "ToolExecutionContext"
    assert ToolConfirmationRequest.__name__ == "ToolConfirmationRequest"
    assert ToolConfirmationResponse.__name__ == "ToolConfirmationResponse"


def test_toolkits_surface_exports_current_concrete_toolkits():
    import unchain.toolkits as toolkits
    from unchain.toolkits import AgentReachToolkit, CoreToolkit, MCPToolkit, PlanToolkit

    assert CoreToolkit.__name__ == "CoreToolkit"
    assert PlanToolkit.__name__ == "PlanToolkit"
    assert AgentReachToolkit.__name__ == "AgentReachToolkit"
    assert MCPToolkit.__name__ == "MCPToolkit"
    assert sorted(toolkits.__all__) == [
        "AgentReachToolkit",
        "BuiltinToolkit",
        "CoreToolkit",
        "MCPToolkit",
        "PlanToolkit",
    ]
    for legacy_name in (
        "DevToolkit",
        "ExternalAPIToolkit",
        "GitToolkit",
        "InteractionToolkit",
        "WebToolkit",
        "WorkspaceToolkit",
    ):
        assert not hasattr(toolkits, legacy_name)


def test_workspace_compatibility_toolkit_stays_internal(tmp_path):
    from unchain.toolkits.builtin.workspace.workspace import DevToolkit, WorkspaceToolkit

    toolkit = WorkspaceToolkit(workspace_root=tmp_path)

    assert WorkspaceToolkit.__name__ == "WorkspaceToolkit"
    assert DevToolkit.__name__ == "DevToolkit"
    assert {
        "read_file",
        "write_file",
        "delete_file",
        "move_file",
        "terminal_exec",
        "pin_file_context",
        "unpin_file_context",
    }.issubset(toolkit.tools)
    assert toolkit.tools["write_file"].requires_confirmation is True
    assert toolkit.tools["delete_file"].requires_confirmation is True
    assert toolkit.tools["move_file"].requires_confirmation is True
    assert toolkit.tools["terminal_exec"].requires_confirmation is True
    for toolkit_cls in (WorkspaceToolkit, DevToolkit):
        assert toolkit_cls.__unchain_public_builtin__ is False
        assert toolkit_cls.__unchain_legacy_compat__ is True


def test_focused_interaction_and_web_toolkits_stay_internal(tmp_path):
    from unchain.toolkits.builtin.interaction import InteractionToolkit
    from unchain.toolkits.builtin.web import WebToolkit

    interaction_toolkit = InteractionToolkit(workspace_root=tmp_path)
    web_toolkit = WebToolkit(workspace_root=tmp_path)

    assert InteractionToolkit.__name__ == "InteractionToolkit"
    assert WebToolkit.__name__ == "WebToolkit"
    assert set(interaction_toolkit.tools) == {"ask_user_question"}
    assert set(web_toolkit.tools) == {"web_fetch"}
    assert web_toolkit.tools["web_fetch"].requires_confirmation is True
    for toolkit_cls in (InteractionToolkit, WebToolkit):
        assert toolkit_cls.__unchain_public_builtin__ is False
        assert toolkit_cls.__unchain_legacy_compat__ is True


def test_legacy_git_and_external_api_toolkits_stay_internal_compat():
    from unchain.toolkits.builtin.external_api import ExternalAPIToolkit
    from unchain.toolkits.builtin.git import GitToolkit

    for toolkit_cls in (ExternalAPIToolkit, GitToolkit):
        assert toolkit_cls.__unchain_public_builtin__ is False
        assert toolkit_cls.__unchain_legacy_compat__ is True


def test_public_toolkit_reference_docs_exclude_legacy_compat_toolkits():
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[1]
    public_reference_docs = (
        "README.md",
        "en/api/toolkits.md",
        "zh-CN/api/toolkits.md",
        "en/appendix/class-index.md",
        "zh-CN/appendix/class-index.md",
        "en/appendix/export-index.md",
        "zh-CN/appendix/export-index.md",
    )
    legacy_terms = (
        "GitToolkit",
        "ExternalAPIToolkit",
        "src/unchain/toolkits/builtin/git",
        "src/unchain/toolkits/builtin/external_api",
    )

    for relative_path in public_reference_docs:
        docs_path = repo_root / relative_path
        if not docs_path.exists():
            docs_path = repo_root / "docs" / relative_path
        text = docs_path.read_text(encoding="utf-8")
        for term in legacy_terms:
            assert term not in text, f"{term!r} leaked into {relative_path}"


def test_completion_policy_reference_docs_state_opt_in_runtime_boundary():
    from pathlib import Path

    docs_root = Path(__file__).resolve().parents[1] / "docs"
    runtime_docs = (
        "en/api/runtime.md",
        "zh-CN/api/runtime.md",
    )
    agent_docs = (
        "en/api/agents.md",
        "zh-CN/api/agents.md",
        "en/skills/agent-and-team.md",
        "zh-CN/skills/agent-and-team.md",
    )

    for relative_path in runtime_docs:
        text = (docs_root / relative_path).read_text(encoding="utf-8")
        assert "CompletionPolicy" in text, f"CompletionPolicy missing from {relative_path}"
        assert "CompletionPolicyRunner" in text, f"CompletionPolicyRunner missing from {relative_path}"
        assert "opt-in" in text or "显式启用" in text, f"opt-in boundary missing from {relative_path}"

    for relative_path in agent_docs:
        text = (docs_root / relative_path).read_text(encoding="utf-8")
        assert "completion_policy" in text, f"completion_policy missing from {relative_path}"
        assert "PoliciesModule" in text, f"PoliciesModule missing from {relative_path}"


def test_web_toolkit_does_not_construct_core_toolkit_bundle(monkeypatch, tmp_path):
    import unchain.toolkits.builtin.web.web as web_module
    from unchain.toolkits.builtin.web import WebToolkit

    def fail_core_toolkit(*args, **kwargs):
        raise AssertionError("WebToolkit should not construct the CoreToolkit bundle")

    monkeypatch.setattr(web_module, "CoreToolkit", fail_core_toolkit, raising=False)

    toolkit = WebToolkit(workspace_root=tmp_path)

    assert set(toolkit.tools) == {"web_fetch"}


def test_memory_surface_exports_pupu_runtime_dependencies():
    from unchain.memory import (
        JsonFileLongTermProfileStore,
        JsonFileSessionStore,
        LongTermMemoryConfig,
        MemoryConfig,
        MemoryManager,
        QdrantLongTermVectorAdapter,
        QdrantVectorAdapter,
        build_openai_embed_fn,
        collect_complete_turns_for_vector_index,
    )

    assert MemoryManager.__name__ == "MemoryManager"
    assert MemoryConfig.__name__ == "MemoryConfig"
    assert LongTermMemoryConfig.__name__ == "LongTermMemoryConfig"
    assert JsonFileLongTermProfileStore.__name__ == "JsonFileLongTermProfileStore"
    assert JsonFileSessionStore.__name__ == "JsonFileSessionStore"
    assert QdrantVectorAdapter.__name__ == "QdrantVectorAdapter"
    assert QdrantLongTermVectorAdapter.__name__ == "QdrantLongTermVectorAdapter"
    assert callable(build_openai_embed_fn)
    assert callable(collect_complete_turns_for_vector_index)


def test_memory_surface_exports_same_objects_as_current_internal_paths():
    from unchain.memory import (
        JsonFileLongTermProfileStore,
        JsonFileSessionStore,
        QdrantLongTermVectorAdapter,
        QdrantVectorAdapter,
        build_openai_embed_fn,
        collect_complete_turns_for_vector_index,
    )
    from unchain.memory.long_term import JsonFileLongTermProfileStore as InternalProfileStore
    from unchain.memory.manager import _collect_complete_turns_for_vector_index
    from unchain.memory.qdrant import (
        JsonFileSessionStore as InternalSessionStore,
        QdrantLongTermVectorAdapter as InternalLongTermVectorAdapter,
        QdrantVectorAdapter as InternalVectorAdapter,
        build_openai_embed_fn as internal_build_openai_embed_fn,
    )

    assert JsonFileLongTermProfileStore is InternalProfileStore
    assert JsonFileSessionStore is InternalSessionStore
    assert QdrantVectorAdapter is InternalVectorAdapter
    assert QdrantLongTermVectorAdapter is InternalLongTermVectorAdapter
    assert build_openai_embed_fn is internal_build_openai_embed_fn

    messages = [
        {"role": "system", "content": "setup"},
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
        {"role": "user", "content": "unfinished"},
    ]
    assert collect_complete_turns_for_vector_index(messages, start_index=0) == (
        _collect_complete_turns_for_vector_index(messages, start_index=0)
    )


def test_events_surface_exports_runtime_v4_types():
    from unchain.events import RuntimeEvent, RuntimeEventBridge

    assert RuntimeEvent.__name__ == "RuntimeEvent"
    assert RuntimeEventBridge.__name__ == "RuntimeEventBridge"


def test_interaction_surface_reexports_current_human_input_protocol():
    from unchain.input import HumanInputRequest as InputHumanInputRequest
    from unchain.input import HumanInputResponse as InputHumanInputResponse
    from unchain.interaction import (
        HumanInputRequest,
        HumanInputResponse,
        build_ask_user_question_tool,
    )

    assert HumanInputRequest is InputHumanInputRequest
    assert HumanInputResponse is InputHumanInputResponse
    assert callable(build_ask_user_question_tool)
