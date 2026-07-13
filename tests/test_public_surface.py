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


def test_interaction_surface_exports_durable_contracts():
    from unchain.interaction import (
        InteractionAlreadyAppliedError,
        InteractionError,
        InteractionIntegrityError,
        InteractionNotPendingError,
        InteractionReceipt,
        InteractionReceiptConflictError,
        InteractionRequest,
    )

    assert InteractionRequest.__name__ == "InteractionRequest"
    assert InteractionReceipt.__name__ == "InteractionReceipt"
    assert issubclass(InteractionIntegrityError, InteractionError)
    assert issubclass(InteractionNotPendingError, InteractionError)
    assert issubclass(InteractionReceiptConflictError, InteractionError)
    assert issubclass(InteractionAlreadyAppliedError, InteractionError)


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


def test_legacy_focused_toolkit_classes_are_removed():
    import importlib
    import sys

    import pytest

    removed_modules = (
        "unchain.toolkits.builtin.interaction.interaction",
        "unchain.toolkits.builtin.web.web",
        "unchain.toolkits.builtin.workspace.backend",
        "unchain.toolkits.builtin.workspace.workspace",
    )
    removed_exports = (
        ("unchain.toolkits.builtin.interaction", "InteractionToolkit"),
        ("unchain.toolkits.builtin.web", "WebToolkit"),
        ("unchain.toolkits.builtin.workspace", "DevToolkit"),
        ("unchain.toolkits.builtin.workspace", "WorkspaceToolkit"),
    )

    for module_name in (*removed_modules, *(package_name for package_name, _ in removed_exports)):
        sys.modules.pop(module_name, None)

    for module_name in removed_modules:
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(module_name)

    for package_name, class_name in removed_exports:
        try:
            package = importlib.import_module(package_name)
        except ModuleNotFoundError:
            continue
        assert not hasattr(package, class_name)


def test_legacy_git_and_external_api_toolkit_classes_are_removed():
    import importlib
    import sys

    import pytest

    removed_modules = (
        "unchain.toolkits.builtin.external_api.external_api",
        "unchain.toolkits.builtin.git.git",
    )
    removed_exports = (
        ("unchain.toolkits.builtin.external_api", "ExternalAPIToolkit"),
        ("unchain.toolkits.builtin.git", "GitToolkit"),
    )

    for module_name in (*removed_modules, *(package_name for package_name, _ in removed_exports)):
        sys.modules.pop(module_name, None)

    for module_name in removed_modules:
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(module_name)

    for package_name, class_name in removed_exports:
        try:
            package = importlib.import_module(package_name)
        except ModuleNotFoundError:
            continue
        assert not hasattr(package, class_name)


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


def test_public_toolkit_reference_docs_include_all_current_public_toolkits():
    from pathlib import Path

    docs_root = Path(__file__).resolve().parents[1] / "docs"
    reference_docs = (
        "en/api/toolkits.md",
        "zh-CN/api/toolkits.md",
        "en/appendix/class-index.md",
        "zh-CN/appendix/class-index.md",
        "en/appendix/export-index.md",
        "zh-CN/appendix/export-index.md",
    )
    current_terms = (
        "CoreToolkit",
        "PlanToolkit",
        "AgentReachToolkit",
        "MCPToolkit",
    )

    for relative_path in reference_docs:
        text = (docs_root / relative_path).read_text(encoding="utf-8")
        for term in current_terms:
            assert term in text, f"{term!r} missing from {relative_path}"


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


def test_memory_surface_exports_session_history_ownership_error():
    from unchain.memory import SessionHistoryOwnershipError
    from unchain.memory.ownership import (
        SessionHistoryOwnershipError as InternalSessionHistoryOwnershipError,
    )

    assert SessionHistoryOwnershipError is InternalSessionHistoryOwnershipError
    assert issubclass(SessionHistoryOwnershipError, ValueError)
    assert SessionHistoryOwnershipError.code == "session_history_ownership_conflict"


def test_memory_surface_exports_revision_and_commit_contracts():
    from unchain.memory import (
        MemoryCommitResult,
        RevisionedSessionStore,
        SessionRevisionConflictError,
        SessionSnapshot,
        SessionStoreCorruptionError,
        load_session_snapshot,
        save_session_snapshot,
    )
    from unchain.memory.manager import MemoryCommitResult as InternalCommitResult
    from unchain.memory.revision import SessionSnapshot as InternalSessionSnapshot

    assert MemoryCommitResult is InternalCommitResult
    assert SessionSnapshot is InternalSessionSnapshot
    assert RevisionedSessionStore.__name__ == "RevisionedSessionStore"
    assert SessionRevisionConflictError.code == "session_revision_conflict"
    assert SessionStoreCorruptionError.code == "session_store_corruption"
    assert callable(load_session_snapshot)
    assert callable(save_session_snapshot)


def test_memory_surface_exports_execution_checkpoint_errors():
    from unchain.memory import (
        ExecutionCheckpointError,
        ExecutionCheckpointIntegrityError,
        ExecutionCheckpointPersistenceError,
        ExecutionCheckpointReplayUnavailableError,
        ExecutionCheckpointResumeRequiredError,
    )
    from unchain.memory.checkpoint_state import (
        ExecutionCheckpointError as InternalExecutionCheckpointError,
    )

    assert ExecutionCheckpointError is InternalExecutionCheckpointError
    assert issubclass(ExecutionCheckpointIntegrityError, ExecutionCheckpointError)
    assert issubclass(ExecutionCheckpointPersistenceError, ExecutionCheckpointError)
    assert issubclass(ExecutionCheckpointReplayUnavailableError, ExecutionCheckpointError)
    assert issubclass(ExecutionCheckpointResumeRequiredError, ExecutionCheckpointError)


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
