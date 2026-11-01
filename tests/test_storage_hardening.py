"""Managed-state and bounded compiler lifecycle regressions.

The compiler doubles below exercise orchestration without starting Docker. The
separate test_compiler module retains its real Docker acceptance coverage.
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import uuid
import zipfile

import pytest

from lemfolio import compiler as compiler_module
from lemfolio.compiler import Compiler, CompilerError
from lemfolio.store import Store, StoreError


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "store")


def _change(store, pid, text):
    current = store.read_file(pid, "main.tex")
    return store.write_file(pid, "main.tex", text, current["sha256"])


def _offline_compiler(root, store, monkeypatch, **kwargs):
    compiler = Compiler(root, store, **kwargs)
    monkeypatch.setattr(compiler, "status", lambda: {"available": False, "setup": "unit test"})
    monkeypatch.setattr(compiler, "_remove_container", lambda *_: {"status": "absent"})
    return compiler


def _finished(compiler, record):
    worker = compiler.workers.get(record["id"])
    if worker is not None:
        worker.join(timeout=5)
        assert not worker.is_alive()
    return compiler.inspect(record["id"])


def test_capture_derives_identity_and_text_from_one_scan(store, monkeypatch):
    project = store.create_project("Capture")
    store.upload_asset(project["id"], "figure.png", b"asset", None)
    original = store._scan
    scans = []

    def scan(pid):
        scans.append(pid)
        return original(pid)

    monkeypatch.setattr(store, "_scan", scan)
    snapshot = store.capture_project(project["id"])
    assert scans == [project["id"]]
    assert snapshot["text_files"]["main.tex"].encode() == snapshot["files"]["main.tex"]
    assert "figure.png" not in snapshot["text_files"]
    assert snapshot["source_hashes"] == {p: hashlib.sha256(data).hexdigest() for p, data in snapshot["files"].items()}
    assert snapshot["revision"] == store._revision(snapshot["files"])
    assert snapshot["project"]["state_revision"] == snapshot["state_revision"]
    assert len(snapshot["snapshot_id"]) == 32


def test_capture_blocks_a_managed_writer_until_bytes_and_metadata_are_frozen(store, monkeypatch):
    project = store.create_project("Atomic capture")
    pid = project["id"]
    entered, release, attempted, saved = (threading.Event() for _ in range(4))
    original = store._scan
    snapshots = []

    def scan(project_id):
        if threading.current_thread().name == "capture-audit":
            entered.set()
            assert release.wait(5)
        return original(project_id)

    def edit():
        attempted.set()
        _change(store, pid, "New source")
        saved.set()

    monkeypatch.setattr(store, "_scan", scan)
    capture = threading.Thread(target=lambda: snapshots.append(store.capture_project(pid)), name="capture-audit")
    writer = threading.Thread(target=edit)
    capture.start()
    try:
        assert entered.wait(5)
        writer.start()
        assert attempted.wait(5)
        assert not saved.wait(.05)
    finally:
        release.set()
        capture.join(5)
        writer.join(5)
    assert not capture.is_alive() and not writer.is_alive()
    assert snapshots[0]["revision"] == project["revision"]
    assert snapshots[0]["state_revision"] == project["state_revision"]
    assert saved.is_set()


def test_managed_aba_rejects_old_proposal_even_when_content_revision_matches(store):
    project = store.create_project("ABA")
    pid = project["id"]
    original = store.read_file(pid, "main.tex")["content"]
    proposal = store.propose(pid, [{"path": "main.tex", "content": "Proposed"}], project["revision"], "Edit",
                             base_state_revision=project["state_revision"])
    _change(store, pid, "Intermediate B")
    _change(store, pid, original)
    current = store.get_project(pid)
    assert current["revision"] == project["revision"]
    assert current["generation"] == project["generation"] + 2
    assert current["state_revision"] != project["state_revision"]
    with pytest.raises(StoreError, match="stale"):
        store.decide(pid, proposal["id"], "accept", proposal["digest"])
    with pytest.raises(StoreError, match="state changed"):
        store.propose(pid, [{"path": "main.tex", "content": "New"}], current["revision"], "Stale creation",
                      base_state_revision=project["state_revision"])


def test_main_switch_is_approval_state_and_is_restorable(store):
    project = store.create_project("Entrypoint")
    pid = project["id"]
    store.write_file(pid, "alternate.tex", "Alternate", None)
    before = store.get_project(pid)
    saved = store.snapshot(pid, "Main is main.tex")
    proposal = store.propose(pid, [{"path": "main.tex", "content": "Changed"}], before["revision"], "Edit")
    changed = store.set_main_file(pid, "alternate.tex", before["revision"], base_state_revision=before["state_revision"])
    assert changed["revision"] == before["revision"]
    assert changed["state_revision"] != before["state_revision"]
    assert proposal["main_file"] == "main.tex"
    with pytest.raises(StoreError, match="stale"):
        store.decide(pid, proposal["id"], "accept", proposal["digest"])
    with pytest.raises(StoreError, match="state changed"):
        store.set_main_file(pid, "main.tex", before["revision"], base_state_revision=before["state_revision"])
    with pytest.raises(StoreError, match="state changed"):
        store.restore(pid, saved["commit"], before["revision"], base_state_revision=before["state_revision"])
    restored = store.restore(pid, saved["state_revision"], changed["revision"], base_state_revision=changed["state_revision"])
    assert restored["main_file"] == "main.tex"
    assert restored["generation"] > changed["generation"]
    assert store.get_project(pid)["main_file"] == "main.tex"
    assert any(item["main_file"] == "alternate.tex" for item in store.history(pid))


def test_active_main_cannot_be_deleted_by_save_or_proposal(store):
    project = store.create_project("Keep entrypoint")
    pid = project["id"]
    current = store.read_file(pid, "main.tex")
    with pytest.raises(StoreError, match="active main"):
        store.delete_file(pid, "main.tex", current["sha256"])
    with pytest.raises(StoreError, match="active main"):
        store.propose(pid, [{"path": "main.tex", "content": None}, {"path": "next.tex", "content": "Next"}],
                      project["revision"], "Replace entrypoint")
    assert store.get_project(pid)["state_revision"] == project["state_revision"]


def test_database_failure_after_apply_immediately_restores_files_and_state(store):
    project = store.create_project("SQL failure")
    pid = project["id"]
    original = store.text_files(pid)
    original_head = store._git(pid, ["rev-parse", "HEAD"])
    original_index = (store.project_dir(pid) / ".git" / "index").read_bytes()
    original_history = store.history(pid)
    proposal = store.propose(pid, [{"path": "main.tex", "content": "Never commit"},
                                  {"path": "new.tex", "content": "New"}], project["revision"], "Failure")
    with contextlib.closing(sqlite3.connect(store.database)) as db:
        db.execute("CREATE TRIGGER fail_decision BEFORE UPDATE ON proposals BEGIN SELECT RAISE(ABORT, 'injected SQL failure'); END")
        db.commit()
    with pytest.raises(sqlite3.DatabaseError, match="injected"):
        store.decide(pid, proposal["id"], "accept", proposal["digest"])
    assert store.text_files(pid) == original
    assert store.get_project(pid)["state_revision"] == project["state_revision"]
    assert store.get_proposal(pid, proposal["id"])["status"] == "pending"
    assert store._git(pid, ["rev-parse", "HEAD"]) == original_head
    assert (store.project_dir(pid) / ".git" / "index").read_bytes() == original_index
    assert store.history(pid) == original_history
    assert not list(store.journal_root.iterdir())


def test_commit_failure_immediately_restores_project(store, monkeypatch):
    project = store.create_project("Commit failure")
    original = store.read_file(project["id"], "main.tex")
    original_head = store._git(project["id"], ["rev-parse", "HEAD"])
    original_index = (store.project_dir(project["id"]) / ".git" / "index").read_bytes()
    original_history = store.history(project["id"])

    class FailingCommit(sqlite3.Connection):
        def commit(self):
            raise sqlite3.OperationalError("injected commit failure")

    def connect():
        db = sqlite3.connect(store.database, isolation_level=None, factory=FailingCommit)
        db.row_factory = sqlite3.Row
        return db

    with monkeypatch.context() as patch:
        patch.setattr(store, "_connect", connect)
        with pytest.raises(sqlite3.OperationalError, match="commit failure"):
            store.write_file(project["id"], "main.tex", "Uncommitted", original["sha256"])
    assert store.read_file(project["id"], "main.tex")["content"] == original["content"]
    assert store.get_project(project["id"])["state_revision"] == project["state_revision"]
    assert store._git(project["id"], ["rev-parse", "HEAD"]) == original_head
    assert (store.project_dir(project["id"]) / ".git" / "index").read_bytes() == original_index
    assert store.history(project["id"]) == original_history
    assert not list(store.journal_root.iterdir())


def test_main_only_snapshot_failure_restores_git_ref_index_and_sql(store):
    project = store.create_project("Main metadata abort")
    pid = project["id"]
    store.write_file(pid, "alternate.tex", "Alternate", None)
    original = store.get_project(pid)
    original_files = store.capture_project(pid)["files"]
    original_head = store._git(pid, ["rev-parse", "HEAD"])
    original_index = (store.project_dir(pid) / ".git" / "index").read_bytes()
    original_history = store.history(pid)
    with contextlib.closing(sqlite3.connect(store.database)) as db:
        db.execute("CREATE TRIGGER fail_main_snapshot BEFORE INSERT ON snapshots BEGIN SELECT RAISE(ABORT, 'main snapshot abort'); END")
        db.commit()
    with pytest.raises(sqlite3.DatabaseError, match="main snapshot abort"):
        store.set_main_file(pid, "alternate.tex", original["revision"])
    assert store.get_project(pid) == original
    assert store.capture_project(pid)["files"] == original_files
    assert store._git(pid, ["rev-parse", "HEAD"]) == original_head
    assert (store.project_dir(pid) / ".git" / "index").read_bytes() == original_index
    assert store.history(pid) == original_history


def test_failed_snapshot_preserves_preexisting_index_and_unapproved_worktree_files(store):
    project = store.create_project("Private index rollback")
    pid = project["id"]
    directory = store.project_dir(pid)
    (directory / "staged.tex").write_bytes(b"Already staged external source")
    store._git(pid, ["add", "--", "staged.tex"])
    (directory / "untracked.tex").write_bytes(b"Unapproved external source")
    original_files = store.capture_project(pid)["files"]
    original_head = store._git(pid, ["rev-parse", "HEAD"])
    original_index = (directory / ".git" / "index").read_bytes()
    original_staged = store._git(pid, ["diff", "--cached", "--name-only"])
    with contextlib.closing(sqlite3.connect(store.database)) as db:
        db.execute("CREATE TRIGGER fail_private_snapshot BEFORE INSERT ON snapshots BEGIN SELECT RAISE(ABORT, 'private snapshot abort'); END")
        db.commit()
    with pytest.raises(sqlite3.DatabaseError, match="private snapshot abort"):
        store.snapshot(pid, "Aborted snapshot")
    assert store._git(pid, ["rev-parse", "HEAD"]) == original_head
    assert (directory / ".git" / "index").read_bytes() == original_index
    assert store._git(pid, ["diff", "--cached", "--name-only"]) == original_staged
    assert store.capture_project(pid)["files"] == original_files
    assert b"untracked.tex" in store._git(pid, ["ls-files", "--others", "--exclude-standard"])


def test_restore_failure_after_two_git_commits_restores_original_ref(store):
    project = store.create_project("Restore rollback")
    pid = project["id"]
    _change(store, pid, "Current source before failed restore")
    original = store.get_project(pid)
    original_files = store.capture_project(pid)["files"]
    original_head = store._git(pid, ["rev-parse", "HEAD"])
    original_index = (store.project_dir(pid) / ".git" / "index").read_bytes()
    original_history = store.history(pid)
    with contextlib.closing(sqlite3.connect(store.database)) as db:
        db.execute("CREATE TRIGGER fail_restore_snapshot BEFORE INSERT ON snapshots WHEN NEW.message LIKE 'Restore %' BEGIN SELECT RAISE(ABORT, 'restore snapshot abort'); END")
        db.commit()
    with pytest.raises(sqlite3.DatabaseError, match="restore snapshot abort"):
        store.restore(pid, project["revision"], original["revision"])
    assert store.get_project(pid) == original
    assert store.capture_project(pid)["files"] == original_files
    assert store._git(pid, ["rev-parse", "HEAD"]) == original_head
    assert (store.project_dir(pid) / ".git" / "index").read_bytes() == original_index
    assert store.history(pid) == original_history


@pytest.mark.parametrize("mode", ["content", "main"])
def test_process_death_before_sql_commit_recovers_git_and_project(store, mode):
    project = store.create_project("Git crash recovery")
    pid = project["id"]
    store.write_file(pid, "alternate.tex", "Alternate", None)
    original = store.get_project(pid)
    original_files = store.capture_project(pid)["files"]
    original_head = store._git(pid, ["rev-parse", "HEAD"])
    original_index = (store.project_dir(pid) / ".git" / "index").read_bytes()
    original_history = store.history(pid)
    code = """
import os, sys
from lemfolio.store import Store
store = Store(sys.argv[1])
pid, mode = sys.argv[2:]
snapshot = store._snapshot
def crash(db, project_id, message):
    snapshot(db, project_id, message)
    os._exit(23)
store._snapshot = crash
if mode == 'content':
    current = store.read_file(pid, 'main.tex')
    store.write_file(pid, 'main.tex', 'Crashed uncommitted source', current['sha256'])
else:
    store.set_main_file(pid, 'alternate.tex', store.get_project(pid)['revision'])
"""
    environment = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"),
                       PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run([sys.executable, "-c", code, str(store.root), pid, mode],
                            env=environment, capture_output=True, timeout=20)
    assert result.returncode == 23, result.stderr.decode("utf-8", "replace")
    assert store._git(pid, ["rev-parse", "HEAD"]) != original_head
    reopened = Store(store.root)
    assert reopened.get_project(pid) == original
    assert reopened.capture_project(pid)["files"] == original_files
    assert reopened._git(pid, ["rev-parse", "HEAD"]) == original_head
    assert (reopened.project_dir(pid) / ".git" / "index").read_bytes() == original_index
    assert reopened.history(pid) == original_history
    assert not list(reopened.journal_root.iterdir())


def test_creation_and_zip_import_failure_leave_no_partial_project(store, monkeypatch):
    class FailingCommit(sqlite3.Connection):
        def commit(self):
            raise sqlite3.OperationalError("injected create commit failure")

    def connect():
        db = sqlite3.connect(store.database, isolation_level=None, factory=FailingCommit)
        db.row_factory = sqlite3.Row
        return db

    with monkeypatch.context() as patch:
        patch.setattr(store, "_connect", connect)
        with pytest.raises(sqlite3.OperationalError, match="create commit failure"):
            store.create_project("Never visible")
    assert store.list_projects() == []
    assert not list(store.projects_root.iterdir())

    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("paper.tex", "Imported")
    snapshot = store._snapshot

    def fail_import(db, project_id, message):
        result = snapshot(db, project_id, message)
        if message == "Import ZIP":
            raise StoreError("injected import failure")
        return result

    monkeypatch.setattr(store, "_snapshot", fail_import)
    with pytest.raises(StoreError, match="import failure"):
        store.import_zip("Never partial", archive.getvalue())
    assert store.list_projects() == []
    assert not list(store.projects_root.iterdir())
    assert not list(store.journal_root.iterdir())


def test_old_database_schema_migrates_without_changing_content_revision(tmp_path):
    root = tmp_path / "legacy"
    root.mkdir()
    with contextlib.closing(sqlite3.connect(root / "store.sqlite3")) as db:
        db.execute("CREATE TABLE projects (id TEXT PRIMARY KEY,name TEXT NOT NULL,main_file TEXT NOT NULL,created_at REAL NOT NULL,updated_at REAL NOT NULL)")
        db.commit()
    store = Store(root)
    project = store.create_project("Migrated")
    assert project["generation"] == 0
    assert project["revision"] == store._revision(store.capture_project(project["id"])["files"])
    assert store.history(project["id"])[0]["main_file"] == "main.tex"


def test_compiler_uses_supplied_frozen_capture_and_selected_main(store, tmp_path, monkeypatch):
    project = store.create_project("Frozen build")
    pid = project["id"]
    store.write_file(pid, "alternate.tex", "Frozen alternate", None)
    current = store.get_project(pid)
    store.set_main_file(pid, "alternate.tex", current["revision"])
    snapshot = store.capture_project(pid)
    source = store.read_file(pid, "alternate.tex")
    store.write_file(pid, "alternate.tex", "Live later source", source["sha256"])
    compiler = _offline_compiler(tmp_path / "compiler", store, monkeypatch)
    monkeypatch.setattr(store, "capture_project", lambda *_: pytest.fail("must use the provided capture"))
    record = _finished(compiler, compiler.submit(pid, snapshot=snapshot))
    assert record["main_file"] == "alternate.tex"
    assert record["snapshot_id"] == snapshot["snapshot_id"]
    assert record["state_revision"] == snapshot["state_revision"]
    assert record["source_hashes"] == snapshot["source_hashes"]
    assert (compiler.root / record["id"] / "work" / "alternate.tex").read_bytes() == snapshot["files"]["alternate.tex"]
    assert record["identity_digest"] == compiler._identity(record)
    assert record["legacy_identity"] is False


def test_compiler_rejects_capture_with_mismatched_bytes(store, tmp_path, monkeypatch):
    project = store.create_project("Tampered capture")
    snapshot = store.capture_project(project["id"])
    snapshot["files"]["main.tex"] = b"Different bytes"
    compiler = _offline_compiler(tmp_path / "compiler", store, monkeypatch)
    with pytest.raises(ValueError, match="identity"):
        compiler.submit(project["id"], snapshot=snapshot)
    assert not list(compiler.root.iterdir())


def test_compiler_bounds_active_jobs_and_persists_cancellation(store, tmp_path, monkeypatch):
    project = store.create_project("Admission")
    compiler = _offline_compiler(tmp_path / "compiler", store, monkeypatch, max_workers=1)
    entered, release = threading.Event(), threading.Event()

    def blocked_status():
        entered.set()
        assert release.wait(5)
        return {"available": False, "setup": "unit test"}

    monkeypatch.setattr(compiler, "status", blocked_status)
    build = compiler.submit(project["id"])
    try:
        assert entered.wait(5)
        with pytest.raises(CompilerError) as error:
            compiler.submit(project["id"])
        assert error.value.status_code == 429
        interrupted = compiler.interrupt(build["id"])
        assert interrupted["cancellation_requested"] is True
        assert compiler.inspect(build["id"])["cancellation_requested"] is True
    finally:
        release.set()
        compiler.close()
    assert compiler.inspect(build["id"])["status"] == "cancelled"
    assert not compiler.workers and not compiler.reservations


@pytest.mark.parametrize("limits", [{"max_storage_bytes": 1}, {"max_builds": 1}])
def test_compiler_storage_and_retention_admission_are_finite(store, tmp_path, monkeypatch, limits):
    project = store.create_project("Storage budget")
    compiler = _offline_compiler(tmp_path / "compiler", store, monkeypatch, **limits)
    if "max_builds" in limits:
        _finished(compiler, compiler.submit(project["id"]))
    before = set(compiler.root.iterdir())
    with pytest.raises(CompilerError) as error:
        compiler.submit(project["id"])
    assert error.value.status_code == 507
    assert set(compiler.root.iterdir()) == before


def test_unconfirmed_container_cleanup_blocks_further_admission(store, tmp_path, monkeypatch):
    project = store.create_project("Cleanup failure")
    compiler = _offline_compiler(tmp_path / "compiler", store, monkeypatch)
    monkeypatch.setattr(compiler, "_remove_container", lambda *_: {"status": "failed"})
    terminal = _finished(compiler, compiler.submit(project["id"]))
    assert terminal["cleanup"]["status"] == "failed"
    with pytest.raises(CompilerError) as error:
        compiler.submit(project["id"])
    assert error.value.status_code == 503
    assert compiler.pending_cleanup == {"lemfolio-" + terminal["id"]}


def test_output_accounting_tolerates_latexmk_removing_intermediate_files(tmp_path, monkeypatch):
    survivor = tmp_path / "main.pdf"
    intermediate = tmp_path / "main.tmp"
    survivor.write_bytes(b"PDF")
    intermediate.write_bytes(b"temporary")

    def output_walk(self, pattern):
        yield survivor
        intermediate.unlink()
        yield intermediate

    monkeypatch.setattr(type(tmp_path), "rglob", output_walk)
    assert Compiler._disk_size(tmp_path) == 3


def _legacy_receipt(root, project, status="success"):
    build_id = uuid.uuid4().hex
    job = root / "builds" / build_id
    (job / "work").mkdir(parents=True)
    (job / "out").mkdir()
    record = {"id": build_id, "project_id": project["id"], "base_revision": project["revision"],
              "source_hashes": {"main.tex": "hash"}, "main_file": "main.tex", "engine": "pdflatex",
              "status": status, "created_at": 1, "image_id": "sha256:unit-test", "artifacts": []}
    (job / "receipt.json").write_text(json.dumps(record), encoding="utf-8")
    return record


def test_restart_marks_owned_build_interrupted_without_forging_legacy_identity(store, tmp_path, monkeypatch):
    project = store.create_project("Restart")
    old = _legacy_receipt(tmp_path / "compiler", project, "running")
    removed = []
    monkeypatch.setattr(Compiler, "_remove_container", lambda self, bid: removed.append(bid) or {"status": "absent"})
    compiler = Compiler(tmp_path / "compiler", store)
    record = compiler.inspect(old["id"])
    assert removed == [old["id"]]
    assert record["status"] == "interrupted" and record["completed_at"] > 1
    assert record["cleanup"]["status"] == "absent"
    assert record["legacy_identity"] is True
    assert record["snapshot_id"] is record["state_revision"] is record["identity_digest"] is None
    assert compiler.list_builds(project["id"])[0] == record


def test_restart_retries_failed_cleanup_for_terminal_build_and_synctex(store, tmp_path, monkeypatch):
    project = store.create_project("Cleanup retry")
    root = tmp_path / "compiler"
    old = _legacy_receipt(root, project, "timeout")
    old["cleanup"] = {"status": "failed"}
    job = root / "builds" / old["id"]
    (job / "receipt.json").write_text(json.dumps(old), encoding="utf-8")
    name = "lemfolio-synctex-" + uuid.uuid4().hex
    marker = job / (name.removeprefix("lemfolio-") + ".json")
    marker.write_text(json.dumps({"container_name": name, "status": "running"}), encoding="utf-8")
    removed = []
    monkeypatch.setattr(Compiler, "_remove_named_container", lambda self, container: removed.append(container) or {"status": "removed"})
    compiler = Compiler(root, store)
    assert set(removed) == {"lemfolio-" + old["id"], name}
    assert compiler.inspect(old["id"])["status"] == "timeout"
    assert compiler.inspect(old["id"])["cleanup"]["status"] == "removed"
    assert not marker.exists()


class _Process:
    def __init__(self, returncode=None):
        self.returncode = returncode
        self.killed = False

    def poll(self):
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -9

    def wait(self, timeout=None):
        return self.returncode


def test_compiler_timeout_removes_container_and_stops_client(store, tmp_path, monkeypatch):
    project = store.create_project("Timeout")
    compiler = _offline_compiler(tmp_path / "compiler", store, monkeypatch, timeout=0)
    monkeypatch.setattr(compiler, "status", lambda: {"available": True, "image_id": "sha256:test"})
    process = _Process()
    commands = []
    monkeypatch.setattr(compiler_module.subprocess, "Popen", lambda command, **kwargs: commands.append(command) or process)
    removed = []
    monkeypatch.setattr(compiler, "_remove_container", lambda bid: removed.append(bid) or {"status": "removed"})
    record = _finished(compiler, compiler.submit(project["id"]))
    assert record["status"] == "timeout" and process.killed
    assert removed and all(bid == record["id"] for bid in removed)
    assert record["identity_digest"] == compiler._identity(record)
    command = commands[0]
    for flag in ("--network=none", "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges", "--memory=768m", "--cpus=2", "-no-shell-escape", "-norc"):
        assert flag in command


def test_running_build_cancellation_is_terminal_and_stops_client(store, tmp_path, monkeypatch):
    project = store.create_project("Running cancellation")
    compiler = _offline_compiler(tmp_path / "compiler", store, monkeypatch)
    monkeypatch.setattr(compiler, "status", lambda: {"available": True, "image_id": "sha256:test"})
    process = _Process()
    started = threading.Event()

    def start(*args, **kwargs):
        started.set()
        return process

    monkeypatch.setattr(compiler_module.subprocess, "Popen", start)
    record = compiler.submit(project["id"])
    assert started.wait(5)
    compiler.interrupt(record["id"])
    terminal = _finished(compiler, record)
    assert terminal["status"] == "cancelled" and terminal["cancellation_requested"] is True
    assert process.killed and not compiler.workers and not compiler.reservations


def test_output_limit_is_checked_even_when_compiler_exits_before_first_poll(store, tmp_path, monkeypatch):
    project = store.create_project("Fast oversized output")
    compiler = _offline_compiler(tmp_path / "compiler", store, monkeypatch)
    monkeypatch.setattr(compiler, "status", lambda: {"available": True, "image_id": "sha256:test"})
    monkeypatch.setattr(compiler_module, "OUTPUT_LIMIT", 32)

    def start(command, stdout, **kwargs):
        stdout.write(b"x" * 33)
        stdout.flush()
        return _Process(0)

    monkeypatch.setattr(compiler_module.subprocess, "Popen", start)
    record = _finished(compiler, compiler.submit(project["id"]))
    assert record["status"] == "output_limit"
    assert not compiler.workers and not compiler.reservations


@pytest.mark.parametrize("mode", ["matched", "timeout", "oversize"])
def test_synctex_is_named_bounded_and_always_cleaned(store, tmp_path, monkeypatch, mode):
    project = store.create_project("SyncTeX lifecycle")
    root = tmp_path / "compiler"
    old = _legacy_receipt(root, project)
    compiler = _offline_compiler(root, store, monkeypatch)
    removed, commands, processes = [], [], []
    monkeypatch.setattr(compiler, "_remove_named_container", lambda name: removed.append(name) or {"status": "removed"})
    if mode == "timeout":
        ticks = iter([0, 20])
        monkeypatch.setattr(compiler_module.time, "monotonic", lambda: next(ticks))
    if mode == "oversize":
        monkeypatch.setattr(compiler_module, "QUERY_OUTPUT_LIMIT", 32)

    def start(command, stdout, **kwargs):
        commands.append(command)
        stdout.write(b"Page:1\n" if mode == "matched" else b"x" * (33 if mode == "oversize" else 0))
        stdout.flush()
        process = _Process(None if mode == "timeout" else 0)
        processes.append(process)
        return process

    monkeypatch.setattr(compiler_module.subprocess, "Popen", start)
    if mode == "matched":
        assert compiler.synctex(old["id"], "main.tex")["status"] == "matched"
    else:
        with pytest.raises(CompilerError):
            compiler.synctex(old["id"], "main.tex")
    command = commands[0]
    name = command[command.index("--name") + 1]
    assert name.startswith("lemfolio-synctex-") and removed == [name]
    for flag in ("--network=none", "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges", "--cpus=1", "--user", "--ulimit"):
        assert flag in command
    assert not compiler.queries and not compiler.reservations
    assert not list((root / "builds" / old["id"]).glob("synctex-*.log"))
    if mode == "timeout":
        assert processes[0].killed


def test_close_waits_for_active_synctex_cancellation(store, tmp_path, monkeypatch):
    project = store.create_project("Query shutdown")
    root = tmp_path / "compiler"
    old = _legacy_receipt(root, project)
    compiler = _offline_compiler(root, store, monkeypatch)
    monkeypatch.setattr(compiler, "_remove_named_container", lambda *_: {"status": "removed"})
    started = threading.Event()
    process = _Process()
    errors = []

    def start(*args, **kwargs):
        started.set()
        return process

    def query():
        try:
            compiler.synctex(old["id"], "main.tex")
        except CompilerError as error:
            errors.append(error)

    monkeypatch.setattr(compiler_module.subprocess, "Popen", start)
    thread = threading.Thread(target=query)
    thread.start()
    assert started.wait(5)
    compiler.close()
    thread.join(5)
    assert not thread.is_alive() and errors and process.killed
    assert not compiler.queries and not compiler.query_events and not compiler.reservations
    with pytest.raises(CompilerError) as error:
        compiler.submit(project["id"])
    assert error.value.status_code == 503
