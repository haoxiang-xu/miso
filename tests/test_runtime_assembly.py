from __future__ import annotations

import inspect

from unchain.kernel import KernelLoop, ModelTurnResult


class _QueueModelIO:
    provider = "openai"
    model = "gpt-4.1"

    def __init__(self, results):
        self.results = list(results)

    def fetch_turn(self, request):
        if not self.results:
            raise AssertionError("unexpected fetch_turn call")
        return self.results.pop(0)


def test_runtime_assembly_builds_default_tool_and_interaction_hooks():
    from unchain.runtime import build_default_runtime_components

    components = build_default_runtime_components()
    names = {component.name for component in components}

    assert {
        "tool_prompt",
        "tool_execution",
        "human_input_resume",
    }.issubset(names)


def test_runtime_assembly_builds_workspace_change_artifact_hook():
    from unchain.runtime import build_default_runtime_components

    components = build_default_runtime_components()
    names = {component.name for component in components}

    assert "workspace_change_artifacts" in names


def test_runtime_assembly_builds_kernel_loop_with_default_hooks():
    from unchain.runtime import build_runtime_loop

    loop = build_runtime_loop(
        model_io=_QueueModelIO(
            [
                ModelTurnResult(
                    assistant_messages=[{"role": "assistant", "content": "done"}],
                    tool_calls=[],
                    final_text="done",
                    response_id="resp_runtime",
                )
            ]
        )
    )

    assert {
        "tool_prompt",
        "tool_execution",
        "human_input_resume",
    }.issubset({harness.name for harness in loop.harnesses})


def test_runtime_assembly_builds_kernel_loop_with_memory_runtime_components():
    from unchain.memory import KernelMemoryRuntime
    from unchain.runtime import build_runtime_loop

    loop = build_runtime_loop(memory_runtime=KernelMemoryRuntime.from_config())
    names = {harness.name for harness in loop.harnesses}

    assert {
        "memory_bootstrap",
        "memory_short_term_recall",
        "memory_commit",
        "memory_prepare_event",
        "memory_commit_event",
    }.issubset(names)


def test_kernel_loop_does_not_own_default_runtime_assembly():
    import unchain.kernel.loop as kernel_loop_module

    source = inspect.getsource(kernel_loop_module.KernelLoop)

    assert "ToolPromptHarness" not in source
    assert "ToolExecutionHarness" not in source
    assert "HumanInputResumeHarness" not in source
    assert "_ensure_runtime_harnesses" not in source


def test_kernel_loop_does_not_own_workspace_change_artifact_emission():
    import unchain.kernel.loop as kernel_loop_module

    module_source = inspect.getsource(kernel_loop_module)
    source = inspect.getsource(kernel_loop_module.KernelLoop)

    assert "WorkspaceChangeTracker" not in module_source
    assert "upsert_artifacts" not in module_source
    assert "_emit_workspace_change_set_artifact" not in source


def test_kernel_loop_does_not_own_tool_observation_runtime():
    import unchain.kernel.loop as kernel_loop_module

    module_source = inspect.getsource(kernel_loop_module)
    source = inspect.getsource(kernel_loop_module.KernelLoop)

    assert "OBSERVATION_" not in module_source
    assert "observe_tool_batch" not in source
    assert "_build_observation_payload" not in source


def test_kernel_loop_does_not_own_human_input_continuation_building():
    import unchain.kernel.loop as kernel_loop_module

    source = inspect.getsource(kernel_loop_module.KernelLoop)

    assert "build_human_input_continuation" not in source
    assert "human_input_continuation" not in source


def test_kernel_loop_does_not_own_human_input_resume_state_reconstruction():
    import unchain.kernel.loop as kernel_loop_module

    source = inspect.getsource(kernel_loop_module.KernelLoop.resume_human_input)

    assert "_deserialize_response_format" not in source
    assert "provider_state.previous_response_id" not in source
    assert "token_state." not in source
    assert "workspace_change_state" not in source


def test_kernel_loop_does_not_own_model_turn_request_or_token_accounting():
    import unchain.kernel.loop as kernel_loop_module

    fetch_source = inspect.getsource(kernel_loop_module.KernelLoop.fetch_model_turn)
    apply_source = inspect.getsource(kernel_loop_module.KernelLoop.apply_model_turn)

    assert "ModelTurnRequest" not in fetch_source
    assert "RetryContext" not in fetch_source
    assert "fetch_turn_with_retry" not in fetch_source
    assert "state.next_model_input" not in fetch_source
    assert "previous_response_id" not in fetch_source

    assert "token_state" not in apply_source
    assert "previous_response_id" not in apply_source
    assert "next_model_input" not in apply_source


def test_kernel_loop_does_not_own_run_result_or_legacy_bundle_assembly():
    import unchain.kernel.loop as kernel_loop_module

    source = inspect.getsource(kernel_loop_module.KernelLoop)

    assert "_build_result" not in source
    assert "_build_legacy_bundle" not in source


def test_kernel_loop_delegates_run_lifecycle_event_payloads():
    import unchain.kernel.loop as kernel_loop_module

    source = inspect.getsource(kernel_loop_module.KernelLoop._run_state)

    assert "build_run_started_payload" in source
    assert "build_run_max_iterations_payload" in source
    assert "build_max_iterations_decision_payload" in source
    assert "build_iteration_started_payload" in source
    assert "build_response_received_payload" in source
    assert "build_iteration_completed_payload" in source
    assert "build_legacy_run_bundle(" not in source
    assert "_last_assistant_text" not in source


def test_kernel_loop_delegates_terminal_run_outcomes():
    import unchain.kernel.loop as kernel_loop_module

    source = inspect.getsource(kernel_loop_module.KernelLoop._run_state)

    assert "finish_completed_run(" in source
    assert "finish_max_iterations_run(" in source
    assert '"final_message"' not in source
    assert '"run_completed"' not in source
    assert "_dispatch_run_finalizing(" not in source
    assert "build_final_message_payload" not in source
    assert "build_run_completed_payload" not in source


def test_kernel_loop_run_does_not_auto_register_default_runtime_hooks():
    loop = KernelLoop(
        model_io=_QueueModelIO(
            [
                ModelTurnResult(
                    assistant_messages=[{"role": "assistant", "content": "done"}],
                    tool_calls=[],
                    final_text="done",
                    response_id="resp_kernel",
                )
            ]
        )
    )

    result = loop.run(
        [{"role": "user", "content": "hello"}],
        provider="openai",
        model="gpt-4.1",
        max_iterations=1,
    )

    assert result.status == "completed"
    assert {
        "tool_prompt",
        "tool_execution",
        "human_input_resume",
    }.isdisjoint({harness.name for harness in loop.harnesses})
