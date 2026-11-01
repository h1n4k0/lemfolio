"""Runtime admission faults must not leak children or start after close."""
import sqlite3
import sys
import threading
import time

import pytest

import lemfolio.runtime as module


def task():
    return {"projectId": "p", "intent": "Check the supplied text", "baseRevision": "r1",
            "contextSnapshotId": "c1", "context": {"files": {"main.tex": "hello"}},
            "mode": "chat", "provider": "codex"}


def terminal(runtime, run_id):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        run = runtime.inspect(run_id)
        if run["status"] in module.TERMINAL:
            return run
        time.sleep(.02)
    pytest.fail("owned test CLI did not terminate")


def test_guard_persistence_failure_terminates_the_actual_created_child(tmp_path, monkeypatch):
    runtime = module.LocalRuntime(tmp_path / "guard-failure")
    processes = []
    spawn = module.spawn_owned

    def capture(*args, **kwargs):
        process = spawn(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(module, "spawn_owned", capture)
    monkeypatch.setattr(runtime, "capabilities", lambda: {"capabilities": ["research.generate"]})
    monkeypatch.setattr(runtime, "_command", lambda *_: [sys.executable, "-c", "import time; time.sleep(90)"])
    try:
        with runtime._db() as db:
            db.execute("""CREATE TRIGGER fail_guard BEFORE INSERT ON run_guards
                          BEGIN SELECT RAISE(ABORT,'synthetic ownership persistence fault'); END""")
        run = terminal(runtime, runtime.submit(task())["id"])
        assert run["status"] == "failed" and run["result"] is None
        assert len(processes) == 1
        assert processes[0].poll() is not None
        assert not runtime._processes
        with sqlite3.connect(runtime.db_path) as db:
            assert db.execute("SELECT COUNT(*) FROM run_guards").fetchone()[0] == 0
        assert [event["type"] for event in runtime.events(run["id"])] == ["queued", "running", "failed"]
    finally:
        runtime.close()


def test_worker_cannot_spawn_after_close_releases_the_data_directory(tmp_path, monkeypatch):
    root = tmp_path / "late-launch"
    runtime = module.LocalRuntime(root)
    entered, release, spawned = threading.Event(), threading.Event(), []
    monkeypatch.setattr(runtime, "capabilities", lambda: {"capabilities": ["research.generate"]})

    def delayed_command(*_):
        entered.set()
        if not release.wait(10):
            raise AssertionError("test did not release the blocked command")
        return [sys.executable, "-c", "import time; time.sleep(90)"]

    def forbidden_spawn(*args, **kwargs):
        spawned.append(args)
        raise AssertionError("old worker admitted a child after close")

    monkeypatch.setattr(runtime, "_command", delayed_command)
    monkeypatch.setattr(module, "spawn_owned", forbidden_spawn)
    reopened = None
    try:
        run = runtime.submit(task())
        assert entered.wait(5)
        worker = runtime._threads[run["id"]]
        runtime.close()
        assert worker.is_alive()  # close need not wait for a stalled pre-launch computation.
        reopened = module.LocalRuntime(root)
        release.set()
        worker.join(timeout=5)
        assert not worker.is_alive()
        assert spawned == []
        saved = reopened.inspect(run["id"])
        assert saved["status"] == "interrupted" and saved["result"] is None
        assert [event["type"] for event in reopened.events(run["id"])].count("interrupted") == 1
        assert reopened._threads == {} and reopened._processes == {}
    finally:
        release.set()
        runtime.close()
        if reopened is not None:
            reopened.close()
