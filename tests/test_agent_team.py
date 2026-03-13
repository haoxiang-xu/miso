import json

from miso import Agent, MemoryManager, Team


def _step_result(*, publish=None, handoff=None, final="", idle=False):
    return {
        "publish": publish or [],
        "handoff": handoff,
        "final": final,
        "idle": idle,
        "artifacts": [],
        "conversation": [],
        "bundle": {},
        "raw_output": {},
    }


def test_agent_run_builds_fresh_engine_with_local_tools_and_memory(monkeypatch):
    instances = []

    class FakeBroth:
        def __init__(
            self,
            provider=None,
            model=None,
            api_key=None,
            memory_manager=None,
            extra_component=None,
        ):
            self.provider = provider
            self.model = model
            self.api_key = api_key
            self.memory_manager = memory_manager
            self.extra_component = extra_component
            self.toolkit = None
            self.max_iterations = 6
            self.run_calls = []
            self.on_tool_confirm = None
            instances.append(self)

        def run(
            self,
            *,
            messages,
            payload,
            response_format,
            callback,
            verbose,
            max_iterations,
            previous_response_id,
            on_tool_confirm,
            on_continuation_request,
            session_id,
            memory_namespace,
        ):
            self.run_calls.append({
                "messages": messages,
                "payload": payload,
                "response_format": response_format,
                "session_id": session_id,
                "memory_namespace": memory_namespace,
            })
            return messages + [{"role": "assistant", "content": "ok"}], {"consumed_tokens": 1}

    monkeypatch.setattr("miso.agent.Broth", FakeBroth)

    memory = MemoryManager()

    def echo(text: str):
        return {"echo": text}

    agent = Agent(
        name="planner",
        provider="openai",
        model="gpt-5",
        api_key="sk-test",
        instructions="Be concise.",
        tools=[echo],
        short_term_memory=memory,
        broth_options={"extra_component": "router_v2", "max_iterations": 9},
    )

    conversation_a, _ = agent.run("hello", session_id="run-a", memory_namespace="ns-a")
    conversation_b, _ = agent.run("hello again", session_id="run-b", memory_namespace="ns-b")

    assert len(instances) == 2
    assert instances[0] is not instances[1]
    assert instances[0].memory_manager is memory
    assert instances[0].extra_component == "router_v2"
    assert instances[0].max_iterations == 9
    assert instances[0].toolkit.execute("echo", {"text": "pong"}) == {"echo": "pong"}
    assert instances[0].run_calls[0]["messages"][0] == {"role": "system", "content": "Be concise."}
    assert instances[0].run_calls[0]["messages"][1] == {"role": "user", "content": "hello"}
    assert instances[0].run_calls[0]["session_id"] == "run-a"
    assert instances[1].run_calls[0]["session_id"] == "run-b"
    assert conversation_a[-1]["content"] == "ok"
    assert conversation_b[-1]["content"] == "ok"


def test_agent_memory_configs_are_coerced_into_memory_manager():
    agent = Agent(
        name="memoryful",
        short_term_memory={"last_n_turns": 3},
        long_term_memory={"max_profile_chars": 512},
    )

    assert agent.memory_manager is not None
    assert agent.memory_manager.config.last_n_turns == 3
    assert agent.memory_manager.config.long_term is not None
    assert agent.memory_manager.config.long_term.max_profile_chars == 512


def test_agent_step_parses_structured_multi_agent_response(monkeypatch):
    class FakeBroth:
        def __init__(self, provider=None, model=None, api_key=None, memory_manager=None):
            self.toolkit = None

        def run(
            self,
            *,
            messages,
            payload,
            response_format,
            callback,
            verbose,
            max_iterations,
            previous_response_id,
            on_tool_confirm,
            on_continuation_request,
            session_id,
            memory_namespace,
        ):
            del payload, callback, verbose, max_iterations, previous_response_id
            del on_tool_confirm, on_continuation_request, session_id, memory_namespace
            assert messages[0]["role"] == "system"
            assert "Available channels: review, shared" in messages[1]["content"] or "Available channels: shared, review" in messages[1]["content"]
            content = json.dumps(
                {
                    "publish": [
                        {
                            "channel": "review",
                            "content": "Need @reviewer validation",
                            "mentions": ["reviewer"],
                            "kind": "question",
                        }
                    ],
                    "handoff_to": "reviewer",
                    "handoff_message": "Please take over the risk review.",
                    "final": "",
                    "idle": False,
                    "artifacts": [{"name": "notes.md", "content": "Risk notes"}],
                },
                ensure_ascii=False,
            )
            return messages + [{"role": "assistant", "content": content}], {"consumed_tokens": 2}

    monkeypatch.setattr("miso.agent.Broth", FakeBroth)

    agent = Agent(name="planner", instructions="Plan carefully.")
    result = agent.step(
        inbox=[{"channel": "shared", "sender": "user", "content": "start", "step": 0}],
        channels={"shared": ["planner", "reviewer"], "review": ["planner", "reviewer"]},
        owner="planner",
        team_transcript=[{"channel": "shared", "sender": "user", "content": "start", "step": 0}],
    )

    assert result["publish"][0]["channel"] == "review"
    assert result["publish"][0]["kind"] == "question"
    assert result["publish"][0]["mentions"] == ["reviewer"]
    assert result["handoff"] == {
        "agent": "reviewer",
        "content": "Please take over the risk review.",
    }
    assert result["artifacts"][0]["name"] == "notes.md"


def test_agent_as_tool_wraps_run_output(monkeypatch):
    class FakeBroth:
        def __init__(self, provider=None, model=None, api_key=None, memory_manager=None):
            self.toolkit = None

        def run(
            self,
            *,
            messages,
            payload,
            response_format,
            callback,
            verbose,
            max_iterations,
            previous_response_id,
            on_tool_confirm,
            on_continuation_request,
            session_id,
            memory_namespace,
        ):
            del messages, payload, response_format, callback, verbose, max_iterations
            del previous_response_id, on_tool_confirm, on_continuation_request, session_id, memory_namespace
            return [{"role": "assistant", "content": "delegated answer"}], {"consumed_tokens": 4}

    monkeypatch.setattr("miso.agent.Broth", FakeBroth)

    agent = Agent(name="planner")
    delegated_tool = agent.as_tool(name="delegate_planner")
    result = delegated_tool.execute({"task": "Write the plan"})

    assert result["agent"] == "planner"
    assert result["output"] == "delegated answer"
    assert result["bundle"]["consumed_tokens"] == 4


def test_team_schedules_named_channels_and_owner_finalizes():
    planner = Agent(name="planner")
    worker = Agent(name="worker")
    planner_state = {"calls": 0}

    def planner_step(**kwargs):
        planner_state["calls"] += 1
        if planner_state["calls"] == 1:
            inbox = kwargs["inbox"]
            assert inbox[0]["sender"] == "user"
            return _step_result(
                publish=[{"channel": "shared", "content": "@worker research this", "mentions": ["worker"]}]
            )
        return _step_result(final="final answer")

    def worker_step(**kwargs):
        assert kwargs["memory_namespace"].endswith(":worker")
        return _step_result(publish=[{"channel": "shared", "content": "research complete"}])

    planner.step = planner_step
    worker.step = worker_step

    team = Team(
        agents=[planner, worker],
        owner="planner",
        channels={"shared": ["planner", "worker"]},
    )

    result = team.run("start")

    scheduled = [event["agent"] for event in result["events"] if event["type"] == "scheduled"]
    assert scheduled == ["planner", "worker", "planner"]
    assert result["final"] == "final answer"
    assert result["stop_reason"] == "owner_finalized"
    assert any(
        item["sender"] == "worker" and item["channel"] == "shared" and item["step"] == 2
        for item in result["transcript"]
    )


def test_team_mentions_raise_priority_without_forcing_dispatch():
    planner = Agent(name="planner")
    reviewer = Agent(name="reviewer")
    coder = Agent(name="coder")
    planner_state = {"calls": 0}

    def planner_step(**kwargs):
        planner_state["calls"] += 1
        if planner_state["calls"] == 1:
            return _step_result(
                publish=[{"channel": "shared", "content": "Need input from @reviewer and coder", "mentions": ["reviewer"]}]
            )
        return _step_result(final="done")

    def reviewer_step(**kwargs):
        return _step_result(publish=[{"channel": "shared", "content": "review complete"}])

    def coder_step(**kwargs):
        return _step_result(idle=True)

    planner.step = planner_step
    reviewer.step = reviewer_step
    coder.step = coder_step

    team = Team(
        agents=[planner, reviewer, coder],
        owner="planner",
        channels={"shared": ["planner", "reviewer", "coder"]},
    )

    result = team.run("start")
    scheduled = [event["agent"] for event in result["events"] if event["type"] == "scheduled"]

    assert scheduled[:2] == ["planner", "reviewer"]
    assert result["final"] == "done"


def test_team_handoff_delivers_directly_without_shared_memory():
    planner = Agent(name="planner")
    reviewer = Agent(name="reviewer")
    planner_state = {"calls": 0}

    def planner_step(**kwargs):
        planner_state["calls"] += 1
        if planner_state["calls"] == 1:
            return _step_result(handoff={"agent": "reviewer", "content": "Take ownership of review."})
        return _step_result(final="approved")

    def reviewer_step(**kwargs):
        inbox = kwargs["inbox"]
        assert any(item["kind"] == "handoff" and item["target"] == "reviewer" for item in inbox)
        return _step_result(publish=[{"channel": "shared", "content": "review finished"}])

    planner.step = planner_step
    reviewer.step = reviewer_step

    team = Team(
        agents=[planner, reviewer],
        owner="planner",
        channels={"shared": ["planner", "reviewer"]},
    )

    result = team.run("start")

    assert any(event["type"] == "handoff" and event["target"] == "reviewer" for event in result["events"])
    assert result["final"] == "approved"


def test_team_owner_finalization_stops_even_when_other_agents_are_runnable():
    planner = Agent(name="planner")
    worker = Agent(name="worker")
    worker_calls = {"count": 0}

    def planner_step(**kwargs):
        return _step_result(
            publish=[{"channel": "shared", "content": "@worker keep this queued", "mentions": ["worker"]}],
            final="ship it",
        )

    def worker_step(**kwargs):
        worker_calls["count"] += 1
        return _step_result(idle=True)

    planner.step = planner_step
    worker.step = worker_step

    team = Team(
        agents=[planner, worker],
        owner="planner",
        channels={"shared": ["planner", "worker"]},
    )

    result = team.run("start")

    assert result["final"] == "ship it"
    assert result["stop_reason"] == "owner_finalized"
    assert worker_calls["count"] == 0
