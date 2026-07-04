from __future__ import annotations

from types import SimpleNamespace


def test_prepare_fresh_run_invocation_seeds_state_and_previous_response_mode():
    from unchain.kernel.run_preparation import prepare_fresh_run_invocation

    class ModelIO:
        provider = "openai"
        model = "gpt-test"

        def _merged_payload(self, payload):
            return {**payload, "store": True}

    plan = prepare_fresh_run_invocation(
        messages=[{"role": "user", "content": "hello"}],
        payload={"temperature": 0},
        model_io=ModelIO(),
        provider=None,
        model=None,
        previous_response_id="resp-1",
        session_id="session-1",
        memory_namespace="memory-1",
        max_context_window_tokens=100,
        run_id="run-1",
        run_id_factory=lambda: "generated",
    )

    assert plan.run_id == "run-1"
    assert plan.payload == {"temperature": 0}
    assert plan.state.provider_state.provider == "openai"
    assert plan.state.provider_state.model == "gpt-test"
    assert plan.state.provider_state.previous_response_id == "resp-1"
    assert plan.state.provider_state.use_previous_response_chain is True
    assert plan.state.session_state.session_id == "session-1"
    assert plan.state.session_state.memory_namespace == "memory-1"
    assert plan.state.run_status == "running"


def test_prepare_fresh_run_invocation_disables_openai_previous_response_chain_when_store_false():
    from unchain.kernel.run_preparation import prepare_fresh_run_invocation

    plan = prepare_fresh_run_invocation(
        messages=[{"role": "user", "content": "hello"}],
        payload={"store": False},
        model_io=SimpleNamespace(provider="openai", model="gpt-test"),
        provider=None,
        model=None,
        previous_response_id="resp-1",
        session_id=None,
        memory_namespace=None,
        max_context_window_tokens=None,
        run_id=None,
        run_id_factory=lambda: "generated-run",
    )

    assert plan.run_id == "generated-run"
    assert plan.state.provider_state.use_previous_response_chain is False


def test_prepare_fresh_run_invocation_rejects_unsupported_provider():
    from unchain.kernel.run_preparation import prepare_fresh_run_invocation

    try:
        prepare_fresh_run_invocation(
            messages=[],
            payload={},
            model_io=SimpleNamespace(provider="unknown", model="x"),
            provider=None,
            model=None,
            previous_response_id=None,
            session_id=None,
            memory_namespace=None,
            max_context_window_tokens=None,
            run_id="run-1",
            run_id_factory=lambda: "generated",
        )
    except NotImplementedError as exc:
        assert "supports only provider" in str(exc)
    else:
        raise AssertionError("expected NotImplementedError")
