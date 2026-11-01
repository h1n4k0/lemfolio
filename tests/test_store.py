import hashlib
import io
import json
import os
import sqlite3
import stat
import zipfile

import pytest

from lemfolio.store import Store, StoreError, validate_path


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "store")


def make_zip(files):
    result = io.BytesIO()
    with zipfile.ZipFile(result, "w") as archive:
        for path, content in files.items():
            archive.writestr(path, content)
    return result.getvalue()


def test_project_edit_hash_and_history(store):
    project = store.create_project("研究论文")
    assert project["main_file"] == "main.tex"
    original = store.read_file(project["id"], "main.tex")
    with pytest.raises(StoreError) as stale:
        store.write_file(project["id"], "main.tex", "changed", None)
    assert stale.value.status_code == 409
    changed = store.write_file(project["id"], "main.tex", "changed", original["sha256"])
    assert changed["revision"] != project["revision"]
    history = store.history(project["id"])
    assert len(history) == 2
    assert len(history[0]["commit"]) == 40
    restored = store.restore(project["id"], project["revision"], changed["revision"])
    assert restored["revision"] == project["revision"]
    assert store.read_file(project["id"], "main.tex")["content"] == original["content"]


def test_proposal_binds_digest_and_entire_manifest(store):
    p = store.create_project("Review")
    proposal = store.propose(p["id"], [{"path": "main.tex", "content": "Proposed"}], p["revision"], "Correction")
    assert store.read_file(p["id"], "main.tex")["content"] != "Proposed"
    with pytest.raises(StoreError):
        store.decide(p["id"], proposal["id"], "accept", "wrong")
    store.write_file(p["id"], "other.tex", "Concurrent edit", None)
    with pytest.raises(StoreError, match="stale"):
        store.decide(p["id"], proposal["id"], "accept", proposal["digest"])
    assert store.get_proposal(p["id"], proposal["id"])["status"] == "pending"
    assert store.decide(p["id"], proposal["id"], "reject", proposal["digest"])["status"] == "rejected"


def test_accept_multi_file_proposal_is_idempotent(store):
    p = store.create_project("Edit")
    proposal = store.propose(p["id"], [{"path": "main.tex", "content": "Updated"},
                                      {"path": "sections/result.tex", "content": "Result"},
                                      {"path": "references.bib", "content": None}], p["revision"], "Edit")
    accepted = store.decide(p["id"], proposal["id"], "accept", proposal["digest"])
    assert accepted["status"] == "accepted"
    assert store.text_files(p["id"]) == {"main.tex": "Updated", "sections/result.tex": "Result"}
    assert store.decide(p["id"], proposal["id"], "accept", proposal["digest"]) == accepted
    assert len(store.history(p["id"])) == 2


def test_proposal_tamper_is_detected(store):
    p = store.create_project("Integrity")
    proposal = store.propose(p["id"], [{"path": "main.tex", "content": "X"}], p["revision"], "Edit")
    with sqlite3.connect(store.database) as db:
        db.execute("UPDATE proposals SET payload=? WHERE id=?", ("{}", proposal["id"]))
    with pytest.raises(StoreError, match="integrity"):
        store.get_proposal(p["id"], proposal["id"])


@pytest.mark.parametrize("path", ["../x.tex", "/x.tex", "C:/x.tex", "a\\b.tex", "a/../b.tex", "x.tex:stream",
                                ".git/config", ".latexmkrc", "run.py", "a.ps1", "CON.tex", "a./x.tex", "a//b.tex"])
def test_path_rejections(path):
    with pytest.raises(StoreError):
        validate_path(path)


def test_zip_import_export_assets_and_no_git_metadata(store):
    source = {"paper/main.tex": b"\\documentclass{article}", "figures/test.png": b"\x89PNG\r\n\x1a\n\x00asset"}
    p = store.import_zip("Imported", make_zip(source))
    assert p["main_file"] == "paper/main.tex"
    with zipfile.ZipFile(io.BytesIO(store.export_zip(p["id"]))) as archive:
        assert {name: archive.read(name) for name in archive.namelist()} == source
    assert store.read_bytes(p["id"], "figures/test.png") == source["figures/test.png"]
    with pytest.raises(StoreError):
        store.read_file(p["id"], "figures/test.png")


def test_zip_refuses_traversal_config_collisions_and_symlink(store):
    for files in ({"../x.tex": "x"},
                  {"main.tex": "x", "MAIN.tex": "y"}, {"main.tex": "x", "a.tex": "x", "a.tex/b.tex": "y"}):
        with pytest.raises(StoreError):
            store.import_zip("Bad", make_zip(files))
    result = io.BytesIO()
    with zipfile.ZipFile(result, "w") as archive:
        entry = zipfile.ZipInfo("main.tex")
        entry.create_system = 3
        entry.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(entry, "../../outside")
    with pytest.raises(StoreError):
        store.import_zip("Link", result.getvalue())
    assert store.list_projects() == []


def test_delete_requires_hash_and_restore_preserves_current_backup(store):
    p = store.create_project("Delete")
    original = store.read_file(p["id"], "references.bib")
    with pytest.raises(StoreError):
        store.delete_file(p["id"], "references.bib", None)
    store.delete_file(p["id"], "references.bib", original["sha256"])
    changed = store.get_project(p["id"])
    with pytest.raises(StoreError):
        store.restore(p["id"], p["revision"], p["revision"])
    store.restore(p["id"], p["revision"], changed["revision"])
    assert any(item["message"] == "Before restore" and item["revision"] == changed["revision"]
               for item in store.history(p["id"]))


def test_failed_multi_file_commit_rolls_back_all_files(store, monkeypatch):
    p = store.create_project("Rollback")
    original = store.text_files(p["id"])
    proposal = store.propose(p["id"], [{"path": "main.tex", "content": "new"},
                                      {"path": "new.tex", "content": "new"}], p["revision"], "Edit")
    def fail(*args, **kwargs):
        raise StoreError("Injected snapshot failure", 500)
    monkeypatch.setattr(store, "_snapshot", fail)
    with pytest.raises(StoreError, match="Injected"):
        store.decide(p["id"], proposal["id"], "accept", proposal["digest"])
    assert store.text_files(p["id"]) == original
    assert store.get_proposal(p["id"], proposal["id"])["status"] == "pending"


def test_crash_recovery_rolls_back_uncommitted_transaction(store):
    p = store.create_project("Crash")
    source = store.read_bytes(p["id"], "main.tex")
    with store._transaction() as db:
        store._recover(db)
    _, journal = store._journal(p["id"], {"main.tex": b"uncommitted", "new.tex": b"new"})
    store._atomic(store.project_dir(p["id"]) / "main.tex", b"uncommitted")
    store._atomic(store.project_dir(p["id"]) / "new.tex", b"new")
    reopened = Store(store.root)
    assert reopened.read_bytes(p["id"], "main.tex") == source
    assert "new.tex" not in reopened.text_files(p["id"])
    assert not journal.exists()


def test_committed_journal_survives_restart_without_rollback(store):
    p = store.create_project("Restart")
    source = store.read_file(p["id"], "main.tex")
    store.write_file(p["id"], "main.tex", "committed", source["sha256"])
    reopened = Store(store.root)
    assert reopened.read_file(p["id"], "main.tex")["content"] == "committed"


def test_hard_link_is_rejected(store, tmp_path):
    p = store.create_project("Hard link")
    external = tmp_path / "external.tex"
    external.write_text("outside", encoding="utf-8")
    try:
        os.link(external, store.project_dir(p["id"]) / "linked.tex")
    except OSError:
        pytest.skip("OS does not permit a hard link")
    with pytest.raises(StoreError, match="Unsafe file"):
        store.files(p["id"])


def test_main_source_change_is_bounded(store):
    p = store.create_project("Main source")
    result = store.write_file(p["id"], "chapter.tex", "chapter", None)
    updated = store.set_main_file(p["id"], "chapter.tex", result["revision"])
    assert updated["main_file"] == "chapter.tex"
    with pytest.raises(StoreError):
        store.set_main_file(p["id"], "missing.tex", result["revision"])


def test_asset_upload_binds_hash_and_manifest(store):
    p = store.create_project("Assets")
    first = store.upload_asset(p["id"], "figures/chart.png", b"PNG source", None)
    assert store._manifest(p["id"])["figures/chart.png"] == {"sha256": first["sha256"], "size": 10}
    with pytest.raises(StoreError):
        store.upload_asset(p["id"], "figures/chart.png", b"new", None)
    second = store.upload_asset(p["id"], "figures/chart.png", b"new", first["sha256"])
    assert second["sha256"] != first["sha256"]
    with pytest.raises(StoreError):
        store.upload_asset(p["id"], "run.exe", b"unsafe", None)


def test_no_newline_diff_remains_reviewable(store):
    p = store.create_project("Diff")
    current = store.read_file(p["id"], "main.tex")
    store.write_file(p["id"], "main.tex", "before", current["sha256"])
    project = store.get_project(p["id"])
    proposal = store.propose(p["id"], [{"path": "main.tex", "content": "after"}], project["revision"], "Change")
    assert "-before\n\\ No newline at end of file\n+after\n" in proposal["diff"]


def test_actual_keel_reader_is_used(store, monkeypatch):
    from lemfolio import store as module
    from lemfolio._vendor.keel_files import Read
    p = store.create_project("Reader")
    calls = []
    original = module.read_stable
    def tracked(*args, **kwargs):
        calls.append(args[0])
        return original(*args, **kwargs)
    monkeypatch.setattr(module, "read_stable", tracked)
    store.read_file(p["id"], "main.tex")
    assert len(calls) == 1
    monkeypatch.setattr(module, "read_stable", lambda *a, **kw: Read("unstable", "stat+read+sha256"))
    with pytest.raises(StoreError, match="stably"):
        store.read_file(p["id"], "main.tex")


DOCUMENT = b"\\documentclass{article}\n\\begin{document}x\\end{document}\n"


def test_prism_style_single_project_keeps_paths_below_its_folder(store):
    project = store.import_zip("Imported paper", make_zip({
        "My Paper/paper.tex": DOCUMENT,
        "My Paper/sections/intro.tex": b"intro",
        "My Paper/figures/a.txt": b"a",
        "__MACOSX/My Paper/._paper.tex": b"junk",
    }))
    assert project["name"] == "My Paper"
    assert project["main_file"] == "paper.tex"
    assert {item["path"] for item in store.files(project["id"])} == {"paper.tex", "sections/intro.tex", "figures/a.txt"}


def test_prism_style_export_of_all_projects_becomes_one_project_each(store):
    before = len(store.list_projects())
    result = store.import_archive("prism-export", make_zip({
        "prism/Alpha/main.tex": DOCUMENT,
        "prism/Alpha/conversations/chat-1.json": b"{}",
        "prism/Beta/thesis.tex": DOCUMENT,
        "prism/Beta/ch/one.tex": b"one",
        "prism/export-errors.json": b"[]",
    }))
    assert sorted(item["name"] for item in result["projects"]) == ["Alpha", "Beta"]
    assert len(store.list_projects()) == before + 2
    beta = next(item for item in result["projects"] if item["name"] == "Beta")
    assert beta["main_file"] == "thesis.tex"
    assert "prism/Alpha/conversations/chat-1.json" in result["skipped"]
    assert result["export_errors"] == ["prism/export-errors.json"]
    alpha = next(item for item in result["projects"] if item["name"] == "Alpha")
    assert {item["path"] for item in store.files(alpha["id"])} == {"main.tex"}


def test_chapter_folders_beside_a_root_document_stay_one_project(store):
    result = store.import_archive("Thesis", make_zip({
        "thesis.tex": DOCUMENT,
        "a/one.tex": DOCUMENT,
        "b/two.tex": DOCUMENT,
    }))
    assert [item["name"] for item in result["projects"]] == ["Thesis"]
    assert result["projects"][0]["main_file"] == "thesis.tex"


def test_latexmkrc_names_the_root_document(store):
    project = store.import_zip("Rc", make_zip({
        "a.tex": DOCUMENT,
        "paper/final.tex": DOCUMENT,
        ".latexmkrc": b"@default_files = ('paper/final.tex');\n",
    }))
    assert project["main_file"] == "paper/final.tex"


def test_bad_project_in_an_export_imports_nothing(store):
    before = len(store.list_projects())
    with pytest.raises(StoreError):
        store.import_archive("x", make_zip({
            "A/main.tex": DOCUMENT,
            "B/main.tex": DOCUMENT,
            "B/main.tex/inner.txt": b"conflict",
        }))
    assert len(store.list_projects()) == before


def test_latexmkrc_is_perl_and_never_reaches_the_project(store):
    kept = store.import_zip("Rc left out", make_zip({"main.tex": "x", ".latexmkrc": "evil", "latexmkrc": "evil", "tool.py": "evil"}))
    assert [item["path"] for item in store.files(kept["id"])] == ["main.tex"]
