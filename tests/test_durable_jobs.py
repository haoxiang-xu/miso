from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
import shutil
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

import pytest

import unchain.jobs.environment as job_environment
import unchain.jobs.process as job_process

from unchain.jobs import (
    DurableJobConflictError,
    DurableJobNotFoundError,
    DurableJobStoreCorruptionError,
    JobEnvironmentProfile,
    JsonFileJobStore,
    ProcessJobSupervisor,
)


def test_jobs_module_is_available_from_the_agent_public_surface() -> None:
    from unchain.agent import JobsModule

    assert JobsModule.__name__ == "JobsModule"


def _capture_worker_command(
    monkeypatch: pytest.MonkeyPatch,
    supervisor: ProcessJobSupervisor,
    tmp_path: Path,
) -> tuple[list[str], dict[str, Any]]:
    launches: list[tuple[list[str], dict[str, Any]]] = []

    class _FakeWorker:
        def __init__(self, command, **kwargs) -> None:
            launches.append((list(command), dict(kwargs)))

        def poll(self):
            return None

        def wait(self):
            return 0

    monkeypatch.setattr(job_process.subprocess, "Popen", _FakeWorker)
    snapshot, _created = supervisor.store.reserve_process_job(
        execution_id="execution-worker-prefix",
        adapter="local_process",
        argv=[sys.executable, "-c", "pass"],
        cwd=str(tmp_path.resolve()),
        timeout_ms=1_000,
        idempotency_key="worker-prefix",
        intent_digest="a" * 64,
        max_log_bytes=1_000,
        environment_digest=supervisor.environment_profile.digest,
    )
    supervisor._spawn_worker(snapshot)
    assert len(launches) == 1
    return launches[0]


def test_supervisor_default_worker_command_remains_python_module(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    supervisor = ProcessJobSupervisor(JsonFileJobStore(tmp_path / "default-jobs"))
    try:
        command, _popen_kwargs = _capture_worker_command(
            monkeypatch,
            supervisor,
            tmp_path,
        )
        assert command[:3] == [
            sys.executable,
            "-m",
            "unchain.jobs._worker",
        ]
    finally:
        supervisor.close()


def test_supervisor_uses_immutable_trusted_worker_command_prefix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prefix = [str(tmp_path / "host-runtime"), "--durable-job-worker"]
    overlay = {"PYINSTALLER_RESET_ENVIRONMENT": "1"}
    supervisor = ProcessJobSupervisor(
        JsonFileJobStore(tmp_path / "custom-jobs"),
        worker_command_prefix=prefix,
        worker_environment_overlay=overlay,
        environment={"PATH": "/usr/bin", "STABLE_JOB_VALUE": "kept"},
    )
    prefix.append("--mutated-after-construction")
    overlay["PYINSTALLER_RESET_ENVIRONMENT"] = "mutated-after-construction"
    try:
        canonical_digest = supervisor.environment_profile.digest
        command, popen_kwargs = _capture_worker_command(
            monkeypatch,
            supervisor,
            tmp_path,
        )
        assert supervisor.worker_command_prefix == (
            str(tmp_path / "host-runtime"),
            "--durable-job-worker",
        )
        assert supervisor.worker_environment_overlay == (
            ("PYINSTALLER_RESET_ENVIRONMENT", "1"),
        )
        assert command[:2] == list(supervisor.worker_command_prefix)
        assert "--mutated-after-construction" not in command
        assert (
            popen_kwargs["env"]["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
        )
        assert popen_kwargs["env"]["STABLE_JOB_VALUE"] == "kept"
        assert (
            "PYINSTALLER_RESET_ENVIRONMENT"
            not in supervisor.environment_profile.to_environment()
        )
        assert supervisor.environment_profile.digest == canonical_digest
    finally:
        supervisor.close()


def test_frozen_environment_profile_does_not_bind_temporary_bundle_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(job_environment.sys, "frozen", True, raising=False)
    monkeypatch.setattr(
        job_environment,
        "__file__",
        "/tmp/_MEI-parent/unchain/jobs/environment.py",
    )
    parent = JobEnvironmentProfile.capture({"PATH": "/usr/bin"})
    monkeypatch.setattr(
        job_environment,
        "__file__",
        "/tmp/_MEI-worker/unchain/jobs/environment.py",
    )
    worker = JobEnvironmentProfile.capture({"PATH": "/usr/bin"})

    assert parent.digest == worker.digest
    assert parent.to_environment()["PYTHONPATH"] == ""


_JOB_WORKER_SOURCE = r"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path


def append_fsync(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, payload.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)


mode, marker_raw, ready_raw, gate_raw = sys.argv[1:5]
marker = Path(marker_raw)
ready = Path(ready_raw)
gate = Path(gate_raw)

append_fsync(marker, json.dumps({"event": "START", "pid": os.getpid()}) + "\n")
print("alpha", flush=True)
print("warn-alpha", file=sys.stderr, flush=True)
append_fsync(ready, "ready\n")

if mode in {"gate", "timeout"}:
    deadline = time.monotonic() + 30.0
    while not gate.exists():
        if time.monotonic() >= deadline:
            raise SystemExit(97)
        time.sleep(0.01)

print("beta", flush=True)
print("warn-beta", file=sys.stderr, flush=True)
append_fsync(marker, json.dumps({"event": "DONE", "pid": os.getpid()}) + "\n")
"""


def _wait_until(
    predicate: Callable[[], Any],
    *,
    timeout: float = 15.0,
    description: str,
) -> Any:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            value = predicate()
        except Exception as exc:  # pragma: no cover - diagnostic preservation
            last_error = exc
        else:
            if value:
                return value
        time.sleep(0.01)
    detail = f"; last error: {type(last_error).__name__}: {last_error}" if last_error else ""
    raise AssertionError(f"timed out waiting for {description}{detail}")


def _marker_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _job_argv(
    script: Path,
    *,
    mode: str,
    marker: Path,
    ready: Path,
    gate: Path,
) -> list[str]:
    return [
        sys.executable,
        "-u",
        str(script),
        mode,
        str(marker),
        str(ready),
        str(gate),
    ]


def _intent_digest(*, argv: list[str], cwd: str, timeout_ms: int) -> str:
    payload = json.dumps(
        {
            "argv": argv,
            "cwd": str(Path(cwd).resolve()),
            "timeout_ms": timeout_ms,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _supervisor(base_dir: str | Path) -> ProcessJobSupervisor:
    return ProcessJobSupervisor(
        JsonFileJobStore(base_dir),
        heartbeat_stale_ms=1_000,
        launch_grace_ms=1_000,
        poll_interval_s=0.01,
        default_max_log_bytes=100_000,
    )


def _concurrent_start_owner(
    base_dir: str,
    execution_id: str,
    idempotency_key: str,
    argv: list[str],
    cwd: str,
    start: Any,
    ready: Any,
    results: Any,
) -> None:
    supervisor = _supervisor(base_dir)
    ready.put(os.getpid())
    if not start.wait(timeout=15):
        results.put(("owner_timeout", ""))
        supervisor.close()
        return
    try:
        snapshot = supervisor.start(
            execution_id=execution_id,
            idempotency_key=idempotency_key,
            intent_digest=_intent_digest(argv=argv, cwd=cwd, timeout_ms=20_000),
            argv=argv,
            cwd=cwd,
            timeout_ms=20_000,
        )
        results.put(("started", snapshot.job_id))
    except Exception as exc:  # pragma: no cover - returned for parent diagnostics
        results.put(("error", f"{type(exc).__name__}: {exc}"))
    finally:
        supervisor.close()


def _concurrent_store_open_owner(
    base_dir: str,
    start: Any,
    ready: Any,
    results: Any,
) -> None:
    ready.put(os.getpid())
    if not start.wait(timeout=15):
        results.put(("owner_timeout", ""))
        return
    try:
        results.put(("opened", JsonFileJobStore(base_dir).store_id))
    except Exception as exc:  # pragma: no cover - returned for parent diagnostics
        results.put(("error", f"{type(exc).__name__}: {exc}"))


def _start_then_crash_owner(
    base_dir: str,
    execution_id: str,
    idempotency_key: str,
    argv: list[str],
    cwd: str,
    timeout_ms: int,
    results: Any,
) -> None:
    supervisor = _supervisor(base_dir)
    try:
        snapshot = supervisor.start(
            execution_id=execution_id,
            idempotency_key=idempotency_key,
            intent_digest=_intent_digest(argv=argv, cwd=cwd, timeout_ms=timeout_ms),
            argv=argv,
            cwd=cwd,
            timeout_ms=timeout_ms,
        )
        results.put((snapshot.job_id, snapshot.worker_pid, snapshot.child_pid))
        results.close()
        results.join_thread()
    except Exception as exc:  # pragma: no cover - returned for parent diagnostics
        results.put(("error", f"{type(exc).__name__}: {exc}"))
        results.close()
        results.join_thread()
        raise
    os._exit(23)


def _force_kill_marked_workers(marker: Path) -> None:
    for event in _marker_events(marker):
        pid = event.get("pid")
        if not isinstance(pid, int) or pid <= 0:
            continue
        try:
            os.kill(pid, getattr(signal, "SIGKILL", signal.SIGTERM))
        except (ProcessLookupError, PermissionError, OSError):
            continue


def _force_kill_job_processes(state_dir: Path) -> None:
    pids: set[int] = set()
    for state_path in state_dir.rglob("job_*/state.json"):
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if not isinstance(state, dict):
            continue
        for key in ("child_pid", "worker_pid"):
            pid = state.get(key)
            if isinstance(pid, int) and not isinstance(pid, bool) and pid > 0:
                pids.add(pid)
    for pid in pids:
        try:
            os.kill(pid, getattr(signal, "SIGKILL", signal.SIGTERM))
        except (ProcessLookupError, PermissionError, OSError):
            continue


@pytest.fixture
def job_script(tmp_path: Path) -> Path:
    script = tmp_path / "durable_job_worker.py"
    script.write_text(_JOB_WORKER_SOURCE, encoding="utf-8")
    return script


def test_same_intent_concurrent_and_repeated_start_spawns_once(
    tmp_path: Path,
    job_script: Path,
) -> None:
    state_dir = tmp_path / "state"
    marker = tmp_path / "effects.jsonl"
    ready_file = tmp_path / "worker.ready"
    gate = tmp_path / "worker.gate"
    execution_id = "execution-dedupe"
    idempotency_key = "shell:call-background"
    argv = _job_argv(
        job_script,
        mode="gate",
        marker=marker,
        ready=ready_file,
        gate=gate,
    )
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    owners_ready = context.Queue()
    results = context.Queue()
    owners = [
        context.Process(
            target=_concurrent_start_owner,
            args=(
                str(state_dir),
                execution_id,
                idempotency_key,
                argv,
                str(tmp_path),
                start,
                owners_ready,
                results,
            ),
        )
        for _ in range(2)
    ]
    supervisor: ProcessJobSupervisor | None = None
    job_id = JsonFileJobStore.durable_job_id(execution_id, idempotency_key)
    try:
        for owner in owners:
            owner.start()
        assert len({owners_ready.get(timeout=15), owners_ready.get(timeout=15)}) == 2
        start.set()
        outcomes = [results.get(timeout=15), results.get(timeout=15)]
        for owner in owners:
            owner.join(timeout=15)
        assert all(owner.exitcode == 0 for owner in owners)
        assert outcomes == [("started", job_id), ("started", job_id)]

        _wait_until(
            lambda: ready_file.exists(),
            description="the single durable worker to report ready",
        )
        assert [event["event"] for event in _marker_events(marker)] == ["START"]

        supervisor = _supervisor(state_dir)
        repeated = supervisor.start(
            execution_id=execution_id,
            idempotency_key=idempotency_key,
            intent_digest=_intent_digest(
                argv=argv,
                cwd=str(tmp_path),
                timeout_ms=20_000,
            ),
            argv=argv,
            cwd=str(tmp_path),
            timeout_ms=20_000,
        )
        assert repeated.job_id == job_id
        assert [event["event"] for event in _marker_events(marker)] == ["START"]

        conflicting = [*argv]
        conflicting[3] = "quick"
        with pytest.raises(DurableJobConflictError):
            supervisor.start(
                execution_id=execution_id,
                idempotency_key=idempotency_key,
                intent_digest=_intent_digest(
                    argv=conflicting,
                    cwd=str(tmp_path),
                    timeout_ms=20_000,
                ),
                argv=conflicting,
                cwd=str(tmp_path),
                timeout_ms=20_000,
            )
        assert [event["event"] for event in _marker_events(marker)] == ["START"]
    finally:
        start.set()
        for owner in owners:
            owner.join(timeout=2)
            if owner.is_alive():
                owner.terminate()
                owner.join(timeout=5)
        if supervisor is None:
            supervisor = _supervisor(state_dir)
        try:
            supervisor.cancel(job_id, execution_id=execution_id, wait_timeout_ms=5_000)
        except Exception:
            pass
        supervisor.close()
        _force_kill_marked_workers(marker)
        _force_kill_job_processes(state_dir)


def test_new_supervisor_cold_reattaches_after_owner_os_exit_and_finishes(
    tmp_path: Path,
    job_script: Path,
) -> None:
    state_dir = tmp_path / "state"
    marker = tmp_path / "effects.jsonl"
    ready_file = tmp_path / "worker.ready"
    gate = tmp_path / "worker.gate"
    execution_id = "execution-cold"
    idempotency_key = "shell:call-cold"
    argv = _job_argv(
        job_script,
        mode="gate",
        marker=marker,
        ready=ready_file,
        gate=gate,
    )
    context = multiprocessing.get_context("spawn")
    results = context.Queue()
    owner = context.Process(
        target=_start_then_crash_owner,
        args=(
            str(state_dir),
            execution_id,
            idempotency_key,
            argv,
            str(tmp_path),
            20_000,
            results,
        ),
    )
    supervisor: ProcessJobSupervisor | None = None
    job_id = JsonFileJobStore.durable_job_id(execution_id, idempotency_key)
    try:
        owner.start()
        started = results.get(timeout=15)
        assert started[0] == job_id, started
        owner.join(timeout=15)
        assert owner.exitcode == 23
        _wait_until(lambda: ready_file.exists(), description="detached worker readiness")

        supervisor = _supervisor(state_dir)
        reattached = supervisor.reattach(execution_id=execution_id)
        assert [snapshot.job_id for snapshot in reattached] == [job_id]
        assert all(snapshot.execution_id == execution_id for snapshot in reattached)
        assert reattached[0].status in {"starting", "running"}
        assert reattached[0].completed is False

        first = supervisor.poll(
            job_id,
            execution_id=execution_id,
            max_output_chars=3,
        )
        assert first.stdout == "alp"
        assert first.stderr == "war"
        assert first.truncated is True
        supervisor.close()
        supervisor = _supervisor(state_dir)

        second = supervisor.poll(
            job_id,
            execution_id=execution_id,
            max_output_chars=20_000,
        )
        assert second.stdout == "ha\n"
        assert second.stderr == "n-alpha\n"

        gate.write_text("continue\n", encoding="utf-8")
        completed = supervisor.wait(
            job_id,
            execution_id=execution_id,
            timeout_ms=10_000,
            max_output_chars=20_000,
        )
        assert completed.status == "completed"
        assert completed.completed is True
        assert completed.ok is True
        assert completed.returncode == 0
        assert completed.stdout == "beta\n"
        assert completed.stderr == "warn-beta\n"
        assert [event["event"] for event in _marker_events(marker)] == ["START", "DONE"]

        terminal_poll = supervisor.poll(
            job_id,
            execution_id=execution_id,
            max_output_chars=20_000,
        )
        assert terminal_poll.stdout == ""
        assert terminal_poll.stderr == ""
        assert terminal_poll.status == "completed"
    finally:
        owner.join(timeout=2)
        if owner.is_alive():
            owner.terminate()
            owner.join(timeout=5)
        if supervisor is not None:
            try:
                supervisor.cancel(job_id, execution_id=execution_id, wait_timeout_ms=2_000)
            except Exception:
                pass
            supervisor.close()
        _force_kill_marked_workers(marker)
        _force_kill_job_processes(state_dir)


def test_jobs_are_execution_owned_and_unknown_is_distinct_from_outcome_unknown(
    tmp_path: Path,
    job_script: Path,
) -> None:
    state_dir = tmp_path / "state"
    marker = tmp_path / "effects.jsonl"
    ready_file = tmp_path / "worker.ready"
    gate = tmp_path / "worker.gate"
    execution_a = "execution-a"
    execution_b = "execution-b"
    idempotency_key = "shell:call-owned"
    argv = _job_argv(
        job_script,
        mode="gate",
        marker=marker,
        ready=ready_file,
        gate=gate,
    )
    supervisor = _supervisor(state_dir)
    started = supervisor.start(
        execution_id=execution_a,
        idempotency_key=idempotency_key,
        intent_digest=_intent_digest(
            argv=argv,
            cwd=str(tmp_path),
            timeout_ms=20_000,
        ),
        argv=argv,
        cwd=str(tmp_path),
        timeout_ms=20_000,
    )
    try:
        _wait_until(lambda: ready_file.exists(), description="owned job readiness")
        unknown_id = "job_" + "0" * 32
        with pytest.raises(DurableJobNotFoundError):
            supervisor.inspect(unknown_id, execution_id=execution_a)
        with pytest.raises(DurableJobNotFoundError):
            supervisor.inspect(started.job_id, execution_id=execution_b)
        with pytest.raises(DurableJobNotFoundError):
            supervisor.poll(
                started.job_id,
                execution_id=execution_b,
                max_output_chars=20_000,
            )
        with pytest.raises(DurableJobNotFoundError):
            supervisor.cancel(
                started.job_id,
                execution_id=execution_b,
                wait_timeout_ms=100,
            )

        owner_view = supervisor.inspect(started.job_id, execution_id=execution_a)
        assert owner_view.status in {"starting", "running"}
        assert owner_view.completed is False

        store = JsonFileJobStore(state_dir)
        spec = store.load_spec(started.job_id)
        claim = store.read_claim(started.job_id)
        assert claim is not None
        unknown_outcome = store.update_state(
            started.job_id,
            worker_token=spec["worker_token"],
            attempt_id=claim["claim_id"],
            worker_pid=claim["worker_pid"],
            status="outcome_unknown",
            outcome_unknown_reason="process_identity_mismatch",
            error="cannot prove whether the detached command completed",
        )
        assert unknown_outcome.status == "outcome_unknown"
        assert unknown_outcome.completed is True
        assert unknown_outcome.returncode is None
        assert unknown_outcome.outcome_unknown_reason == "process_identity_mismatch"
        assert unknown_outcome.job_id != unknown_id
        assert "worker_token" not in unknown_outcome.to_dict()
    finally:
        try:
            supervisor.cancel(started.job_id, execution_id=execution_a, wait_timeout_ms=2_000)
        except Exception:
            pass
        supervisor.close()
        _force_kill_marked_workers(marker)
        _force_kill_job_processes(state_dir)


def test_timeout_survives_owner_exit_and_close_detaches_while_cancel_is_explicit(
    tmp_path: Path,
    job_script: Path,
) -> None:
    timeout_state = tmp_path / "timeout-state"
    timeout_marker = tmp_path / "timeout-effects.jsonl"
    timeout_ready = tmp_path / "timeout.ready"
    never_gate = tmp_path / "never.gate"
    execution_id = "execution-timeout"
    timeout_key = "shell:call-timeout"
    timeout_argv = _job_argv(
        job_script,
        mode="timeout",
        marker=timeout_marker,
        ready=timeout_ready,
        gate=never_gate,
    )
    context = multiprocessing.get_context("spawn")
    owner_results = context.Queue()
    timeout_owner = context.Process(
        target=_start_then_crash_owner,
        args=(
            str(timeout_state),
            execution_id,
            timeout_key,
            timeout_argv,
            str(tmp_path),
            400,
            owner_results,
        ),
    )

    timeout_job_id = JsonFileJobStore.durable_job_id(execution_id, timeout_key)
    timeout_supervisor: ProcessJobSupervisor | None = None
    cancel_supervisor: ProcessJobSupervisor | None = None
    cancel_marker = tmp_path / "cancel-effects.jsonl"
    cancel_ready = tmp_path / "cancel.ready"
    cancel_gate = tmp_path / "cancel.gate"
    cancel_job_id = ""
    try:
        # Even an abrupt owner exit must not disable the detached worker's timeout.
        timeout_owner.start()
        owner_started = owner_results.get(timeout=15)
        assert owner_started[0] == timeout_job_id, owner_started
        timeout_owner.join(timeout=15)
        assert timeout_owner.exitcode == 23
        _wait_until(lambda: timeout_ready.exists(), description="timeout job readiness")
        timeout_supervisor = _supervisor(timeout_state)
        timed_out = timeout_supervisor.wait(
            timeout_job_id,
            execution_id=execution_id,
            timeout_ms=10_000,
            max_output_chars=20_000,
        )
        assert timed_out.status == "timed_out"
        assert timed_out.completed is True
        assert timed_out.timed_out is True
        assert timed_out.cancelled is False
        assert [event["event"] for event in _marker_events(timeout_marker)] == ["START"]

        cancel_state = tmp_path / "cancel-state"
        cancel_supervisor = _supervisor(cancel_state)
        cancel_argv = _job_argv(
            job_script,
            mode="gate",
            marker=cancel_marker,
            ready=cancel_ready,
            gate=cancel_gate,
        )
        started = cancel_supervisor.start(
            execution_id="execution-cancel",
            idempotency_key="shell:call-cancel",
            intent_digest=_intent_digest(
                argv=cancel_argv,
                cwd=str(tmp_path),
                timeout_ms=20_000,
            ),
            argv=cancel_argv,
            cwd=str(tmp_path),
            timeout_ms=20_000,
        )
        cancel_job_id = started.job_id
        _wait_until(lambda: cancel_ready.exists(), description="cancel job readiness")
        cancel_supervisor.close()
        cancel_supervisor = _supervisor(cancel_state)
        still_running = cancel_supervisor.inspect(
            cancel_job_id,
            execution_id="execution-cancel",
        )
        assert still_running.status in {"starting", "running"}
        assert still_running.completed is False

        cancelled = cancel_supervisor.cancel(
            cancel_job_id,
            execution_id="execution-cancel",
            wait_timeout_ms=5_000,
            max_output_chars=20_000,
        )
        assert cancelled.status == "cancelled"
        assert cancelled.completed is True
        assert cancelled.cancelled is True
        assert cancelled.timed_out is False
        assert [event["event"] for event in _marker_events(cancel_marker)] == ["START"]
    finally:
        timeout_owner.join(timeout=2)
        if timeout_owner.is_alive():
            timeout_owner.terminate()
            timeout_owner.join(timeout=5)
        if timeout_supervisor is not None:
            timeout_supervisor.close()
        if cancel_supervisor is not None:
            if cancel_job_id:
                try:
                    cancel_supervisor.cancel(
                        cancel_job_id,
                        execution_id="execution-cancel",
                        wait_timeout_ms=2_000,
                    )
                except Exception:
                    pass
            cancel_supervisor.close()
        _force_kill_marked_workers(timeout_marker)
        _force_kill_marked_workers(cancel_marker)
        _force_kill_job_processes(timeout_state)
        _force_kill_job_processes(tmp_path / "cancel-state")


def test_log_cursor_waits_for_complete_utf8_and_uses_byte_offsets(
    tmp_path: Path,
) -> None:
    store = JsonFileJobStore(tmp_path / "state")
    snapshot, _ = store.reserve_process_job(
        execution_id="execution-unicode-cursor",
        adapter="local_process",
        argv=[sys.executable, "-c", "pass"],
        cwd=str(tmp_path),
        timeout_ms=1_000,
        idempotency_key="shell:unicode-cursor",
        intent_digest=hashlib.sha256(b"unicode-cursor").hexdigest(),
        max_log_bytes=1_024,
    )
    stdout_path = store.paths_for_worker(snapshot.job_id)["stdout"]
    payload = "你🙂".encode("utf-8")
    stdout_path.write_bytes(payload[:1])

    partial = store.consume_logs(
        snapshot.job_id,
        execution_id=snapshot.execution_id,
        max_output_chars=1,
        final=False,
    )
    assert partial["stdout"] == ""
    assert partial["stdout_offset"] == 0

    with stdout_path.open("ab") as handle:
        handle.write(payload[1:])
    first = store.consume_logs(
        snapshot.job_id,
        execution_id=snapshot.execution_id,
        max_output_chars=1,
        final=False,
    )
    second = store.consume_logs(
        snapshot.job_id,
        execution_id=snapshot.execution_id,
        max_output_chars=1,
        final=False,
    )
    assert first["stdout"] == "你"
    assert first["stdout_offset"] == len("你".encode("utf-8"))
    assert first["offset_unit"] == "utf8_bytes"
    assert second["stdout"] == "🙂"
    assert second["stdout_offset"] == len(payload)
    assert second["truncated"] is False


def test_worker_log_cap_preserves_a_utf8_prefix(tmp_path: Path) -> None:
    supervisor = _supervisor(tmp_path / "state")
    command = "import sys; sys.stdout.write('你🙂x'); sys.stdout.flush()"
    try:
        started = supervisor.start(
            execution_id="execution-unicode-cap",
            idempotency_key="shell:unicode-cap",
            argv=[sys.executable, "-c", command],
            cwd=str(tmp_path),
            timeout_ms=5_000,
            max_log_bytes=4,
        )
        result = supervisor.wait(
            started.job_id,
            execution_id=started.execution_id,
            timeout_ms=5_000,
            max_output_chars=100,
        )
        assert result.status == "completed"
        assert result.stdout == "你"
        assert result.stdout_truncated is True
        assert result.offset_unit == "utf8_bytes"
    finally:
        supervisor.close()


def test_stale_claim_clear_is_cas_and_cannot_remove_replacement(
    tmp_path: Path,
) -> None:
    store = JsonFileJobStore(tmp_path / "state")
    snapshot, _ = store.reserve_process_job(
        execution_id="execution-claim-cas",
        adapter="local_process",
        argv=[sys.executable, "-c", "pass"],
        cwd=str(tmp_path),
        timeout_ms=1_000,
        idempotency_key="shell:claim-cas",
        intent_digest=hashlib.sha256(b"claim-cas").hexdigest(),
        max_log_bytes=1_024,
    )

    assert store.claim_worker(
        snapshot.job_id,
        worker_token=snapshot.worker_token,
        worker_pid=99_999_999,
    )
    stale_claim = store.read_claim(snapshot.job_id)
    assert stale_claim is not None
    assert store.clear_stale_claim(
        snapshot.job_id,
        worker_token=snapshot.worker_token,
        expected_claim=stale_claim,
    )

    assert store.claim_worker(
        snapshot.job_id,
        worker_token=snapshot.worker_token,
        worker_pid=os.getpid(),
    )
    replacement = store.read_claim(snapshot.job_id)
    assert replacement is not None
    assert replacement["claim_id"] != stale_claim["claim_id"]

    # A second supervisor acting on its stale observation must not delete the
    # replacement generation or make room for another worker.
    assert store.clear_stale_claim(
        snapshot.job_id,
        worker_token=snapshot.worker_token,
        expected_claim=stale_claim,
    ) is False
    assert store.claim_worker(
        snapshot.job_id,
        worker_token=snapshot.worker_token,
        worker_pid=os.getppid(),
    ) is False
    assert store.read_claim(snapshot.job_id) == replacement


def test_claim_worker_rejects_an_explicit_empty_attempt_id(tmp_path: Path) -> None:
    store = JsonFileJobStore(tmp_path / "state")
    snapshot, _ = store.reserve_process_job(
        execution_id="execution-empty-attempt",
        adapter="local_process",
        argv=[sys.executable, "-c", "pass"],
        cwd=str(tmp_path),
        timeout_ms=1_000,
        idempotency_key="shell:empty-attempt",
        intent_digest=hashlib.sha256(b"empty-attempt").hexdigest(),
        max_log_bytes=1_024,
    )

    with pytest.raises(ValueError, match="attempt_id"):
        store.claim_worker(
            snapshot.job_id,
            worker_token=snapshot.worker_token,
            worker_pid=os.getpid(),
            attempt_id="",
        )
    assert store.read_claim(snapshot.job_id) is None


@pytest.mark.parametrize("same_pid", [False, True])
@pytest.mark.parametrize("legacy_heartbeat", [False, True])
def test_replacement_claim_never_inherits_stale_heartbeat(
    tmp_path: Path,
    same_pid: bool,
    legacy_heartbeat: bool,
) -> None:
    now_ms = [1_000]
    store = JsonFileJobStore(
        tmp_path / f"state-{same_pid}-{legacy_heartbeat}",
        clock_ms=lambda: now_ms[0],
    )
    snapshot, _ = store.reserve_process_job(
        execution_id="execution-heartbeat-fence",
        adapter="local_process",
        argv=[sys.executable, "-c", "pass"],
        cwd=str(tmp_path),
        timeout_ms=1_000,
        idempotency_key=f"shell:heartbeat-fence:{same_pid}:{legacy_heartbeat}",
        intent_digest=hashlib.sha256(
            f"heartbeat-fence:{same_pid}:{legacy_heartbeat}".encode()
        ).hexdigest(),
        max_log_bytes=1_024,
    )
    stale_pid = 91_001
    replacement_pid = stale_pid if same_pid else 91_002
    stale_attempt_id = "d" * 32
    replacement_attempt_id = "e" * 32

    assert store.claim_worker(
        snapshot.job_id,
        worker_token=snapshot.worker_token,
        worker_pid=stale_pid,
        attempt_id=stale_attempt_id,
    )
    assert store.write_heartbeat(
        snapshot.job_id,
        worker_token=snapshot.worker_token,
        attempt_id=stale_attempt_id,
        worker_pid=stale_pid,
    )
    stale_claim = store.read_claim(snapshot.job_id)
    stale_heartbeat = store.read_heartbeat(snapshot.job_id)
    assert stale_claim is not None
    assert stale_heartbeat is not None
    assert store.clear_stale_claim(
        snapshot.job_id,
        worker_token=snapshot.worker_token,
        expected_claim=stale_claim,
    )
    assert store.read_heartbeat(snapshot.job_id) is None

    now_ms[0] += 1
    assert store.claim_worker(
        snapshot.job_id,
        worker_token=snapshot.worker_token,
        worker_pid=replacement_pid,
        attempt_id=replacement_attempt_id,
    )
    replacement_claim = store.read_claim(snapshot.job_id)
    assert replacement_claim is not None

    # Simulate residue from a crash or an older process after the atomic stale
    # claim cleanup. Neither the attempt-aware nor legacy frame may become B's
    # lease evidence, even when the operating system has reused A's PID.
    if legacy_heartbeat:
        stale_heartbeat.pop("claim_id")
    heartbeat_path = store.paths_for_worker(snapshot.job_id)["heartbeat"]
    heartbeat_path.write_text(json.dumps(stale_heartbeat), encoding="utf-8")

    supervisor = ProcessJobSupervisor(store, heartbeat_stale_ms=10_000)
    try:
        assert supervisor._heartbeat_is_fresh(
            snapshot,
            store.read_heartbeat(snapshot.job_id),
            replacement_claim,
        ) is False
    finally:
        supervisor.close()

    unknown = store.transition_to_outcome_unknown(
        snapshot.job_id,
        execution_id=snapshot.execution_id,
        expected_revision=snapshot.revision,
        expected_status=snapshot.status,
        reason="replacement_heartbeat_missing",
        error="replacement attempt has no valid lease evidence",
        heartbeat_stale_before_ms=0,
        expected_heartbeat_seq=0,
        expected_claim_id=replacement_attempt_id,
    )
    assert unknown.status == "outcome_unknown"


def test_running_legacy_heartbeat_remains_valid_for_its_original_claim(
    tmp_path: Path,
) -> None:
    store = JsonFileJobStore(tmp_path / "state")
    snapshot, _ = store.reserve_process_job(
        execution_id="execution-legacy-heartbeat",
        adapter="local_process",
        argv=[sys.executable, "-c", "pass"],
        cwd=str(tmp_path),
        timeout_ms=1_000,
        idempotency_key="shell:legacy-heartbeat",
        intent_digest=hashlib.sha256(b"legacy-heartbeat").hexdigest(),
        max_log_bytes=1_024,
    )
    attempt_id = "f" * 32
    worker_pid = os.getpid()
    assert store.claim_worker(
        snapshot.job_id,
        worker_token=snapshot.worker_token,
        worker_pid=worker_pid,
        attempt_id=attempt_id,
    )
    snapshot = store.update_state(
        snapshot.job_id,
        worker_token=snapshot.worker_token,
        attempt_id=attempt_id,
        worker_pid=worker_pid,
        status="starting",
    )
    snapshot = store.update_state(
        snapshot.job_id,
        worker_token=snapshot.worker_token,
        attempt_id=attempt_id,
        worker_pid=worker_pid,
        status="running",
        child_pid=worker_pid,
    )
    assert store.write_heartbeat(
        snapshot.job_id,
        worker_token=snapshot.worker_token,
        attempt_id=attempt_id,
        worker_pid=worker_pid,
    )
    claim = store.read_claim(snapshot.job_id)
    heartbeat = store.read_heartbeat(snapshot.job_id)
    assert claim is not None
    assert heartbeat is not None
    heartbeat.pop("claim_id")
    store.paths_for_worker(snapshot.job_id)["heartbeat"].write_text(
        json.dumps(heartbeat),
        encoding="utf-8",
    )

    supervisor = ProcessJobSupervisor(store, heartbeat_stale_ms=10_000)
    try:
        assert supervisor._heartbeat_is_fresh(
            snapshot,
            store.read_heartbeat(snapshot.job_id),
            claim,
        )
    finally:
        supervisor.close()

    unchanged = store.transition_to_outcome_unknown(
        snapshot.job_id,
        execution_id=snapshot.execution_id,
        expected_revision=snapshot.revision,
        expected_status=snapshot.status,
        reason="legacy_heartbeat_test",
        error="a fresh legacy heartbeat must win",
        heartbeat_stale_before_ms=0,
        expected_heartbeat_seq=heartbeat["heartbeat_seq"],
        expected_claim_id=attempt_id,
    )
    assert unchanged.status == "running"
    assert unchanged.revision == snapshot.revision


def test_replacement_claim_fences_stale_attempt_writes(tmp_path: Path) -> None:
    store = JsonFileJobStore(tmp_path / "state")
    snapshot, _ = store.reserve_process_job(
        execution_id="execution-attempt-fence",
        adapter="local_process",
        argv=[sys.executable, "-c", "pass"],
        cwd=str(tmp_path),
        timeout_ms=1_000,
        idempotency_key="shell:attempt-fence",
        intent_digest=hashlib.sha256(b"attempt-fence").hexdigest(),
        max_log_bytes=1_024,
    )
    worker_pid = os.getpid()
    stale_attempt_id = "a" * 32
    current_attempt_id = "b" * 32

    assert store.claim_worker(
        snapshot.job_id,
        worker_token=snapshot.worker_token,
        worker_pid=worker_pid,
        attempt_id=stale_attempt_id,
    )
    stale_claim = store.read_claim(snapshot.job_id)
    assert stale_claim is not None
    assert store.clear_stale_claim(
        snapshot.job_id,
        worker_token=snapshot.worker_token,
        expected_claim=stale_claim,
    )
    assert store.claim_worker(
        snapshot.job_id,
        worker_token=snapshot.worker_token,
        worker_pid=worker_pid,
        attempt_id=current_attempt_id,
    )

    assert store.worker_claim_is_current(
        snapshot.job_id,
        worker_token=snapshot.worker_token,
        attempt_id=stale_attempt_id,
        worker_pid=worker_pid,
    ) is False
    assert store.worker_claim_is_current(
        snapshot.job_id,
        worker_token=snapshot.worker_token,
        attempt_id=current_attempt_id,
        worker_pid=worker_pid,
    ) is True

    with pytest.raises(DurableJobConflictError, match="current attempt"):
        store.write_heartbeat(
            snapshot.job_id,
            worker_token=snapshot.worker_token,
            attempt_id=stale_attempt_id,
            worker_pid=worker_pid,
        )
    with pytest.raises(DurableJobConflictError, match="current attempt"):
        store.update_state(
            snapshot.job_id,
            worker_token=snapshot.worker_token,
            attempt_id=stale_attempt_id,
            worker_pid=worker_pid,
            status="starting",
        )
    assert store.load(
        snapshot.job_id,
        execution_id=snapshot.execution_id,
    ).revision == 0

    assert store.write_heartbeat(
        snapshot.job_id,
        worker_token=snapshot.worker_token,
        attempt_id=current_attempt_id,
        worker_pid=worker_pid,
    )
    current = store.update_state(
        snapshot.job_id,
        worker_token=snapshot.worker_token,
        attempt_id=current_attempt_id,
        worker_pid=worker_pid,
        status="starting",
    )
    assert current.status == "starting"
    assert current.revision == 1
    with pytest.raises(DurableJobConflictError, match="current attempt"):
        store.update_state(
            snapshot.job_id,
            worker_token=snapshot.worker_token,
            attempt_id=stale_attempt_id,
            worker_pid=worker_pid,
            status="completed",
            returncode=0,
        )


def test_reattach_skips_empty_residue_and_repairs_owned_prelaunch_record(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "state"
    store = JsonFileJobStore(state_dir)
    execution_id = "execution-partial-reattach"
    healthy, _ = store.reserve_process_job(
        execution_id=execution_id,
        adapter="local_process",
        argv=[sys.executable, "-c", "pass"],
        cwd=str(tmp_path),
        timeout_ms=1_000,
        idempotency_key="shell:healthy-reservation",
        intent_digest=hashlib.sha256(b"healthy-reservation").hexdigest(),
        max_log_bytes=1_024,
    )
    partial, _ = store.reserve_process_job(
        execution_id=execution_id,
        adapter="local_process",
        argv=[sys.executable, "-c", "pass"],
        cwd=str(tmp_path),
        timeout_ms=1_000,
        idempotency_key="shell:partial-reservation",
        intent_digest=hashlib.sha256(b"partial-reservation").hexdigest(),
        max_log_bytes=1_024,
    )
    partial_paths = store.paths_for_worker(partial.job_id)
    partial_paths["state"].unlink()
    partial_paths["cursor"].unlink()

    # reserve_process_job creates the directory before the first atomic spec
    # write, so an abrupt exit can leave exactly this ownerless residue.
    empty_job_dir = store.jobs_dir / ("job_" + "f" * 32)
    empty_job_dir.mkdir()

    supervisor = _supervisor(state_dir)
    supervisor.close()
    reattached = supervisor.reattach(execution_id)
    assert {item.job_id for item in reattached} == {
        healthy.job_id,
        partial.job_id,
    }
    assert all(item.status == "queued" for item in reattached)
    assert partial_paths["state"].exists()
    assert partial_paths["cursor"].exists()


def test_outcome_unknown_is_persisted_and_cannot_revert_on_late_heartbeat(
    tmp_path: Path,
) -> None:
    now_ms = [1_000]
    monotonic_s = [0.0]
    store = JsonFileJobStore(
        tmp_path / "state",
        clock_ms=lambda: now_ms[0],
    )
    snapshot, _ = store.reserve_process_job(
        execution_id="execution-monotonic-unknown",
        adapter="local_process",
        argv=[sys.executable, "-c", "pass"],
        cwd=str(tmp_path),
        timeout_ms=1_000,
        idempotency_key="shell:monotonic-unknown",
        intent_digest=hashlib.sha256(b"monotonic-unknown").hexdigest(),
        max_log_bytes=1_024,
    )
    attempt_id = "c" * 32
    assert store.claim_worker(
        snapshot.job_id,
        worker_token=snapshot.worker_token,
        worker_pid=os.getpid(),
        attempt_id=attempt_id,
    )
    snapshot = store.update_state(
        snapshot.job_id,
        worker_token=snapshot.worker_token,
        attempt_id=attempt_id,
        worker_pid=os.getpid(),
        status="starting",
    )
    snapshot = store.update_state(
        snapshot.job_id,
        worker_token=snapshot.worker_token,
        attempt_id=attempt_id,
        worker_pid=os.getpid(),
        status="running",
        child_pid=os.getpid(),
    )
    assert store.write_heartbeat(
        snapshot.job_id,
        worker_token=snapshot.worker_token,
        attempt_id=attempt_id,
        worker_pid=os.getpid(),
    )

    supervisor = ProcessJobSupervisor(
        store,
        heartbeat_stale_ms=100,
        launch_grace_ms=100,
        heartbeat_interval_ms=10,
        suspect_grace_ms=100,
        monotonic_clock=lambda: monotonic_s[0],
    )

    # A wall-clock jump or host sleep only starts a monotonic suspicion grace;
    # it does not immediately terminalize a worker that can renew its lease.
    now_ms[0] = 1_201
    suspect = supervisor.inspect(
        snapshot.job_id,
        execution_id=snapshot.execution_id,
    )
    assert suspect.status == "running"
    first_seq = store.read_heartbeat(snapshot.job_id)["heartbeat_seq"]
    assert store.write_heartbeat(
        snapshot.job_id,
        worker_token=snapshot.worker_token,
        attempt_id=attempt_id,
        worker_pid=os.getpid(),
    )
    assert store.read_heartbeat(snapshot.job_id)["heartbeat_seq"] > first_seq
    assert supervisor.inspect(
        snapshot.job_id,
        execution_id=snapshot.execution_id,
    ).status == "running"

    # When the renewed lease later stops changing for the full monotonic grace,
    # the terminal unknown transition remains durable and monotonic.
    now_ms[0] = 1_402
    assert supervisor.inspect(
        snapshot.job_id,
        execution_id=snapshot.execution_id,
    ).status == "running"
    monotonic_s[0] = 0.101
    unknown = supervisor.inspect(
        snapshot.job_id,
        execution_id=snapshot.execution_id,
    )
    assert unknown.status == "outcome_unknown"
    assert unknown.completed is True
    assert store.load(
        snapshot.job_id,
        execution_id=snapshot.execution_id,
    ).status == "outcome_unknown"

    # A resumed old worker cannot publish a lease or revive a terminal unknown
    # outcome after the supervisor won the transition CAS.
    assert store.write_heartbeat(
        snapshot.job_id,
        worker_token=snapshot.worker_token,
        attempt_id=attempt_id,
        worker_pid=os.getpid(),
    ) is False
    assert supervisor.inspect(
        snapshot.job_id,
        execution_id=snapshot.execution_id,
    ).status == "outcome_unknown"
    with pytest.raises(DurableJobConflictError):
        store.update_state(
            snapshot.job_id,
            worker_token=snapshot.worker_token,
            attempt_id=attempt_id,
            worker_pid=os.getpid(),
            status="completed",
            returncode=0,
        )
    supervisor.close()


def test_worker_lease_covers_log_drain_and_terminal_receipt(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "state"
    descendant_pid_path = tmp_path / "descendant.pid"
    command = (
        "import os, pathlib, subprocess, sys; "
        "child = subprocess.Popen("
        "[sys.executable, '-c', 'import time; time.sleep(10)'], "
        "stderr=subprocess.DEVNULL); "
        f"pathlib.Path({str(descendant_pid_path)!r}).write_text(str(child.pid)); "
        "os._exit(0)"
    )
    supervisor = ProcessJobSupervisor(
        JsonFileJobStore(state_dir),
        heartbeat_stale_ms=200,
        launch_grace_ms=200,
        heartbeat_interval_ms=25,
        suspect_grace_ms=100,
        poll_interval_s=0.01,
    )
    descendant_pid: int | None = None
    try:
        started = supervisor.start(
            execution_id="execution-finalizing-lease",
            idempotency_key="shell:finalizing-lease",
            argv=[sys.executable, "-c", command],
            cwd=str(tmp_path),
            timeout_ms=5_000,
        )
        _wait_until(
            lambda: descendant_pid_path.exists(),
            description="the inherited-pipe descendant pid",
        )
        descendant_pid = int(descendant_pid_path.read_text(encoding="utf-8"))
        result = supervisor.wait(
            started.job_id,
            execution_id=started.execution_id,
            timeout_ms=6_000,
        )
        assert result.status == "failed"
        assert result.outcome_unknown is False
        assert "stdout" in result.error
        assert supervisor.store.load(
            started.job_id,
            execution_id=started.execution_id,
        ).status == "failed"
    finally:
        supervisor.close()
        if descendant_pid is not None:
            try:
                os.kill(descendant_pid, getattr(signal, "SIGKILL", signal.SIGTERM))
            except (ProcessLookupError, PermissionError, OSError):
                pass
        _force_kill_job_processes(state_dir)


def test_invalid_intent_digest_is_rejected_before_writing_job_identity(
    tmp_path: Path,
) -> None:
    store = JsonFileJobStore(tmp_path / "state")
    execution_id = "execution-invalid-intent"
    idempotency_key = "shell:invalid-intent"
    job_id = store.durable_job_id(execution_id, idempotency_key)
    arguments = {
        "execution_id": execution_id,
        "adapter": "local_process",
        "argv": [sys.executable, "-c", "pass"],
        "cwd": str(tmp_path),
        "timeout_ms": 1_000,
        "idempotency_key": idempotency_key,
        "max_log_bytes": 1_024,
    }

    with pytest.raises(ValueError, match="lowercase SHA-256"):
        store.reserve_process_job(
            **arguments,
            intent_digest="not-a-sha256-digest",
        )
    assert (store.jobs_dir / job_id).exists() is False

    with pytest.raises(ValueError, match="environment_digest"):
        store.reserve_process_job(
            **arguments,
            intent_digest=hashlib.sha256(b"valid-intent").hexdigest(),
            environment_digest="not-an-environment-digest",
        )
    assert (store.jobs_dir / job_id).exists() is False

    retried, created = store.reserve_process_job(
        **arguments,
        intent_digest=hashlib.sha256(b"valid-intent").hexdigest(),
    )
    assert created is True
    assert retried.job_id == job_id


def test_environment_profile_is_part_of_the_idempotent_launch_spec(
    tmp_path: Path,
) -> None:
    store = JsonFileJobStore(tmp_path / "state")
    environment_a = dict(os.environ)
    environment_a["UNCHAIN_DURABLE_IDEMPOTENCY_PROFILE"] = "a"
    environment_b = dict(os.environ)
    environment_b["UNCHAIN_DURABLE_IDEMPOTENCY_PROFILE"] = "b"
    arguments = {
        "execution_id": "execution-environment-conflict",
        "adapter": "local_process",
        "argv": [sys.executable, "-c", "pass"],
        "cwd": str(tmp_path),
        "timeout_ms": 1_000,
        "idempotency_key": "shell:environment-conflict",
        "intent_digest": hashlib.sha256(b"environment-conflict").hexdigest(),
        "max_log_bytes": 1_024,
    }
    profile_a = JobEnvironmentProfile.capture(environment_a)
    profile_b = JobEnvironmentProfile.capture(environment_b)
    snapshot, created = store.reserve_process_job(
        **arguments,
        environment_digest=profile_a.digest,
    )
    assert created is True
    assert snapshot.environment_digest == profile_a.digest
    with pytest.raises(DurableJobConflictError):
        store.reserve_process_job(
            **arguments,
            environment_digest=profile_b.digest,
        )


def test_store_identity_survives_reopen_and_fences_same_path_replacement(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "state"
    original = JsonFileJobStore(state_dir)
    reopened = JsonFileJobStore(state_dir)
    assert reopened.store_id == original.store_id

    old_store_id = original.store_id
    shutil.rmtree(state_dir)
    replacement = JsonFileJobStore(state_dir)
    assert replacement.store_id != old_store_id

    job_id = original.durable_job_id("execution-store-id", "shell:store-id")
    with pytest.raises(
        DurableJobStoreCorruptionError,
        match="identity changed",
    ):
        original.contains(job_id)


def test_replacement_generation_is_physically_isolated_from_stale_job_paths(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "state"
    execution_id = "execution-store-generation-race"
    idempotency_key = "shell:store-generation-race"
    arguments = {
        "execution_id": execution_id,
        "adapter": "local_process",
        "argv": [sys.executable, "-c", "pass"],
        "cwd": str(tmp_path),
        "timeout_ms": 1_000,
        "idempotency_key": idempotency_key,
        "intent_digest": hashlib.sha256(b"store-generation-race").hexdigest(),
        "max_log_bytes": 1_024,
    }
    stale_store = JsonFileJobStore(state_dir)
    stale, _ = stale_store.reserve_process_job(**arguments)
    read_completed = threading.Event()
    allow_stale_write = threading.Event()
    original_read_state = stale_store._read_state

    def _gated_read_state(path, *, expected_job_id):
        state = original_read_state(path, expected_job_id=expected_job_id)
        read_completed.set()
        assert allow_stale_write.wait(timeout=10)
        return state

    stale_store._read_state = _gated_read_state  # type: ignore[method-assign]
    outcome: list[object] = []

    def _claim_from_stale_generation() -> None:
        try:
            outcome.append(
                stale_store.claim_worker(
                    stale.job_id,
                    worker_token=stale.worker_token,
                    worker_pid=os.getpid(),
                )
            )
        except BaseException as exc:  # pragma: no cover - diagnostic capture
            outcome.append(exc)

    claimant = threading.Thread(target=_claim_from_stale_generation)
    claimant.start()
    assert read_completed.wait(timeout=10)
    shutil.rmtree(state_dir)
    replacement_store = JsonFileJobStore(state_dir)
    replacement, _ = replacement_store.reserve_process_job(**arguments)
    assert replacement.job_id == stale.job_id
    assert replacement_store.jobs_dir != stale_store.jobs_dir

    allow_stale_write.set()
    claimant.join(timeout=10)
    assert claimant.is_alive() is False
    assert outcome == [True]
    # The stale path may recreate an orphaned A namespace, but it cannot write
    # into replacement namespace B or affect B's launch claim.
    assert replacement_store.read_claim(replacement.job_id) is None
    assert replacement_store.load(
        replacement.job_id,
        execution_id=execution_id,
    ).status == "queued"


def test_concurrent_first_open_creates_one_store_identity(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    owners_ready = context.Queue()
    results = context.Queue()
    owners = [
        context.Process(
            target=_concurrent_store_open_owner,
            args=(str(state_dir), start, owners_ready, results),
        )
        for _ in range(6)
    ]
    try:
        for owner in owners:
            owner.start()
        assert len({owners_ready.get(timeout=15) for _ in owners}) == len(owners)
        start.set()
        outcomes = [results.get(timeout=15) for _ in owners]
        for owner in owners:
            owner.join(timeout=15)
        assert all(owner.exitcode == 0 for owner in owners)
        assert {kind for kind, _ in outcomes} == {"opened"}
        assert len({store_id for _, store_id in outcomes}) == 1
        assert JsonFileJobStore(state_dir).store_id == outcomes[0][1]
    finally:
        for owner in owners:
            if owner.is_alive():
                owner.terminate()
                owner.join(timeout=5)


def test_missing_store_manifest_with_jobs_fails_closed(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    store = JsonFileJobStore(state_dir)
    snapshot, _ = store.reserve_process_job(
        execution_id="execution-missing-manifest",
        adapter="local_process",
        argv=[sys.executable, "-c", "pass"],
        cwd=str(tmp_path),
        timeout_ms=1_000,
        idempotency_key="shell:missing-manifest",
        intent_digest=hashlib.sha256(b"missing-manifest").hexdigest(),
        max_log_bytes=1_024,
    )
    (state_dir / "store.json").unlink()

    with pytest.raises(DurableJobStoreCorruptionError, match="manifest is missing"):
        store.load(snapshot.job_id, execution_id=snapshot.execution_id)
    with pytest.raises(
        DurableJobStoreCorruptionError,
        match="manifest is missing while jobs exist",
    ):
        JsonFileJobStore(state_dir)


def test_supervisor_uses_one_frozen_environment_without_persisting_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_dir = tmp_path / "state"
    variable = "UNCHAIN_DURABLE_PROFILE_PROBE"
    approved_value = "approved-secret-value"
    environment = dict(os.environ)
    environment[variable] = approved_value
    supervisor = ProcessJobSupervisor(
        JsonFileJobStore(state_dir),
        environment=environment,
        poll_interval_s=0.01,
    )
    try:
        # Ambient mutations after construction cannot alter the supervisor's
        # immutable execution profile.
        monkeypatch.setenv(variable, "ambient-drifted-value")
        started = supervisor.start(
            execution_id="execution-frozen-environment",
            idempotency_key="shell:frozen-environment",
            argv=[
                sys.executable,
                "-c",
                f"import os; print(os.environ[{variable!r}])",
            ],
            cwd=str(tmp_path),
            timeout_ms=5_000,
        )
        result = supervisor.wait(
            started.job_id,
            execution_id=started.execution_id,
            timeout_ms=5_000,
        )
        assert result.status == "completed"
        assert result.stdout.strip() == approved_value
        assert started.environment_digest == supervisor.environment_profile.digest
        for json_path in state_dir.rglob("*.json"):
            assert approved_value not in json_path.read_text(encoding="utf-8")
    finally:
        supervisor.close()
        _force_kill_job_processes(state_dir)


def test_queued_recovery_requires_the_same_environment_profile(
    tmp_path: Path,
) -> None:
    variable = "UNCHAIN_DURABLE_RECOVERY_PROFILE"
    environment_a = dict(os.environ)
    environment_a[variable] = "profile-a"
    environment_b = dict(os.environ)
    environment_b[variable] = "profile-b"
    profile_a = JobEnvironmentProfile.capture(environment_a)

    failed_state_dir = tmp_path / "failed-state"
    effect = tmp_path / "must-not-run.txt"
    failed_store = JsonFileJobStore(failed_state_dir)
    queued, _ = failed_store.reserve_process_job(
        execution_id="execution-environment-mismatch",
        adapter="local_process",
        argv=[
            sys.executable,
            "-c",
            f"from pathlib import Path; Path({str(effect)!r}).write_text('ran')",
        ],
        cwd=str(tmp_path),
        timeout_ms=5_000,
        idempotency_key="shell:environment-mismatch",
        intent_digest=hashlib.sha256(b"environment-mismatch").hexdigest(),
        environment_digest=profile_a.digest,
        max_log_bytes=1_024,
    )
    mismatched = ProcessJobSupervisor(
        JsonFileJobStore(failed_state_dir),
        environment=environment_b,
    )
    try:
        with pytest.raises(
            DurableJobConflictError,
            match="environment profile",
        ):
            mismatched.inspect(
                queued.job_id,
                execution_id=queued.execution_id,
            )
        assert failed_store.load(
            queued.job_id,
            execution_id=queued.execution_id,
        ).status == "queued"
        assert effect.exists() is False
    finally:
        mismatched.close()

    matching_after_conflict = ProcessJobSupervisor(
        JsonFileJobStore(failed_state_dir),
        environment=environment_a,
        poll_interval_s=0.01,
    )
    try:
        result = matching_after_conflict.wait(
            queued.job_id,
            execution_id=queued.execution_id,
            timeout_ms=5_000,
        )
        assert result.status == "completed"
        assert effect.read_text(encoding="utf-8") == "ran"
    finally:
        matching_after_conflict.close()
        _force_kill_job_processes(failed_state_dir)

    recovered_state_dir = tmp_path / "recovered-state"
    recovered_store = JsonFileJobStore(recovered_state_dir)
    recoverable, _ = recovered_store.reserve_process_job(
        execution_id="execution-environment-match",
        adapter="local_process",
        argv=[
            sys.executable,
            "-c",
            f"import os; print(os.environ[{variable!r}])",
        ],
        cwd=str(tmp_path),
        timeout_ms=5_000,
        idempotency_key="shell:environment-match",
        intent_digest=hashlib.sha256(b"environment-match").hexdigest(),
        environment_digest=profile_a.digest,
        max_log_bytes=1_024,
    )
    matching = ProcessJobSupervisor(
        JsonFileJobStore(recovered_state_dir),
        environment=environment_a,
        poll_interval_s=0.01,
    )
    try:
        [reattached] = matching.reattach(recoverable.execution_id)
        assert reattached.job_id == recoverable.job_id
        result = matching.wait(
            recoverable.job_id,
            execution_id=recoverable.execution_id,
            timeout_ms=5_000,
        )
        assert result.status == "completed"
        assert result.stdout.strip() == "profile-a"
    finally:
        matching.close()
        _force_kill_job_processes(recovered_state_dir)


def test_cancel_marks_a_queued_job_before_recovery_can_launch_it(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "state"
    effect = tmp_path / "must-not-run.txt"
    store = JsonFileJobStore(state_dir)
    snapshot, _ = store.reserve_process_job(
        execution_id="execution-cancel-queued",
        adapter="local_process",
        argv=[
            sys.executable,
            "-c",
            "from pathlib import Path; Path(r'%s').write_text('ran')" % effect,
        ],
        cwd=str(tmp_path),
        timeout_ms=5_000,
        idempotency_key="shell:cancel-queued",
        intent_digest=hashlib.sha256(b"cancel-queued").hexdigest(),
        max_log_bytes=1_024,
    )
    supervisor = _supervisor(state_dir)
    try:
        result = supervisor.cancel(
            snapshot.job_id,
            execution_id=snapshot.execution_id,
            wait_timeout_ms=5_000,
        )
        assert result.status == "cancelled"
        assert result.cancelled is True
        assert effect.exists() is False
    finally:
        supervisor.close()
        _force_kill_job_processes(state_dir)


def test_job_store_fails_closed_on_spec_tamper_and_cursor_rollback(
    tmp_path: Path,
) -> None:
    store = JsonFileJobStore(tmp_path / "state")
    snapshot, _ = store.reserve_process_job(
        execution_id="execution-corruption",
        adapter="local_process",
        argv=[sys.executable, "-c", "pass"],
        cwd=str(tmp_path),
        timeout_ms=1_000,
        idempotency_key="shell:corruption",
        intent_digest=hashlib.sha256(b"corruption").hexdigest(),
        max_log_bytes=1_024,
    )
    paths = store.paths_for_worker(snapshot.job_id)
    spec = json.loads(paths["spec"].read_text(encoding="utf-8"))
    spec["argv"][-1] = "raise SystemExit(99)"
    paths["spec"].write_text(json.dumps(spec), encoding="utf-8")
    with pytest.raises(DurableJobStoreCorruptionError):
        store.load(snapshot.job_id, execution_id=snapshot.execution_id)

    # Restore the immutable spec, then prove a cursor cannot silently move
    # backwards when its log file is missing or truncated.
    spec["argv"][-1] = "pass"
    paths["spec"].write_text(json.dumps(spec), encoding="utf-8")
    cursor = json.loads(paths["cursor"].read_text(encoding="utf-8"))
    cursor["stdout_offset"] = 10
    paths["cursor"].write_text(json.dumps(cursor), encoding="utf-8")
    with pytest.raises(DurableJobStoreCorruptionError):
        store.consume_logs(
            snapshot.job_id,
            execution_id=snapshot.execution_id,
            max_output_chars=10,
        )
