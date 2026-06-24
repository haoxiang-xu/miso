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
    from unchain.toolkits import (
        AgentReachToolkit,
        CoreToolkit,
        ExternalAPIToolkit,
        GitToolkit,
        MCPToolkit,
        PlanToolkit,
    )

    assert CoreToolkit.__name__ == "CoreToolkit"
    assert GitToolkit.__name__ == "GitToolkit"
    assert PlanToolkit.__name__ == "PlanToolkit"
    assert ExternalAPIToolkit.__name__ == "ExternalAPIToolkit"
    assert AgentReachToolkit.__name__ == "AgentReachToolkit"
    assert MCPToolkit.__name__ == "MCPToolkit"


def test_toolkits_surface_exports_workspace_compatibility_toolkit(tmp_path):
    from unchain.toolkits import DevToolkit, WorkspaceToolkit

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
