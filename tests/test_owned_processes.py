"""Native admission fault tests: never fall back to an unowned executable."""
import ctypes
import os
import subprocess
import sys
import threading

import pytest

from lemfolio import processes


pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows Job lifecycle boundary")


@pytest.mark.parametrize("stage", ["sizing", "initialize", "job_attribute", "handle_attribute", "create"])
def test_native_admission_failure_closes_job_and_pipe_handles_without_executing(tmp_path, monkeypatch, stage):
    job_handles, child_handles = [], []
    original_new_job = processes._new_job
    original_duplicate = processes._duplicate_handle
    original_initialize = processes._kernel.InitializeProcThreadAttributeList
    original_update = processes._kernel.UpdateProcThreadAttribute
    initialized = []

    def new_job():
        value = original_new_job()
        job_handles.append(value)
        return value

    def duplicate(handle, **kwargs):
        value = original_duplicate(handle, **kwargs)
        child_handles.append(value)
        return value

    def initialize(buffer, count, flags, size):
        if (stage == "sizing" and buffer is None) or (stage == "initialize" and buffer is not None):
            ctypes.set_last_error(5)
            return False
        return original_initialize(buffer, count, flags, size)

    def update(buffer, flags, attribute, value, size, previous, returned):
        if (stage == "job_attribute" and attribute == processes._JOB_LIST
                or stage == "handle_attribute" and attribute == processes._HANDLE_LIST):
            ctypes.set_last_error(5)
            return False
        return original_update(buffer, flags, attribute, value, size, previous, returned)

    def create(*args):
        # This must only be reached for the deliberate CreateProcess failure.
        assert stage == "create"
        initialized.append(True)
        ctypes.set_last_error(5)
        return False

    monkeypatch.setattr(processes, "_new_job", new_job)
    monkeypatch.setattr(processes, "_duplicate_handle", duplicate)
    monkeypatch.setattr(processes._kernel, "InitializeProcThreadAttributeList", initialize)
    monkeypatch.setattr(processes._kernel, "UpdateProcThreadAttribute", update)
    monkeypatch.setattr(processes._kernel, "CreateProcessW", create)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("unprotected fallback"))
    marker = tmp_path / "must-not-run"
    with pytest.raises(OSError):
        processes.spawn_owned([sys.executable, "-c", "from pathlib import Path; Path(__import__('sys').argv[1]).write_text('ran')", str(marker)])
    assert not marker.exists()
    assert len(job_handles) == 1 and len(child_handles) == 3
    assert bool(initialized) == (stage == "create")
    for handle in job_handles + child_handles:
        with pytest.raises(OSError):
            os.get_handle_inheritable(handle)


def test_job_policy_failure_closes_job_before_process_creation(monkeypatch):
    created = []
    original_create = processes._kernel.CreateJobObjectW

    def create(*args):
        value = original_create(*args)
        created.append(value)
        return value

    def refuse(*args):
        ctypes.set_last_error(5)
        return False

    monkeypatch.setattr(processes._kernel, "CreateJobObjectW", create)
    monkeypatch.setattr(processes._kernel, "SetInformationJobObject", refuse)
    monkeypatch.setattr(processes, "_create_process", lambda *a: pytest.fail("launch after failed job policy"))
    with pytest.raises(OSError):
        processes.spawn_owned([sys.executable, "-c", "raise SystemExit(0)"])
    assert len(created) == 1
    with pytest.raises(OSError):
        os.get_handle_inheritable(created[0])


def test_owned_pipes_environment_quoting_and_noninheritable_job(tmp_path, monkeypatch):
    command = [sys.executable, "-c", "import os,sys,json;data=sys.stdin.buffer.read(); print(json.dumps([os.getcwd(),os.getenv('OWNED_SENTINEL'),sys.argv[1],data.decode()]));sys.stderr.write('diagnostic')", 'space "quote" \\ tail']
    original = processes._create_process
    policy = []

    def inspect(job, args, cwd, env, handles):
        policy.append((os.get_handle_inheritable(job), job in handles, len(handles)))
        return original(job, args, cwd, env, handles)

    monkeypatch.setattr(processes, "_create_process", inspect)
    process = processes.spawn_owned(command, cwd=tmp_path, env=dict(os.environ, OWNED_SENTINEL="unicode 研究"))
    try:
        process.stdin.write("input 内容".encode())
        process.stdin.close()
        output, error = process.stdout.read(), process.stderr.read()
        assert process.wait(timeout=5) == 0
        import json
        assert json.loads(output) == [str(tmp_path), "unicode 研究", 'space "quote" \\ tail', "input 内容"]
        assert error == b"diagnostic"
        assert policy == [(False, False, 3)]
    finally:
        process.close()
    process.close()
    processes.kill_owned(process)
    assert process.poll() == 0


def test_concurrent_waiters_remain_safe_while_close_releases_original_handles():
    process = processes.spawn_owned([sys.executable, "-c", "import time;time.sleep(30)"])
    finished, failures = [], []
    ready = threading.Barrier(5)

    def waiter():
        try:
            ready.wait(timeout=5)
            finished.append(process.wait(timeout=10))
        except Exception as exc:
            failures.append(exc)

    threads = [threading.Thread(target=waiter) for _ in range(4)]
    for thread in threads:
        thread.start()
    ready.wait(timeout=5)
    process.close()
    for thread in threads:
        thread.join(timeout=10)
    assert not any(thread.is_alive() for thread in threads)
    assert failures == [] and len(finished) == 4
    assert all(code == process.returncode for code in finished)
    assert process.returncode is not None and process._job_handle is None and process._process_handle is None


def test_probe_output_limit_refuses_unbounded_status_output():
    with pytest.raises(OSError, match="output limit"):
        processes.run_owned([sys.executable, "-c", "import sys,time;sys.stdout.buffer.write(b'x'*1100000);sys.stdout.flush();time.sleep(30)"], timeout=5)


def test_probe_timeout_does_not_become_success():
    with pytest.raises(subprocess.TimeoutExpired):
        processes.run_owned([sys.executable, "-c", "import time;time.sleep(30)"], timeout=.1)


def test_probe_reader_start_failure_still_terminates_owned_process(monkeypatch):
    captured = []
    original_spawn = processes.spawn_owned
    original_start = threading.Thread.start
    starts = []

    def capture(*args, **kwargs):
        result = original_spawn(*args, **kwargs)
        captured.append(result)
        return result

    def start(thread):
        starts.append(thread)
        if len(starts) == 2:
            raise RuntimeError("injected reader startup failure")
        return original_start(thread)

    monkeypatch.setattr(processes, "spawn_owned", capture)
    monkeypatch.setattr(threading.Thread, "start", start)
    with pytest.raises(RuntimeError, match="injected reader startup failure"):
        processes.run_owned([sys.executable, "-c", "import time;time.sleep(30)"], timeout=5)
    assert len(captured) == 1
    process = captured[0]
    assert process.poll() is not None
    assert process._job_handle is None and process._process_handle is None
    assert process.stdout.closed and process.stderr.closed
    assert not starts[0].is_alive()


def test_thread_handle_cleanup_error_does_not_leave_created_process_alive(monkeypatch):
    original_create = processes._kernel.CreateProcessW
    original_close = processes._close_handle
    created = []
    observers = []
    injected = []

    def create(*args):
        succeeded = original_create(*args)
        if succeeded:
            info = ctypes.cast(args[-1], ctypes.POINTER(processes._ProcessInformation)).contents
            created.append((info.hProcess, info.hThread))
            observers.append(processes._duplicate_handle(info.hProcess))
        return succeeded

    def close(handle):
        original_close(handle)
        if created and handle == created[0][1] and not injected:
            injected.append(True)
            raise OSError("injected post-release thread handle failure")

    monkeypatch.setattr(processes._kernel, "CreateProcessW", create)
    monkeypatch.setattr(processes, "_close_handle", close)
    try:
        with pytest.raises(OSError, match="post-release thread handle failure"):
            processes.spawn_owned([sys.executable, "-c", "import time;time.sleep(30)"])
        assert len(observers) == 1 and injected == [True]
        assert processes._kernel.WaitForSingleObject(observers[0], 5000) == processes._WAIT_OBJECT_0
        for handle in created[0]:
            with pytest.raises(OSError):
                os.get_handle_inheritable(handle)
    finally:
        for observer in observers:
            original_close(observer)


def test_broken_stdin_close_does_not_skip_other_streams_or_process_handle():
    process = processes.spawn_owned([sys.executable, "-c", "import time;time.sleep(30)"])
    original_stdin = process.stdin

    class BrokenStdin:
        def close(self):
            raise BrokenPipeError("simulated buffered stdin flush after cancellation")

    process.stdin = BrokenStdin()
    try:
        process.close()
        assert process.stdout.closed and process.stderr.closed
        assert process._job_handle is None and process._process_handle is None
        assert process.poll() is not None
    finally:
        original_stdin.close()
        process.close()


def test_temporary_child_handle_close_failure_cannot_strand_successful_launch(monkeypatch):
    original_create = processes._create_process
    original_close = processes._close_handle
    captured = []
    temporary = []
    injected = []

    def create(job, args, cwd, env, handles):
        temporary.extend(handles)
        pid, handle = original_create(job, args, cwd, env, handles)
        captured.append((job, handle, processes._duplicate_handle(handle)))
        return pid, handle

    def close(handle):
        if temporary and handle == temporary[0] and not injected:
            injected.append(True)
            raise OSError("injected temporary handle release failure")
        original_close(handle)

    monkeypatch.setattr(processes, "_create_process", create)
    monkeypatch.setattr(processes, "_close_handle", close)
    try:
        with pytest.raises(OSError, match="temporary handle release failure"):
            processes.spawn_owned([sys.executable, "-c", "import time;time.sleep(30)"])
        assert len(captured) == 1
        job, process, observer = captured[0]
        assert processes._kernel.WaitForSingleObject(observer, 5000) == processes._WAIT_OBJECT_0
        for handle in temporary + [job, process]:
            with pytest.raises(OSError):
                os.get_handle_inheritable(handle)
    finally:
        for _, _, observer in captured:
            original_close(observer)
