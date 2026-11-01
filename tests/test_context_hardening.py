"""Regression cases for omitted source, byte budgets and guarded edit admission."""
import hashlib
import json

import pytest

from lemfolio.harness import select_context
from lemfolio.runtime import RuntimeError, validate_result_for_task, validate_task


def frozen_task(files, selection=None, **kwargs):
    return {"projectId": "p", "intent": "Edit the selected passage", "baseRevision": "r",
            "contextSnapshotId": "s", "mode": "edit", "provider": "codex",
            "sourceManifest": {path: hashlib.sha256(text.encode()).hexdigest() for path, text in files.items()},
            "context": {"selection": select_context(files, "Edit the selected passage", selection, **kwargs)}}


def result(change):
    return {"answer": "A guarded proposal", "changes": [change], "checks": []}


def test_truncated_large_file_cannot_be_replaced_but_visible_local_edit_can():
    original = "KEEP_PREFIX\n" + "ordinary source\n" * 10000 + "\\section{Target}\nEdit this line.\nKEEP_SUFFIX\n"
    task = frozen_task({"main.tex": original}, {"path": "main.tex", "start_line": 10003})
    context = task["context"]["selection"]
    assert context["truncated"]
    assert not context["coverage"]["main.tex"]["complete"]
    excerpt = context["files"][0]["content"]
    assert "KEEP_PREFIX" not in excerpt
    with pytest.raises(RuntimeError, match="complete source context"):
        validate_result_for_task(result({"path": "main.tex", "content": excerpt, "edits": None}), task)
    proposal = result({"path": "main.tex", "content": None,
                       "edits": [{"old_text": "Edit this line.", "new_text": "Edited."}]})
    assert validate_result_for_task(proposal, task) == proposal
    with pytest.raises(RuntimeError, match="visible source excerpt"):
        validate_result_for_task(result({"path": "main.tex", "content": None,
                                         "edits": [{"old_text": "KEEP_PREFIX", "new_text": "lost"}]}), task)
    assert original.startswith("KEEP_PREFIX")  # Validation never mutates source.


def test_unseen_existing_path_is_distinct_from_new_path():
    files = {"main.tex": "selected " * 500, **{f"f{i:02}.tex": "filler " * 500 for i in range(10)}}
    task = frozen_task(files, {"path": "main.tex"}, max_chars=1000)
    context = task["context"]["selection"]
    unseen = next(path for path, info in context["coverage"].items() if not info["ranges"])
    with pytest.raises(RuntimeError, match="complete source context"):
        validate_result_for_task(result({"path": unseen, "content": "replacement"}), task)
    with pytest.raises(RuntimeError, match="exact existing source path"):
        validate_result_for_task(result({"path": unseen.upper(), "content": "replacement"}), task)
    assert validate_result_for_task(result({"path": "new.tex", "content": "new source"}), task)


def test_complete_source_hash_and_ranges_are_checked_not_just_flag():
    task = frozen_task({"main.tex": "完整原稿\nSecond line.\n"})
    proposal = result({"path": "main.tex", "content": "完整原稿\nEdited line.\n"})
    assert validate_result_for_task(proposal, task)
    task["context"]["selection"]["files"][0]["content"] = "forged"
    with pytest.raises(RuntimeError, match="inconsistent ranges|frozen file digest"):
        validate_result_for_task(proposal, task)


def test_original_bom_digest_and_byte_ranges_survive_decoded_context():
    source = "原稿\r\nA local sentence.\r\n"
    original = b"\xef\xbb\xbf" + source.encode()
    digest = hashlib.sha256(original).hexdigest()
    context = select_context({"main.tex": source}, "Edit sentence", source_hashes={"main.tex": digest})
    info, excerpt = context["coverage"]["main.tex"], context["files"][0]
    assert info["sha256"] == excerpt["sha256"] == digest
    assert info["utf8_bom"] is True and info["total_bytes"] == len(original)
    assert info["text_sha256"] == hashlib.sha256(source.encode()).hexdigest()
    assert excerpt["start_char"] == 0 and excerpt["start_byte"] == 3
    assert original[excerpt["start_byte"]:excerpt["end_byte"]] == excerpt["content"].encode()
    task = {"sourceManifest": {"main.tex": digest}, "context": {"selection": context}}
    assert validate_result_for_task(result({"path": "main.tex", "content": source.replace("local", "guarded")}), task)
    assert validate_result_for_task(result({"path": "main.tex", "content": None,
                                           "edits": [{"old_text": "A local sentence.", "new_text": "A guarded sentence."}]}), task)
    with pytest.raises(ValueError, match="Authoritative source hash"):
        select_context({"main.tex": source}, "Edit", source_hashes={"main.tex": "0" * 64})


@pytest.mark.parametrize("change", [
    {"path": "main.tex", "content": "replacement", "edits": [{"old_text": "old", "new_text": "new"}]},
    {"path": "main.tex", "content": None, "edits": None},
    {"path": "main.tex", "content": None, "edits": []},
    {"path": "main.tex", "content": None, "edits": [{"old_text": "", "new_text": "new"}]},
    {"path": "main.tex", "content": None, "edits": [{"old_text": "old", "new_text": 3}]},
])
def test_change_modes_and_guards_are_strictly_validated(change):
    with pytest.raises(RuntimeError):
        validate_result_for_task(result(change), frozen_task({"main.tex": "old"}))


def test_include_dependency_precedes_unrelated_files_and_offsets_bind_utf8():
    files = {"main.tex": "\\section{Selected}\n\\input{a_defs}\n" + "选定正文 " * 1000,
             "z1.tex": "irrelevant " * 300, "z2.tex": "irrelevant " * 300,
             "z3.tex": "irrelevant " * 300,
             "a_defs.tex": "\\newcommand{\\domain}{CRITICAL_DEFINITION}\n" + "定义 " * 300}
    context = select_context(files, "Edit selected paragraph", {"path": "main.tex", "start_line": 2}, max_chars=1000)
    assert [item["path"] for item in context["files"]][:2] == ["main.tex", "a_defs.tex"]
    assert any("CRITICAL_DEFINITION" in item["content"] for item in context["files"])
    assert "file:a_defs.tex" in context["related_nodes"]
    assert context["context_chars"] <= 1000
    for item in context["files"]:
        source = files[item["path"]]
        assert item["content"] == source[item["start_char"]:item["end_char"]]
        assert item["content"].encode() == source.encode()[item["start_byte"]:item["end_byte"]]
        assert item["sha256"] == hashlib.sha256(source.encode()).hexdigest()
    assert any(item["kind"] == "source" for item in context["omissions"])
    assert context == select_context(dict(reversed(list(files.items()))), "Edit selected paragraph",
                                     {"path": "main.tex", "start_line": 2}, max_chars=1000)


def test_text_and_large_structure_share_exact_serialized_byte_budget():
    source = "".join("\\section{" + str(i) + "_" + "题" * 1700 + "}\nBody.\n" for i in range(600))
    context = select_context({"main.tex": source}, "review", {"path": "main.tex"})
    encoded = json.dumps(context, ensure_ascii=False, separators=(",", ":")).encode()
    assert len(encoded) == context["context_bytes"] <= 240000
    assert context["context_chars"] <= 60000
    assert len(context["structure"]) < 600
    assert any(item["kind"] == "structure" for item in context["omissions"])
    assert context["coverage"]["main.tex"]["sha256"] == hashlib.sha256(source.encode()).hexdigest()


def test_safety_metadata_has_an_explicit_budget_failure():
    files = {f"file{i}.tex": "source" for i in range(30)}
    with pytest.raises(ValueError, match="coverage metadata exceeds the byte budget"):
        select_context(files, "review", max_bytes=2000)


def test_task_admission_is_pure_freezes_input_and_rejects_oversize():
    task = frozen_task({"main.tex": "source"})
    frozen = validate_task(task)
    task["context"]["selection"]["files"][0]["content"] = "mutated"
    assert frozen["context"]["selection"]["files"][0]["content"] == "source"
    task["context"]["oversize"] = "x" * 1_000_001
    with pytest.raises(RuntimeError) as error:
        validate_task(task)
    assert error.value.status_code == 413


def test_legacy_excerpt_cannot_be_misclassified_as_new_file():
    digest = hashlib.sha256(b"unseen prefix plus visible tail").hexdigest()
    task = {"projectId": "p", "intent": "Edit", "baseRevision": "r", "contextSnapshotId": "s",
            "context": {"selection": {"files": [{"path": "main.tex", "content": "visible tail",
                                                   "sha256": digest, "truncated": True}]}}, "mode": "edit"}
    frozen = validate_task(task)
    assert frozen["sourceManifest"] == {"main.tex": digest}
    with pytest.raises(RuntimeError, match="complete source context"):
        validate_result_for_task(result({"path": "main.tex", "content": "visible replacement"}), frozen)
    task["sourceManifest"] = {}
    with pytest.raises(RuntimeError, match="manifest does not match"):
        validate_task(task)
