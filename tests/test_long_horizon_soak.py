from __future__ import annotations

import copy
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from typing import Any

from unchain.agent import Agent, MemoryModule, ToolsModule
from unchain.kernel import ModelTurnResult, ToolCall
from unchain.memory import (
    JsonFileSessionStore,
    KernelMemoryRuntime,
    MemoryConfig,
    MemoryManager,
    SessionRevisionConflictError,
)


class _FinalAnswerModelIO:
    provider = "ollama"
    model = "fake"

    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.requests: list[Any] = []

    def fetch_turn(self, request: Any) -> ModelTurnResult:
        self.requests.append(request)
        return ModelTurnResult(
            assistant_messages=[
                {"role": "assistant", "content": self.answer}
            ],
            tool_calls=[],
            final_text=self.answer,
        )


class _ToolCallingModelIO:
    provider = "ollama"
    model = "fake"

    def __init__(self, task_id: str) -> None:
        self.task_id = task_id
        self.requests: list[Any] = []

    def fetch_turn(self, request: Any) -> ModelTurnResult:
        self.requests.append(request)
        call_id = f"call_{self.task_id}"
        arguments = {"task_id": self.task_id}
        return ModelTurnResult(
            assistant_messages=[
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": "record_side_effect",
                                "arguments": arguments,
                            },
                        }
                    ],
                }
            ],
            tool_calls=[
                ToolCall(
                    call_id=call_id,
                    name="record_side_effect",
                    arguments=arguments,
                )
            ],
        )


class _ResumeAfterToolModelIO:
    provider = "ollama"
    model = "fake"

    def __init__(self, task_id: str) -> None:
        self.task_id = task_id
        self.requests: list[Any] = []

    def fetch_turn(self, request: Any) -> ModelTurnResult:
        self.requests.append(request)
        tool_messages = [
            message
            for message in request.messages
            if isinstance(message, dict) and message.get("role") == "tool"
        ]
        assert len(tool_messages) == 1
        assert json.loads(tool_messages[0]["content"]) == {
            "task_id": self.task_id,
            "side_effect": "recorded",
        }
        answer = f"done:{self.task_id}"
        return ModelTurnResult(
            assistant_messages=[{"role": "assistant", "content": answer}],
            tool_calls=[],
            final_text=answer,
        )


class _LegacySessionStore:
    def __init__(self) -> None:
        self.sessions: dict[str, dict[str, Any]] = {}

    def load(self, session_id: str) -> dict[str, Any]:
        return copy.deepcopy(self.sessions.get(session_id, {}))

    def save(self, session_id: str, state: dict[str, Any]) -> None:
        self.sessions[session_id] = copy.deepcopy(state)


def test_twelve_cold_delta_runs_keep_linear_history_and_prompt_owners(
    tmp_path: Path,
) -> None:
    session_id = "twelve-cold-delta-runs"
    revisions: list[int] = []

    for run_number in range(1, 13):
        model_io = _FinalAnswerModelIO(f"a{run_number}")
        memory = MemoryManager(
            config=MemoryConfig(last_n_turns=100),
            store=JsonFileSessionStore(tmp_path),
        )
        agent = Agent(
            name="cold-delta-soak",
            provider="ollama",
            model="fake",
            instructions="AGENT SYS",
            modules=(MemoryModule(memory=memory),),
            model_io_factory=lambda spec, context, io=model_io: io,
        )

        turn_messages = [{"role": "user", "content": f"u{run_number}"}]
        if run_number == 1:
            turn_messages.insert(
                0,
                {"role": "system", "content": "CALLER SYS"},
            )
        result = agent.run(
            turn_messages,
            session_id=session_id,
        )

        assert result.status == "completed"
        assert len(model_io.requests) == 1
        snapshot = JsonFileSessionStore(tmp_path).load_with_revision(session_id)
        assert "execution_checkpoint" not in snapshot.state
        assert isinstance(snapshot.revision, int)
        revisions.append(snapshot.revision)

    final_snapshot = JsonFileSessionStore(tmp_path).load_with_revision(session_id)
    stored = final_snapshot.state["messages"]
    expected_contents = ["AGENT SYS", "CALLER SYS"]
    for run_number in range(1, 13):
        expected_contents.extend([f"u{run_number}", f"a{run_number}"])

    assert revisions[0] > 0
    assert all(
        next_revision > previous_revision
        for previous_revision, next_revision in zip(revisions, revisions[1:])
    )
    assert final_snapshot.revision == revisions[-1]
    assert [message.get("content") for message in stored] == expected_contents
    assert sum(message.get("content") == "AGENT SYS" for message in stored) == 1
    assert sum(message.get("content") == "CALLER SYS" for message in stored) == 1
    assert len(stored) == 26


def test_six_cold_checkpoint_resumes_do_not_repeat_tool_side_effects(
    tmp_path: Path,
) -> None:
    side_effect_counts: dict[str, int] = {}
    run_count = 0

    def record_side_effect(task_id: str) -> dict[str, str]:
        side_effect_counts[task_id] = side_effect_counts.get(task_id, 0) + 1
        return {"task_id": task_id, "side_effect": "recorded"}

    for task_number in range(6):
        task_id = f"task-{task_number}"
        session_id = f"checkpoint-soak-{task_id}"
        first_model_io = _ToolCallingModelIO(task_id)
        first_memory = MemoryManager(store=JsonFileSessionStore(tmp_path))
        first_agent = Agent(
            name="checkpoint-soak",
            provider="ollama",
            model="fake",
            instructions="EXECUTION SYS",
            modules=(
                ToolsModule(tools=(record_side_effect,)),
                MemoryModule(memory=first_memory),
            ),
            model_io_factory=lambda spec, context, io=first_model_io: io,
        )

        stopped = first_agent.run(
            [
                {"role": "system", "content": "TASK POLICY SYS"},
                {"role": "user", "content": f"execute {task_id}"},
            ],
            session_id=session_id,
            max_iterations=1,
        )
        run_count += 1

        assert stopped.status == "max_iterations"
        assert side_effect_counts[task_id] == 1
        stopped_snapshot = JsonFileSessionStore(tmp_path).load_with_revision(
            session_id
        )
        checkpoint = stopped_snapshot.state["execution_checkpoint"]
        assert checkpoint["status"] == "max_iterations"
        assert checkpoint["transcript"] == stopped.messages
        assert stopped_snapshot.state.get("messages", []) == []
        assert isinstance(stopped_snapshot.revision, int)

        resumed_model_io = _ResumeAfterToolModelIO(task_id)
        resumed_memory = MemoryManager(store=JsonFileSessionStore(tmp_path))
        resumed_agent = Agent(
            name="checkpoint-soak",
            provider="ollama",
            model="fake",
            instructions="EXECUTION SYS",
            modules=(
                ToolsModule(tools=(record_side_effect,)),
                MemoryModule(memory=resumed_memory),
            ),
            model_io_factory=lambda spec, context, io=resumed_model_io: io,
        )

        completed = resumed_agent.run(
            [],
            session_id=session_id,
            max_iterations=1,
        )
        run_count += 1

        assert completed.status == "completed"
        assert side_effect_counts[task_id] == 1
        assert len(first_model_io.requests) == 1
        assert len(resumed_model_io.requests) == 1
        final_snapshot = JsonFileSessionStore(tmp_path).load_with_revision(session_id)
        assert "execution_checkpoint" not in final_snapshot.state
        assert final_snapshot.state["messages"] == completed.messages
        assert isinstance(final_snapshot.revision, int)
        assert final_snapshot.revision > stopped_snapshot.revision
        assert sum(
            message.get("role") == "user"
            and message.get("content") == f"execute {task_id}"
            for message in completed.messages
        ) == 1
        assert sum(
            message.get("role") == "assistant"
            and message.get("content") == f"done:{task_id}"
            for message in completed.messages
        ) == 1
        assert len(
            [message for message in completed.messages if message.get("role") == "tool"]
        ) == 1

    assert run_count == 12
    assert side_effect_counts == {f"task-{index}": 1 for index in range(6)}


def test_two_json_store_instances_survive_fifty_cas_races(tmp_path: Path) -> None:
    session_id = "fifty-cas-races"
    first = JsonFileSessionStore(tmp_path)
    second = JsonFileSessionStore(tmp_path)
    stores = (first, second)

    with ThreadPoolExecutor(max_workers=2) as pool:
        for round_number in range(50):
            before = first.load_with_revision(session_id)
            assert before.revision == round_number
            barrier = Barrier(2)

            def compete(
                store: JsonFileSessionStore,
                contender: str,
            ) -> tuple[str, int]:
                barrier.wait(timeout=5)
                try:
                    revision = store.save_if_revision(
                        session_id,
                        {"round": round_number, "winner": contender},
                        round_number,
                    )
                except SessionRevisionConflictError as exc:
                    return "conflict", exc.actual_revision
                return "saved", revision

            outcomes = [
                future.result(timeout=5)
                for future in (
                    pool.submit(compete, first, "first"),
                    pool.submit(compete, second, "second"),
                )
            ]

            assert sorted(status for status, _ in outcomes) == ["conflict", "saved"]
            assert {revision for _, revision in outcomes} == {round_number + 1}
            for store in stores:
                loaded = store.load_with_revision(session_id)
                assert loaded.revision == round_number + 1
                assert loaded.state["round"] == round_number
                assert loaded.state["winner"] in {"first", "second"}
            raw = json.loads((tmp_path / f"{session_id}.json").read_text("utf-8"))
            assert raw["__unchain_session_revision__"] == round_number + 1


def test_legacy_store_long_run_reports_best_effort_without_cas_claim() -> None:
    store = _LegacySessionStore()
    runtime = KernelMemoryRuntime.from_config(
        MemoryConfig(last_n_turns=100),
        store=store,
    )
    transcript: list[dict[str, Any]] = []

    for turn_number in range(1, 13):
        _, _, prepare_info, _ = runtime.bootstrap_session(
            session_id="legacy-soak",
            memory_namespace=None,
            incoming_messages=[],
            resume_mode=False,
            provider="ollama",
            model="fake",
        )
        assert prepare_info["session_revision"] is None
        assert prepare_info["session_revision_supported"] is False
        assert prepare_info["session_consistency"] == "best_effort"

        transcript.extend(
            [
                {"role": "user", "content": f"u{turn_number}"},
                {"role": "assistant", "content": f"a{turn_number}"},
            ]
        )
        commit_info, persisted = runtime.commit_transcript(
            session_id="legacy-soak",
            transcript=transcript,
            memory_namespace=None,
            model="fake",
            summary_text="",
            expected_revision=prepare_info["session_revision"],
        )
        assert commit_info["session_revision"] is None
        assert commit_info["session_revision_supported"] is False
        assert commit_info["session_consistency"] == "best_effort"
        assert persisted["messages"] == transcript

    assert not callable(getattr(store, "save_if_revision", None))
    assert store.load("legacy-soak")["messages"] == transcript
