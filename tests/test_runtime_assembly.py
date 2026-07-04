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
