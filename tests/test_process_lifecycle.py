"""Windows process-tree ownership, observed through live kernel process handles.

The vendor CLIs are replaced with local Python programs. These tests never log
in, call a model, kill a process by name, or recover a persisted PID.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import time

import pytest


pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows Job Object lifecycle regression")
ROOT = Path(__file__).resolve().parents[1]


class ProcessHandle:
    """Retain the original process object; PID reuse cannot satisfy assertions."""

    def __init__(self, pid: int):
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.kernel.OpenProcess.restype = wintypes.HANDLE
        self.kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self.kernel.WaitForSingleObject.restype = wintypes.DWORD
        self.kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        self.kernel.TerminateProcess.restype = wintypes.BOOL
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel.CloseHandle.restype = wintypes.BOOL
        self.pid = pid
        self.handle = self.kernel.OpenProcess(0x00100000 | 0x0001, False, pid)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())

    def terminated(self, timeout_ms=0):
        status = self.kernel.WaitForSingleObject(self.handle, timeout_ms)
        assert status in (0, 258), f"WaitForSingleObject failed: {status}"
        return status == 0

    def terminate(self, code=91):
        if not self.terminated():
            assert self.kernel.TerminateProcess(self.handle, code)
            assert self.terminated(5000)

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


LEAF = r'''
import json, os, sys, time
from pathlib import Path
directory = Path(sys.argv[1])
(directory / "heartbeat").write_text(str(time.monotonic_ns()))
(directory / "leaf-ready.json").write_text(json.dumps({"pid": os.getpid()}))
while True:
    (directory / "heartbeat").write_text(str(time.monotonic_ns()))
    time.sleep(.025)
'''

CLI = r'''
import json, os, subprocess, sys, time
from pathlib import Path
directory = Path(sys.argv[1])
sys.stdin.read()
with (directory / "cli-invocations").open("a") as stream:
    stream.write(str(os.getpid()) + "\n")
# stdout/stderr are deliberately inherited. A surviving descendant would also
# keep the runtime's pipe readers open after this direct child exits.
leaf = subprocess.Popen([sys.executable, "-B", str(directory / "leaf.py"), str(directory)],
                        stdin=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
until = time.monotonic() + 10
while not (directory / "leaf-ready.json").exists():
    if time.monotonic() > until:
        raise RuntimeError("test descendant did not become ready")
    time.sleep(.01)
ready = directory / "tree-ready.json"
ready.with_suffix(".tmp").write_text(json.dumps({"cli_pid": os.getpid(), "leaf_pid": leaf.pid}))
ready.with_suffix(".tmp").replace(ready)
while not (directory / "finish-cli").exists():
    time.sleep(.01)
result = {"answer": "synthetic lifecycle result", "changes": [], "checks": []}
print(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(result)}}), flush=True)
'''

OWNER = r'''
import json, os, sys, time
from pathlib import Path
from lemfolio.api import create_app
directory = Path(sys.argv[1])
app = create_app(directory / "app-data")
runtime = app.state.runtime
runtime.capabilities = lambda: {"capabilities": ["research.generate"]}
runtime._command = lambda *_: [sys.executable, "-B", str(directory / "cli.py"), str(directory)]
run = runtime.submit({"projectId":"synthetic-paper", "intent":"Lifecycle regression", "baseRevision":"r1",
                      "contextSnapshotId":"c1", "context":{"files":{"main.tex":"hello"}},
                      "mode":"chat", "provider":"codex"})
(directory / "run.json").write_text(json.dumps({"id":run["id"]}))
until = time.monotonic() + 15
while not (directory / "tree-ready.json").exists():
    if time.monotonic() > until:
        raise RuntimeError("test CLI tree did not become ready")
    time.sleep(.01)
(directory / "owner-ready").write_text("ready")
while True:
    action = (directory / "action").read_text() if (directory / "action").exists() else ""
    if action == "os_exit":
        os._exit(73)
    if action == "interrupt":
        runtime.interrupt(run["id"])
        break
    if action == "close":
        runtime.close()
        break
    if action == "complete":
        if runtime.inspect(run["id"])["status"] in {"completed","failed","interrupted"}:
            break
    time.sleep(.01)
snapshot = runtime.inspect(run["id"])
events = runtime.events(run["id"])
outcome = directory / "outcome.json"
outcome.with_suffix(".tmp").write_text(json.dumps({"run":snapshot,"events":events}))
outcome.with_suffix(".tmp").replace(outcome)
# Stay alive until the test has observed tree termination. Owner exit itself
# must not conceal a cancellation/completion cleanup failure.
while not (directory / "exit-owner").exists():
    time.sleep(.01)
runtime.close()
app.state.compiler.close()
'''


def wait_file(path: Path, owner: subprocess.Popen, timeout=20):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        if path.is_file():
            # The writer creates this once; a racing read may catch its write.
            try:
                text = path.read_text(encoding="utf-8")
                if text:
                    return text
            except OSError:
                pass
        if owner.poll() is not None:
            diagnostics = (path.parent / "owner.log").read_text(encoding="utf-8", errors="replace")
            pytest.fail(f"synthetic runtime exited before {path.name}: {diagnostics}")
        time.sleep(.01)
    pytest.fail(f"synthetic runtime did not produce {path.name}")


@pytest.fixture
def tree(tmp_path):
    for name, content in [("owner.py", OWNER), ("cli.py", CLI), ("leaf.py", LEAF)]:
        (tmp_path / name).write_text(content, encoding="utf-8")
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
    handles = []
    with (tmp_path / "owner.log").open("wb") as log:
        owner = subprocess.Popen([sys.executable, "-B", str(tmp_path / "owner.py"), str(tmp_path)],
                                 env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                 creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            wait_file(tmp_path / "owner-ready", owner)
            info = json.loads((tmp_path / "tree-ready.json").read_text())
            for key in ("cli_pid", "leaf_pid"):
                handles.append(ProcessHandle(info[key]))
                assert not handles[-1].terminated(), f"synthetic {key} unexpectedly exited"
            run_id = json.loads((tmp_path / "run.json").read_text())["id"]
            yield tmp_path, owner, handles, run_id
        finally:
            # Every handle belongs to a process created by this fixture. The
            # assertions run before this cleanup; cleanup cannot manufacture PASS.
            if owner.poll() is None:
                owner.kill()
            owner.wait(timeout=10)
            for handle in handles:
                handle.terminate()
                handle.close()


def assert_tree_dead(directory, handles):
    assert handles[0].terminated(10000), "direct CLI survived its runtime owner"
    assert handles[1].terminated(10000), "CLI descendant survived its runtime owner"
    heartbeat = (directory / "heartbeat").read_bytes()
    time.sleep(.1)
    assert (directory / "heartbeat").read_bytes() == heartbeat


@pytest.mark.parametrize("failure", ["os_exit", "terminate_process"])
def test_abrupt_runtime_death_kills_cli_tree_and_application_restart_does_not_replay(tree, failure):
    from fastapi.testclient import TestClient
    from lemfolio.api import create_app

    directory, owner, handles, run_id = tree
    calls = (directory / "cli-invocations").read_bytes()
    if failure == "os_exit":
        (directory / "action").write_text("os_exit")
        assert owner.wait(timeout=10) == 73  # os._exit bypasses finally and atexit.
    else:
        owner_handle = ProcessHandle(owner.pid)
        try:
            owner_handle.terminate(91)  # External termination executes no Python cleanup.
        finally:
            owner_handle.close()
        assert owner.wait(timeout=10) == 91
    assert_tree_dead(directory, handles)

    with sqlite3.connect(directory / "app-data/runtime/runs.sqlite3") as db:
        assert db.execute("SELECT status,result FROM runs WHERE id=?", (run_id,)).fetchone() == ("running", None)

    previous_events = None
    for _ in range(2):
        app = create_app(directory / "app-data")
        with TestClient(app, base_url="http://127.0.0.1") as client:
            assert client.get("/api/session").status_code == 200
            response = client.get(f"/api/runs/{run_id}")
            assert response.status_code == 200, response.text
            run = response.json()
            assert run["status"] == "interrupted"
            assert run["result"] is None and run["execution_resume_supported"] is False
            events_response = client.get(f"/api/runs/{run_id}/event-log")
            assert events_response.status_code == 200, events_response.text
            events = events_response.json()
            interrupted = [event for event in events if event["type"] == "interrupted"]
            assert len(interrupted) == 1
            assert interrupted[0]["data"]["reason"] == "runtime_restart"
            assert not any(event["type"] in {"result", "completed"} for event in events)
            if previous_events is not None:
                assert events == previous_events  # Reopening cannot create another interruption.
            previous_events = events
            assert not app.state.runtime._processes and not app.state.runtime._threads
            assert (directory / "cli-invocations").read_bytes() == calls
            assert all(handle.terminated() for handle in handles)


@pytest.mark.parametrize("action", ["interrupt", "close", "complete"])
def test_normal_runtime_lifecycle_cleans_descendants_even_after_direct_cli_exit(tree, action):
    directory, owner, handles, _ = tree
    if action == "complete":
        (directory / "finish-cli").write_text("finish")
    (directory / "action").write_text(action)
    wait_file(directory / "outcome.json", owner)
    assert owner.poll() is None, "owner exit must not conceal failed explicit cleanup"
    assert_tree_dead(directory, handles)
    outcome = json.loads((directory / "outcome.json").read_text())
    if action == "complete":
        assert outcome["run"]["status"] == "completed"
        assert outcome["run"]["result"] == {"answer": "synthetic lifecycle result", "changes": [], "checks": []}
        assert [event["type"] for event in outcome["events"]].count("completed") == 1
    else:
        assert outcome["run"]["status"] == "interrupted"
        assert outcome["run"]["result"] is None
    (directory / "exit-owner").write_text("exit")
    assert owner.wait(timeout=10) == 0, (directory / "owner.log").read_text(errors="replace")


def test_job_creation_failure_is_fail_closed_before_any_child_creation(monkeypatch, tmp_path):
    import lemfolio.processes as processes

    def admission_failure():
        raise OSError("injected job admission failure")

    def forbidden_fallback(*_args, **_kwargs):
        pytest.fail("failed ownership admission must never launch an unprotected child")

    monkeypatch.setattr(processes, "_new_job", admission_failure)
    monkeypatch.setattr(processes, "_create_process", forbidden_fallback)
    monkeypatch.setattr(subprocess, "Popen", forbidden_fallback)
    with pytest.raises(OSError, match="injected job admission failure"):
        processes.spawn_owned([sys.executable, "-c", "raise SystemExit(0)"], cwd=tmp_path)


@pytest.mark.parametrize("action", ["interrupt", "close"])
def test_launch_registration_barrier_cannot_outlive_cancel_or_release_runtime_lock(monkeypatch, tmp_path, action):
    import lemfolio.runtime as module

    root = tmp_path / "runtime"
    runtime = module.LocalRuntime(root)
    monkeypatch.setattr(runtime, "capabilities", lambda: {"capabilities": ["research.generate"]})
    monkeypatch.setattr(runtime, "_command", lambda *_: [sys.executable, "-c", "import time; time.sleep(60)"])
    original_spawn = module.spawn_owned
    reached, release, finished = threading.Event(), threading.Event(), threading.Event()
    created, errors = [], []
    held = None
    action_thread = None

    def paused_spawn(*args, **kwargs):
        process = original_spawn(*args, **kwargs)
        created.append(process)
        reached.set()
        if not release.wait(10):
            process.close()
            raise AssertionError("test launch barrier timed out")
        return process

    monkeypatch.setattr(module, "spawn_owned", paused_spawn)
    try:
        run = runtime.submit({"projectId": "p", "intent": "Cancellation at process admission", "baseRevision": "r1",
                              "contextSnapshotId": "c1", "context": {"files": {"main.tex": "hello"}},
                              "mode": "chat", "provider": "codex"})
        assert reached.wait(5), "runtime did not enter the real launch barrier"
        held = ProcessHandle(created[0].pid)
        assert not held.terminated()

        def perform_action():
            try:
                runtime.close() if action == "close" else runtime.interrupt(run["id"])
            except BaseException as exc:
                errors.append(exc)
            finally:
                finished.set()

        action_thread = threading.Thread(target=perform_action, daemon=True)
        action_thread.start()
        assert not finished.wait(2.2 if action == "close" else .2)
        # The previous two-second close join must not release this lock while a
        # spawned but unregistered worker can still begin executing afterwards.
        with pytest.raises(module.RuntimeError, match="already owns"):
            module.LocalRuntime(root)
        release.set()
        assert finished.wait(10), "lifecycle action did not settle after admission"
        action_thread.join(timeout=1)
        assert not errors
        assert held.terminated(5000), "cancel/close returned with an owned CLI alive"
        assert runtime.inspect(run["id"])["status"] == "interrupted"
        assert runtime.inspect(run["id"])["result"] is None
        until = time.monotonic() + 5
        while runtime._threads and time.monotonic() < until:
            time.sleep(.01)
        assert not runtime._threads and not runtime._processes
        if action == "close":
            reopened = module.LocalRuntime(root)
            try:
                assert reopened.inspect(run["id"])["status"] == "interrupted"
                assert not reopened._threads
            finally:
                reopened.close()
    finally:
        release.set()
        if action_thread is not None:
            action_thread.join(timeout=10)
        runtime.close()
        if held is not None:
            held.terminate()
            held.close()


def test_authentication_probe_tree_also_dies_after_forced_owner_exit(tmp_path):
    """A logged-in probe is still a vendor process and needs the same boundary."""
    for name, content in [("cli.py", CLI), ("leaf.py", LEAF)]:
        (tmp_path / name).write_text(content, encoding="utf-8")
    probe_owner = r'''
import sys
from pathlib import Path
import lemfolio.runtime as module
directory = Path(sys.argv[1])
runtime = module.LocalRuntime(directory / "runtime")
module._resolve_cli = lambda name: [sys.executable,"-B",str(directory / "cli.py"),str(directory)] if name == "codex" else None
runtime.capabilities()
'''
    (tmp_path / "owner.py").write_text(probe_owner, encoding="utf-8")
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
    handles = []
    with (tmp_path / "owner.log").open("wb") as log:
        owner = subprocess.Popen([sys.executable, "-B", str(tmp_path / "owner.py"), str(tmp_path)],
                                 env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                 creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            wait_file(tmp_path / "tree-ready.json", owner, timeout=8)
            info = json.loads((tmp_path / "tree-ready.json").read_text())
            handles = [ProcessHandle(info[key]) for key in ("cli_pid", "leaf_pid")]
            assert all(not handle.terminated() for handle in handles)
            parent = ProcessHandle(owner.pid)
            try:
                parent.terminate(91)
            finally:
                parent.close()
            assert owner.wait(timeout=5) == 91
            assert_tree_dead(tmp_path, handles)
        finally:
            if owner.poll() is None:
                owner.kill()
            owner.wait(timeout=10)
            for handle in handles:
                handle.terminate()
                handle.close()


def test_death_inside_native_creation_before_wrapper_or_runtime_registration_still_kills_tree(tmp_path):
    """The Job must own executable code before any Python registration exists."""
    from lemfolio.runtime import LocalRuntime

    (tmp_path / "leaf.py").write_text(LEAF, encoding="utf-8")
    # The runtime cannot write stdin before spawn returns. This special child
    # executes immediately, proving ownership is active inside native creation.
    (tmp_path / "cli.py").write_text(CLI.replace("sys.stdin.read()\n", ""), encoding="utf-8")
    window_owner = r'''
import json, os, sys, time
from pathlib import Path
import lemfolio.processes as processes
from lemfolio.runtime import LocalRuntime
directory = Path(sys.argv[1])
runtime = LocalRuntime(directory / "runtime")
runtime.capabilities = lambda: {"capabilities":["research.generate"]}
runtime._command = lambda *_: [sys.executable,"-B",str(directory / "cli.py"),str(directory)]
original_create = processes._create_process
def crash_after_native_create(*args, **kwargs):
    native = original_create(*args, **kwargs)
    assert not runtime._processes
    (directory / "registration-window").write_text("no OwnedProcess or runtime registration")
    until = time.monotonic() + 15
    while not (directory / "crash-now").exists():
        if time.monotonic() > until:
            raise RuntimeError("test crash trigger did not arrive")
        time.sleep(.01)
    os._exit(75)
processes._create_process = crash_after_native_create
run = runtime.submit({"projectId":"p","intent":"Native creation crash","baseRevision":"r1",
                      "contextSnapshotId":"c1","context":{"files":{"main.tex":"hello"}},
                      "provider":"codex","mode":"chat"})
(directory / "run.json").write_text(json.dumps({"id":run["id"]}))
while True:
    time.sleep(.1)
'''
    (tmp_path / "owner.py").write_text(window_owner, encoding="utf-8")
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
    handles = []
    with (tmp_path / "owner.log").open("wb") as log:
        owner = subprocess.Popen([sys.executable, "-B", str(tmp_path / "owner.py"), str(tmp_path)],
                                 env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                 creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            wait_file(tmp_path / "registration-window", owner)
            wait_file(tmp_path / "tree-ready.json", owner)
            info = json.loads((tmp_path / "tree-ready.json").read_text())
            handles = [ProcessHandle(info[key]) for key in ("cli_pid", "leaf_pid")]
            assert all(not handle.terminated() for handle in handles)
            run_id = json.loads(wait_file(tmp_path / "run.json", owner))["id"]
            with sqlite3.connect(tmp_path / "runtime/runs.sqlite3") as db:
                assert db.execute("SELECT mechanism FROM run_guards WHERE run_id=?", (run_id,)).fetchone() is None
            (tmp_path / "crash-now").write_text("crash")
            assert owner.wait(timeout=10) == 75
            assert_tree_dead(tmp_path, handles)
            reopened = LocalRuntime(tmp_path / "runtime")
            try:
                assert reopened.inspect(run_id)["status"] == "interrupted"
                interrupted = [event for event in reopened.events(run_id) if event["type"] == "interrupted"]
                assert len(interrupted) == 1
                # A crash before guard persistence remains honestly unknown in
                # durable metadata, even though this test observes both deaths.
                assert interrupted[0]["data"]["process_ownership"] is None
                assert interrupted[0]["data"]["prior_cli_termination_confirmed"] is False
                assert not reopened._processes and not reopened._threads
            finally:
                reopened.close()
        finally:
            if owner.poll() is None:
                owner.kill()
            owner.wait(timeout=10)
            for handle in handles:
                handle.terminate()
                handle.close()
