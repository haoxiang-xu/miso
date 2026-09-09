from __future__ import annotations

from types import SimpleNamespace

import pytest

from unchain.agent import AgentBuilder, AgentCallContext, AgentSpec, AgentState
from unchain.agent.model_io import ModelIOFactoryRegistry
from unchain.agent.modules import ContextModule
from unchain.context import (
    ContextCompileRequest,
    ContextCompiler,
    ContextRuntime,
    DurableToolExecutorContractError,
    resolve_context_budget,
)
from unchain.interaction.durable import InteractionIntegrityError
from unchain.kernel import ModelTurnResult
from unchain.kernel.types import ToolCall
from unchain.tools import Toolkit
from unchain.tools.runtime import (
    ToolRuntimeOutcome,
    run_tool_runtime_plugins,
    snapshot_durable_tool_runtime_route,
)


class _ToolThenFinalModelIO:
    provider = "openai"
    model = "gpt-authority-test"

    def __init__(self) -> None:
        self.requests = []

    def fetch_turn(self, request):
        self.requests.append(request)
        if len(self.requests) == 1:
            call = ToolCall(
                call_id="call-authority",
                name="authority_probe",
                arguments={},
            )
            return ModelTurnResult(
                assistant_messages=[
                    {
                        "type": "function_call",
                        "call_id": call.call_id,
                        "name": call.name,
                        "arguments": call.arguments,
                    }
                ],
                tool_calls=[call],
                response_id="response-tool",
            )
        if len(self.requests) == 2:
            return ModelTurnResult(
                assistant_messages=[{"role": "assistant", "content": "done"}],
                tool_calls=[],
                final_text="done",
                response_id="response-final",
            )
        raise AssertionError("unexpected model turn")


def _context_request(context) -> ContextCompileRequest:
    run_id = str(context.event.get("run_id") or "authority-run")
    execution_id = str(context.state.session_state.session_id or run_id)
    return ContextCompileRequest(
        case="production-tool-authority",
        source_messages=tuple(context.latest_messages()),
        current_generation=f"generation-{execution_id}",
        fixed_overhead_tokens=0,
        budget=resolve_context_budget(context_window_tokens=16_384),
        provider="openai",
        model="gpt-authority-test",
        build_id=f"build-{run_id}-{context.state.iteration}",
        execution_id=execution_id,
        generation_id=f"generation-{execution_id}",
        attempt_id=run_id,
    )


def _context_runtime() -> ContextRuntime:
    return ContextRuntime._for_test(
        owner_id="context-v2",
        compiler=ContextCompiler(),
        request_factory=_context_request,
        durable_event_sink=lambda event: None,
        partial_attempt_sink=lambda event, error: None,
    )


def _prepared_authority_agent(
    *,
    model_io: _ToolThenFinalModelIO,
    runtime: ContextRuntime,
    plugins=(),
):
    toolkit = Toolkit()
    toolkit.register(
        lambda: {"source": "ordinary-toolkit-route"},
        name="authority_probe",
    )
    builder = AgentBuilder(
        agent=SimpleNamespace(name="authority-agent"),
        spec=AgentSpec(
            name="authority-agent",
            provider="openai",
            model="gpt-authority-test",
        ),
        state=AgentState(),
        call_context=AgentCallContext(
            mode="run",
            input_messages=[{"role": "user", "content": "run authority probe"}],
            session_id="authority-session",
            run_id="authority-run",
            max_iterations=2,
            max_context_window_tokens=16_384,
        ),
        model_io_registry=ModelIOFactoryRegistry(),
        toolkit=toolkit,
    )
    builder.set_model_io(model_io)
    ContextModule(runtime=runtime).configure(builder)
    for plugin in plugins:
        builder.add_tool_runtime_plugin(plugin)
    return builder.build()


def test_declared_terminal_plugin_cannot_fall_through_to_unmanifested_plugin():
    executed = []

    class DeclaredTerminalPlugin:
        def can_handle(self, *, tool_call, context):
            del tool_call, context
            return True

        def durable_runtime_manifest(self, *, tool_call, context):
            del tool_call, context
            return {"handler": "declared-terminal", "terminal_handler": True}

        def execute(self, *, tool_call, context):
            del tool_call, context
            executed.append("declared-terminal")
            return ToolRuntimeOutcome(handled=False)

    class HiddenUnmanifestedPlugin:
        def can_handle(self, *, tool_call, context):
            del tool_call, context
            return True

        def execute(self, *, tool_call, context):
            del tool_call, context
            executed.append("hidden-unmanifested")
            return ToolRuntimeOutcome(tool_result={"route": "hidden"})

    plugins = [DeclaredTerminalPlugin(), HiddenUnmanifestedPlugin()]
    tool_call = ToolCall(call_id="call-route", name="authority_probe", arguments={})
    context = SimpleNamespace()

    try:
        route = snapshot_durable_tool_runtime_route(
            plugins,
            tool_call=tool_call,
            context=context,
        )
        assert route is not None
        run_tool_runtime_plugins(
            plugins,
            tool_call=tool_call,
            context=context,
        )
    except InteractionIntegrityError:
        pass

    assert "hidden-unmanifested" not in executed


def test_plugin_result_messages_without_durable_completion_never_reach_model():
    marker = "UNAUTHORIZED_PLUGIN_RESULT_MESSAGE"

    class ResultMessagePlugin:
        def can_handle(self, *, tool_call, context):
            del tool_call, context
            return True

        def execute(self, *, tool_call, context):
            del context
            return ToolRuntimeOutcome(
                result_messages=[
                    {
                        "type": "function_call_output",
                        "call_id": tool_call.call_id,
                        "output": marker,
                    }
                ]
            )

    model_io = _ToolThenFinalModelIO()
    result = None
    try:
        result = _prepared_authority_agent(
            model_io=model_io,
            runtime=_context_runtime(),
            plugins=(ResultMessagePlugin(),),
        ).run()
    except (DurableToolExecutorContractError, InteractionIntegrityError):
        pass

    assert marker not in repr([request.messages for request in model_io.requests])
    if result is not None:
        assert marker not in repr(result.messages)


def test_v2_production_tool_path_delegates_to_context_runtime_authority(
    monkeypatch,
):
    class AuthorityGateReached(RuntimeError):
        pass

    calls = []

    def require_context_runtime_authority(
        self,
        context,
    ):
        calls.append((self, context))
        raise AuthorityGateReached

    monkeypatch.setattr(
        ContextRuntime,
        "prepare_tool_execution",
        require_context_runtime_authority,
    )
    model_io = _ToolThenFinalModelIO()

    with pytest.raises(AuthorityGateReached):
        _prepared_authority_agent(
            model_io=model_io,
            runtime=_context_runtime(),
        ).run()

    assert len(calls) == 1
