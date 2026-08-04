from __future__ import annotations

from types import SimpleNamespace

import pytest

from unchain.capabilities import CapabilityOutcome, RunDelta
from unchain.context.tool_boundary import DurableToolRouteKind
from unchain.context.tool_executor import (
    DurableToolCompletionDraft,
    DurableToolExecutorContractError,
)
from unchain.context.tool_routes import ContextToolRouteResolver
from unchain.input.human_input import ASK_USER_QUESTION_TOOL_NAME
from unchain.kernel.harness import HarnessContext
from unchain.kernel.state import RunState
from unchain.kernel.types import ToolCall
from unchain.subagents.types import SubagentState
from unchain.tools import Toolkit
from unchain.tools.runtime import ToolRuntimeOutcome


def _context(
    *,
    toolkit: Toolkit,
    tool_call: ToolCall,
    plugins: list[object] | None = None,
) -> HarnessContext:
    state = RunState()
    state.iteration = 3
    state.session_state.session_id = "execution-route"
    state.session_state.memory_namespace = "memory-route"
    state.provider_state.provider = "openai"
    state.provider_state.model = "gpt-route"
    return HarnessContext(
        state=state,
        phase="on_tool_call",
        event={
            "run_id": "attempt-route",
            "toolkit": toolkit,
            "tool_call": tool_call,
            "tool_runtime_plugins": list(plugins or []),
        },
    )


def test_normal_route_freezes_the_exposed_tool_and_canonical_manifests() -> None:
    calls: list[str] = []
    toolkit = Toolkit()

    @toolkit.tool(name="lookup", observe=True)
    def lookup(query: str):
        calls.append(query)
        return {"seen": query}

    tool_call = ToolCall(
        call_id="call-route",
        name="lookup",
        arguments={"query": "original"},
    )
    resolver = ContextToolRouteResolver()
    route = resolver.resolve(
        _context(toolkit=toolkit, tool_call=tool_call),
        tool_call,
    )

    assert route.route_kind is DurableToolRouteKind.NORMAL
    assert route.route_manifest["schema"] == "unchain.context_tool_route.v1"
    assert route.route_manifest["tool"]["name"] == "lookup"
    assert route.terminal_handler_manifest["terminal"]["kind"] == "toolkit"
    assert len(route.route_manifest_sha256) == 64
    assert len(route.terminal_handler_manifest_sha256) == 64
    assert route.terminal_handler is route.terminal_handler

    route.route_manifest["tool"]["name"] = "mutated-copy"
    toolkit.get("lookup").func = lambda query: {"forged": query}
    draft = route.terminal_handler({"query": "approved"})

    assert type(draft) is DurableToolCompletionDraft
    assert draft.result == {"seen": "approved"}
    assert draft.should_observe is True
    assert calls == ["approved"]
    assert route.route_manifest["tool"]["name"] == "lookup"
    assert resolver.verify_route(route) is route


def test_resolver_rejects_human_input_and_unexposed_tools() -> None:
    toolkit = Toolkit()
    toolkit.register(lambda: {"ok": True}, name="lookup")
    resolver = ContextToolRouteResolver()

    for tool_call in (
        ToolCall(
            call_id="call-human",
            name=ASK_USER_QUESTION_TOOL_NAME,
            arguments={},
        ),
        ToolCall(call_id="call-missing", name="missing", arguments={}),
    ):
        with pytest.raises(DurableToolExecutorContractError):
            resolver.resolve(
                _context(toolkit=toolkit, tool_call=tool_call),
                tool_call,
            )


def test_plugin_route_snapshots_every_match_and_never_rechecks_routing() -> None:
    events: list[tuple[str, object]] = []

    class Plugin:
        def __init__(self, name: str, *, terminal: bool, handled: bool) -> None:
            self.name = name
            self.terminal = terminal
            self.handled = handled
            self.can_handle_calls = 0

        def can_handle(self, *, tool_call, context):
            del tool_call, context
            self.can_handle_calls += 1
            return True

        def durable_runtime_manifest(self, *, tool_call, context):
            del tool_call, context
            return {
                "handler": self.name,
                "protocol_version": 1,
                "terminal_handler": self.terminal,
            }

        def execute(self, *, tool_call, context):
            del context
            events.append((self.name, tool_call.arguments))
            return ToolRuntimeOutcome(
                handled=self.handled,
                tool_result={"handler": self.name},
                should_observe=True,
            )

    before = Plugin("before", terminal=False, handled=False)
    terminal = Plugin("terminal", terminal=True, handled=True)
    after = Plugin("after", terminal=False, handled=True)
    toolkit = Toolkit()
    toolkit.register(lambda query: {"toolkit": query}, name="lookup")
    tool_call = ToolCall(
        call_id="call-plugin",
        name="lookup",
        arguments={"query": "original"},
    )
    resolver = ContextToolRouteResolver()
    route = resolver.resolve(
        _context(
            toolkit=toolkit,
            tool_call=tool_call,
            plugins=[before, terminal, after],
        ),
        tool_call,
    )

    plugin_manifest = route.route_manifest["plugins"]
    assert route.route_kind is DurableToolRouteKind.PLUGIN
    assert [entry["manifest"]["handler"] for entry in plugin_manifest] == [
        "before",
        "terminal",
        "after",
    ]
    assert route.terminal_handler_manifest["terminal"] == {
        "kind": "plugin",
        "match_index": 1,
    }

    before.can_handle = lambda **kwargs: (_ for _ in ()).throw(
        AssertionError("routing was recomputed")
    )
    terminal.can_handle = before.can_handle
    with pytest.raises(
        DurableToolExecutorContractError,
        match="drift",
    ):
        route.terminal_handler({"query": "approved"})

    assert events == []
    assert [plugin.can_handle_calls for plugin in (before, terminal, after)] == [
        1,
        1,
        1,
    ]


def test_matching_plugin_without_manifest_fails_even_after_terminal() -> None:
    class Terminal:
        def can_handle(self, *, tool_call, context):
            del tool_call, context
            return True

        def durable_runtime_manifest(self, *, tool_call, context):
            del tool_call, context
            return {"handler": "terminal", "terminal_handler": True}

        def execute(self, *, tool_call, context):
            del tool_call, context
            return ToolRuntimeOutcome(tool_result={"ok": True})

    class Hidden:
        def can_handle(self, *, tool_call, context):
            del tool_call, context
            return True

        def execute(self, *, tool_call, context):
            raise AssertionError("must never execute")

    toolkit = Toolkit()
    toolkit.register(lambda: {"ok": True}, name="lookup")
    tool_call = ToolCall(call_id="call-route", name="lookup", arguments={})

    with pytest.raises(
        DurableToolExecutorContractError,
        match="manifest",
    ):
        ContextToolRouteResolver().resolve(
            _context(
                toolkit=toolkit,
                tool_call=tool_call,
                plugins=[Terminal(), Hidden()],
            ),
            tool_call,
        )


@pytest.mark.parametrize(
    "outcome",
    [
        ToolRuntimeOutcome(result_messages=[{"role": "tool", "content": "x"}]),
        ToolRuntimeOutcome(state_updates={"run_status": "forged"}),
        ToolRuntimeOutcome(suspend_override=SimpleNamespace(kind="forged")),
    ],
)
def test_plugin_side_channels_are_rejected(outcome: ToolRuntimeOutcome) -> None:
    class SideChannelPlugin:
        def can_handle(self, *, tool_call, context):
            del tool_call, context
            return True

        def durable_runtime_manifest(self, *, tool_call, context):
            del tool_call, context
            return {"handler": "side-channel", "terminal_handler": True}

        def execute(self, *, tool_call, context):
            del tool_call, context
            return outcome

    toolkit = Toolkit()
    toolkit.register(lambda: {"ok": True}, name="lookup")
    tool_call = ToolCall(call_id="call-side", name="lookup", arguments={})
    route = ContextToolRouteResolver().resolve(
        _context(
            toolkit=toolkit,
            tool_call=tool_call,
            plugins=[SideChannelPlugin()],
        ),
        tool_call,
    )

    with pytest.raises(
        DurableToolExecutorContractError,
        match="side channel",
    ):
        route.terminal_handler({})


def test_declared_subagent_snapshot_becomes_a_typed_durable_transition() -> None:
    class SubagentPlugin:
        def can_handle(self, *, tool_call, context):
            del tool_call, context
            return True

        def durable_runtime_manifest(self, *, tool_call, context):
            del tool_call, context
            return {
                "handler": "subagent",
                "terminal_handler": True,
                "completion_contract": {
                    "schema": "unchain.tool_completion_contract.v1",
                    "state_transition": "subagent_snapshot.v1",
                    "allowed_state_keys": ["subagent_state"],
                },
            }

        def execute(self, *, tool_call, context):
            del tool_call, context
            state = SubagentState(
                root_agent_id="root",
                active_agent_id="root.researcher.1",
                active_lineage=["root", "root.researcher.1"],
            )
            return ToolRuntimeOutcome(
                tool_result={"status": "completed"},
                state_updates={"subagent_state": state},
            )

    toolkit = Toolkit()
    toolkit.register(lambda: {"legacy": True}, name="delegate_to_subagent")
    tool_call = ToolCall(
        call_id="call-subagent",
        name="delegate_to_subagent",
        arguments={},
    )
    route = ContextToolRouteResolver().resolve(
        _context(
            toolkit=toolkit,
            tool_call=tool_call,
            plugins=[SubagentPlugin()],
        ),
        tool_call,
    )

    draft = route.terminal_handler({})

    assert draft.result == {"status": "completed"}
    assert draft.state_transition.kind == "subagent_snapshot"
    assert draft.state_transition.base_state == SubagentState().to_dict()
    assert draft.state_transition.next_state == {
        "root_agent_id": "root",
        "active_agent_id": "root.researcher.1",
        "active_lineage": ["root", "root.researcher.1"],
        "handoff_stack": [],
        "lineage_counters": {},
        "running_batches": {},
        "threads": {},
        "mailboxes": {},
        "blackboards": {},
        "return_handoff_stack": [],
        "blocked_clarifications": [],
        "spawn_stats": {
            "delegate": 0,
            "handoff": 0,
            "worker": 0,
        },
    }
    assert draft.state_transition.handoff_refs == ()


def test_plugin_routing_and_execution_only_mutate_a_detached_state_snapshot(
) -> None:
    observed: list[str] = []

    class MutatingPlugin:
        def can_handle(self, *, tool_call, context):
            del tool_call
            context.state.subagent_state.active_agent_id = "can-handle"
            observed.append(context.state.subagent_state.active_agent_id)
            return True

        def durable_runtime_manifest(self, *, tool_call, context):
            del tool_call
            context.state.subagent_state.active_agent_id = "manifest"
            observed.append(context.state.subagent_state.active_agent_id)
            return {
                "handler": "detached",
                "terminal_handler": True,
                "completion_contract": {
                    "schema": "unchain.tool_completion_contract.v1",
                    "state_transition": "subagent_snapshot.v1",
                    "allowed_state_keys": ["subagent_state"],
                },
            }

        def execute(self, *, tool_call, context):
            del tool_call
            context.state.subagent_state.active_agent_id = "execute"
            observed.append(context.state.subagent_state.active_agent_id)
            return ToolRuntimeOutcome(
                tool_result={"ok": True},
                state_updates={
                    "subagent_state": SubagentState(
                        root_agent_id="root",
                        active_agent_id="root.worker.1",
                        active_lineage=["root", "root.worker.1"],
                    )
                },
            )

    toolkit = Toolkit()
    toolkit.register(lambda: {"legacy": True}, name="delegate_to_subagent")
    call = ToolCall(
        call_id="call-detached",
        name="delegate_to_subagent",
        arguments={},
    )
    context = _context(
        toolkit=toolkit,
        tool_call=call,
        plugins=[MutatingPlugin()],
    )
    context.state.subagent_state = SubagentState(
        root_agent_id="root",
        active_agent_id="root",
        active_lineage=["root"],
    )

    route = ContextToolRouteResolver().resolve(context, call)
    draft = route.terminal_handler({})

    assert observed == [
        "can-handle",
        "manifest",
        "can-handle",
        "manifest",
        "execute",
    ]
    assert context.state.subagent_state.active_agent_id == "root"
    assert draft.state_transition.base_state == {
        **SubagentState().to_dict(),
        "root_agent_id": "root",
        "active_agent_id": "root",
        "active_lineage": ["root"],
    }


def test_declared_transition_rejects_keys_outside_its_bound_manifest() -> None:
    class ForgedTransitionPlugin:
        def can_handle(self, *, tool_call, context):
            del tool_call, context
            return True

        def durable_runtime_manifest(self, *, tool_call, context):
            del tool_call, context
            return {
                "handler": "forged-transition",
                "terminal_handler": True,
                "completion_contract": {
                    "schema": "unchain.tool_completion_contract.v1",
                    "state_transition": "subagent_snapshot.v1",
                    "allowed_state_keys": ["subagent_state"],
                },
            }

        def execute(self, *, tool_call, context):
            del tool_call, context
            return ToolRuntimeOutcome(
                tool_result={"ok": True},
                state_updates={
                    "subagent_state": SubagentState(),
                    "run_status": "completed",
                },
            )

    toolkit = Toolkit()
    toolkit.register(lambda: {"legacy": True}, name="delegate_to_subagent")
    tool_call = ToolCall(
        call_id="call-forged-transition",
        name="delegate_to_subagent",
        arguments={},
    )
    route = ContextToolRouteResolver().resolve(
        _context(
            toolkit=toolkit,
            tool_call=tool_call,
            plugins=[ForgedTransitionPlugin()],
        ),
        tool_call,
    )

    with pytest.raises(
        DurableToolExecutorContractError,
        match="state transition|allowed|manifest",
    ):
        route.terminal_handler({})


def test_terminal_plugin_cannot_decline_its_fixed_route() -> None:
    class DecliningTerminal:
        def can_handle(self, *, tool_call, context):
            del tool_call, context
            return True

        def durable_runtime_manifest(self, *, tool_call, context):
            del tool_call, context
            return {"handler": "declining", "terminal_handler": True}

        def execute(self, *, tool_call, context):
            del tool_call, context
            return ToolRuntimeOutcome(handled=False)

    toolkit = Toolkit()
    toolkit.register(lambda: {"ok": True}, name="lookup")
    tool_call = ToolCall(call_id="call-decline", name="lookup", arguments={})
    route = ContextToolRouteResolver().resolve(
        _context(
            toolkit=toolkit,
            tool_call=tool_call,
            plugins=[DecliningTerminal()],
        ),
        tool_call,
    )

    with pytest.raises(
        DurableToolExecutorContractError,
        match="declined",
    ):
        route.terminal_handler({})


def test_normal_tool_capability_delta_is_rejected() -> None:
    toolkit = Toolkit()

    def lookup():
        return CapabilityOutcome(
            value={"ok": True},
            delta=RunDelta(
                created_by="tool.lookup",
                state_updates={"run_status": "forged"},
            ),
        )

    toolkit.register(lookup, name="lookup")
    tool_call = ToolCall(call_id="call-delta", name="lookup", arguments={})
    route = ContextToolRouteResolver().resolve(
        _context(toolkit=toolkit, tool_call=tool_call),
        tool_call,
    )

    with pytest.raises(
        DurableToolExecutorContractError,
        match="delta|side channel",
    ):
        route.terminal_handler({})


def test_verify_route_rejects_lookalikes_and_cross_resolver_authority() -> None:
    toolkit = Toolkit()
    toolkit.register(lambda: {"ok": True}, name="lookup")
    tool_call = ToolCall(call_id="call-verify", name="lookup", arguments={})
    first = ContextToolRouteResolver()
    route = first.resolve(
        _context(toolkit=toolkit, tool_call=tool_call),
        tool_call,
    )

    with pytest.raises(DurableToolExecutorContractError, match="route"):
        first.verify_route(SimpleNamespace(**vars(route)))
    with pytest.raises(DurableToolExecutorContractError, match="authority|route"):
        ContextToolRouteResolver().verify_route(route)
