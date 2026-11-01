"""The workflow view projects real records and cannot execute or approve them."""
from copy import deepcopy

import pytest

from lemfolio.contracts import WorkflowView
from lemfolio.workflow import build_workflow


def project(state="A", main="main.tex"):
    return {"id": "paper", "state_revision": state, "revision": "files-" + state, "main_file": main}


def run(status="completed", state="A", changes=True, identity="run-1"):
    return {"id": identity, "status": status, "error": "Provider failure" if status == "failed" else None,
            "task": {"projectId": "paper", "baseStateRevision": state, "baseRevision": "files-" + state,
                     "main_file": "main.tex", "provider": "codex", "mode": "edit",
                     "sourceSnapshotId": "source-1", "contextSnapshotId": "context-1",
                     "sourceSnapshotDigest": "source-archive-digest", "sourceManifest": {"main.tex": "source-digest"},
                     "context": {"selection": {"coverage": {"main.tex": {"complete": True}},
                                                "truncated": False, "context_chars": 10, "context_bytes": 200,
                                                "omissions": []},
                                 "domain_evidence_id": "tools-1",
                                 "domain": {"tool_evidence": [], "structural_verification": {"status": "passed"},
                                            "domain_acceptance": {"status": "partial", "missing": ["compilation"],
                                                                  "failed": [], "uncertain": [], "research_accepted": False}}}},
            "result": {"answer": "Proposed", "changes": [{"path": "main.tex", "content": "updated"}] if changes else [], "checks": []}
                      if status == "completed" else None}


def proposal(status="pending", identity="proposal-1", state="A", output="B"):
    return {"id": identity, "project_id": "paper", "created_at": 1,
            "base_state_revision": state, "base_revision": "files-" + state, "main_file": "main.tex",
            "status": status, "digest": "digest-1", "summary": "A guarded change",
            "result_state_revision": output if status == "accepted" else None,
            "result_revision": "files-" + output if status == "accepted" else None}


def link(run_id="run-1", proposal_id="proposal-1", created_at=1):
    return {"id": "link-" + proposal_id, "project_id": "paper", "kind": "proposal_created", "created_at": created_at,
            "state_revision": "A", "result": {"run_id": run_id, "proposal_id": proposal_id}}


def build(status="success", state="B", proof=True, identity="build-1"):
    return {"id": identity, "project_id": "paper", "created_at": 2, "status": status,
            "state_revision": state, "main_file": "main.tex", "engine": "pdflatex",
            "verified_identity": proof, "artifacts_verified": proof and status == "success",
            "artifacts": [{"name": "main.pdf", "sha256": "actual-pdf-digest", "size": 12}] if status == "success" else [],
            "error": "Compilation failed" if status == "failed" else None}


def verification(status="human_review_required", state="B", identity="verification-1", run_id="run-1", proposal_id="proposal-1"):
    return {"id": identity, "project_id": "paper", "kind": "research_verification", "created_at": 3,
            "base_revision": "files-" + state, "state_revision": state, "main_file": "main.tex",
            "current_state_matches": True,
            "result": {"run_id": run_id, "proposal_id": proposal_id, "build_id": "build-1", "engine": "pdflatex",
                       "verification_status": "failed" if status == "failed" else "queued",
                       "verification_error": "Independent check failed" if status == "failed" else None},
            "verification": {"status": status, "research_accepted": False, "human_review_required": True,
                             "missing": [], "failed": ["compilation"] if status == "failed" else [], "uncertain": []}}


def nodes(value):
    return {node["id"]: node for node in value["nodes"]}


def action(node, kind):
    return next(value for value in node["actions"] if value["kind"] == kind)


def test_no_records_is_ready_source_without_inventing_later_completion():
    view = build_workflow(project(), None, [], [], [])
    WorkflowView.model_validate(view)
    stage = nodes(view)
    assert stage["source"]["status"] == "passed"
    assert all(stage[name]["status"] == "not_run" for name in stage if name != "source")
    assert view["run_id"] is None and view["output_state_revision"] is None
    assert view["input_state_revision"] == view["current_state_revision"] == "A"
    assert [edge["source"] for edge in view["edges"]] == ["source", "context", "tools", "model", "proposal", "approval", "compile"]
    assert action(stage["model"], "submit_run")["enabled"]


@pytest.mark.parametrize("status,expected", [("queued", "running"), ("running", "running"),
                                             ("failed", "failed"), ("interrupted", "interrupted")])
def test_runtime_states_are_real_provider_states_without_fake_downstream_work(status, expected):
    stage = nodes(build_workflow(project(), run(status), [], [], []))
    assert stage["model"]["status"] == expected
    assert stage["context"]["status"] == stage["tools"]["status"] == "passed"
    assert stage["proposal"]["status"] == stage["approval"]["status"] == "not_run"
    assert stage["compile"]["status"] == stage["verification"]["status"] == "not_run"


def test_completed_generation_only_exposes_create_proposal_not_approval():
    view = build_workflow(project(), run(), [], [], [])
    WorkflowView.model_validate(view)
    stage = nodes(view)
    assert stage["model"]["status"] == "passed"
    assert stage["model"]["details"]["research_accepted"] is False
    assert stage["proposal"]["status"] == "pending_review"
    create = action(stage["proposal"], "create_proposal")
    assert create["enabled"] and create["target_id"] == "run-1"
    assert stage["approval"]["status"] == "blocked"
    assert stage["approval"]["details"]["research_accepted"] is False


def test_only_linked_proposal_is_reviewable_and_no_auto_approval_occurs():
    unrelated = proposal(identity="unrelated")
    unrelated["created_at"] = 100
    stage = nodes(build_workflow(project(), run(), [proposal(), unrelated], [link()], []))
    assert stage["proposal"]["details"]["proposal_id"] == "proposal-1"
    assert stage["proposal"]["status"] == "passed"
    assert stage["approval"]["status"] == "pending_review"
    assert action(stage["approval"], "review_proposal") == {
        "kind": "review_proposal", "target_id": "proposal-1", "enabled": True, "reason": None}
    assert stage["approval"]["details"]["human_content_approval"] is False


def test_input_output_and_current_states_remain_distinct_after_source_approval():
    view = build_workflow(project("B"), run(), [proposal("accepted")], [link(), verification()], [build()])
    WorkflowView.model_validate(view)
    stage = nodes(view)
    assert (view["input_state_revision"], view["output_state_revision"], view["current_state_revision"]) == ("A", "B", "B")
    assert stage["source"]["historical"] and stage["source"]["state_revision"] == "A"
    assert stage["approval"]["status"] == "passed" and stage["approval"]["state_revision"] == "B"
    assert stage["compile"]["status"] == "passed" and not stage["compile"]["historical"]
    assert stage["verification"]["status"] == "pending_review"
    assert stage["verification"]["details"]["research_accepted"] is False
    assert not action(stage["verification"], "verify_run")["enabled"]


@pytest.mark.parametrize("missing", ["verified_identity", "artifacts_verified", "artifacts"])
def test_success_word_without_bound_identity_and_actual_pdf_is_not_compile_pass(missing):
    receipt = build()
    receipt.pop(missing)
    stage = nodes(build_workflow(project("B"), run(), [proposal("accepted")], [link(), verification()], [receipt]))
    assert stage["compile"]["status"] == "blocked"
    assert stage["verification"]["status"] == "blocked"


def test_failed_compilation_and_verification_can_retry_only_same_current_state():
    records = [link(), verification("failed")]
    stage = nodes(build_workflow(project("B"), run(), [proposal("accepted")], records, [build("failed")]))
    assert stage["compile"]["status"] == stage["verification"]["status"] == "failed"
    assert action(stage["verification"], "retry_verification")["enabled"]
    historical = nodes(build_workflow(project("C", "alternate.tex"), run(), [proposal("accepted")], records, [build("failed")]))
    assert historical["verification"]["historical"] and historical["compile"]["historical"]
    assert not action(historical["verification"], "retry_verification")["enabled"]
    assert not action(historical["compile"], "compile")["enabled"]


def test_failed_verification_start_is_retryable_without_invented_build():
    record = verification("failed")
    record["result"]["build_id"] = None
    record["result"]["verification_error"] = "Compiler admission failed"
    stage = nodes(build_workflow(project("B"), run(), [proposal("accepted")], [link(), record], []))
    assert stage["compile"]["status"] == "not_run"
    assert stage["verification"]["status"] == "failed"
    assert action(stage["verification"], "retry_verification")["enabled"]


def test_other_run_records_cannot_fill_selected_runs_missing_stages():
    view = build_workflow(project(), run(changes=False, identity="run-2"), [proposal("accepted")],
                          [link(), verification()], [build(state="A")])
    stage = nodes(view)
    assert stage["proposal"]["status"] == stage["approval"]["status"] == "not_run"
    assert stage["compile"]["status"] == stage["verification"]["status"] == "not_run"
    assert stage["compile"]["evidence_refs"] == stage["verification"]["evidence_refs"] == []


@pytest.mark.parametrize("changes", [False, True])
def test_selected_unapproved_run_cannot_launch_an_unassociated_invisible_build(changes):
    stage = nodes(build_workflow(project(), run(changes=changes), [], [], []))
    direct_compile = action(stage["compile"], "compile")
    assert direct_compile["enabled"] is False
    assert direct_compile["reason"] == "请使用独立验证任务来绑定输入编译，或接受提案后编译"
    # This route creates a verification/build linkage that the projection can
    # display; it remains an explicit action and is never executed here.
    assert action(stage["verification"], "verify_run")["enabled"] is True
    assert stage["compile"]["status"] == "not_run"


def test_direct_compile_remains_available_for_manual_or_approved_output_source():
    manual = nodes(build_workflow(project(), None, [], [], []))
    assert action(manual["compile"], "compile")["enabled"] is True
    approved = nodes(build_workflow(project("B"), run(), [proposal("accepted")], [link()], []))
    assert action(approved["compile"], "compile")["enabled"] is True
    assert approved["compile"]["state_revision"] == "B"


def test_manual_proposal_and_manual_build_remain_actual_records_without_a_run():
    stage = nodes(build_workflow(project(), None, [proposal()], [], [build(state="A")]))
    assert stage["context"]["status"] == stage["tools"]["status"] == stage["model"]["status"] == "not_run"
    assert stage["proposal"]["status"] == "passed" and stage["approval"]["status"] == "pending_review"
    assert stage["compile"]["status"] == "passed"
    assert stage["verification"]["status"] == "not_run"


def test_historical_manual_build_can_show_its_proven_pass_without_enabling_stale_compile():
    stage = nodes(build_workflow(project("C", "alternate.tex"), None, [], [], [build(state="A")]))
    assert stage["source"]["state_revision"] == "C"
    assert stage["compile"]["state_revision"] == "A" and stage["compile"]["historical"]
    assert stage["compile"]["status"] == "passed"
    assert not action(stage["compile"], "compile")["enabled"]


def test_approved_output_build_remains_visible_after_current_entry_changes():
    stage = nodes(build_workflow(project("C", "alternate.tex"), run(), [proposal("accepted")], [link()], [build()]))
    assert stage["compile"]["status"] == "passed" and stage["compile"]["historical"]
    assert stage["compile"]["state_revision"] == "B"
    assert not action(stage["compile"], "compile")["enabled"]


def test_entry_change_marks_history_and_blocks_old_mutations_even_for_bad_state_metadata():
    stage = nodes(build_workflow(project("A", "alternate.tex"), run(), [proposal()], [link()], []))
    assert stage["source"]["historical"] and stage["context"]["historical"]
    assert stage["approval"]["status"] == "blocked"
    assert not action(stage["approval"], "review_proposal")["enabled"]
    assert not action(stage["verification"], "verify_run")["enabled"]


def test_foreign_project_run_cannot_expose_mutation_actions():
    foreign = run()
    foreign["task"]["projectId"] = "another-paper"
    stage = nodes(build_workflow(project(), foreign, [proposal()], [link()], [build(state="A")]))
    assert stage["model"]["status"] == "blocked"
    assert stage["model"]["evidence_refs"] == []
    for value in stage.values():
        assert all(not action["enabled"] for action in value["actions"] if action["kind"] not in {"inspect_source", "submit_run"})


def test_legacy_unknown_source_binding_does_not_admit_changes_or_verification():
    legacy = run()
    legacy["task"].pop("baseStateRevision")
    stage = nodes(build_workflow(project(), legacy, [], [], []))
    assert stage["source"]["status"] == "blocked"
    assert not action(stage["proposal"], "create_proposal")["enabled"]
    assert not action(stage["verification"], "verify_run")["enabled"]


def test_state_field_alone_does_not_make_a_legacy_task_actionable():
    legacy = run()
    legacy["task"].pop("sourceSnapshotDigest")
    stage = nodes(build_workflow(project(), legacy, [], [], []))
    assert stage["source"]["status"] == "blocked"
    assert not action(stage["proposal"], "create_proposal")["enabled"]
    assert not action(stage["verification"], "verify_run")["enabled"]


def test_mismatched_link_digest_blocks_approval_instead_of_joining_wrong_content():
    wrong = link()
    wrong["result"]["proposal_digest"] = "different-content"
    stage = nodes(build_workflow(project(), run(), [proposal()], [wrong], []))
    assert stage["proposal"]["status"] == stage["approval"]["status"] == "blocked"
    assert not action(stage["approval"], "review_proposal")["enabled"]


def test_verification_for_another_state_cannot_complete_this_approved_lineage():
    wrong = verification(state="C")
    stage = nodes(build_workflow(project("C"), run(), [proposal("accepted")], [link(), wrong], [build(state="C")]))
    assert stage["approval"]["state_revision"] == "B"
    assert stage["compile"]["status"] == stage["verification"]["status"] == "blocked"
    assert not stage["verification"]["details"]["lineage_consistent"]


def test_projection_neither_mutates_inputs_nor_exposes_nested_mutable_aliases():
    inputs = [project("B"), run(), [proposal("accepted")], [link(), verification()], [build()]]
    before = deepcopy(inputs)
    view = build_workflow(*inputs)
    assert inputs == before
    nodes(view)["tools"]["details"]["domain_acceptance"]["missing"].append("tampered output")
    assert inputs == before
    assert build_workflow(*inputs) == build_workflow(*inputs)
