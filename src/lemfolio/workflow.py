"""Read-only projection of existing research records, never an execution engine.

Stage completion means that the named operation has evidence. It never turns
model generation, source approval or a successful PDF into scientific acceptance.
"""
from __future__ import annotations

from copy import deepcopy

from .contracts import CONTRACT_VERSION


STAGES = (
    ("source", "输入论文"), ("context", "冻结上下文"), ("tools", "独立科研检查"),
    ("model", "模型建议"), ("proposal", "修改提案"), ("approval", "人工审批"),
    ("compile", "隔离编译"), ("verification", "科研验收"),
)


def _dict(value):
    return value if isinstance(value, dict) else {}


def _id(value):
    return value if isinstance(value, str) and value else None


def _latest(values):
    def order(value):
        at = value.get("created_at", "")
        # Inputs use numeric evidence/store times and ISO runtime times. Within
        # one record family both sort naturally; IDs resolve ties deterministically.
        return (at if isinstance(at, (int, float)) else 0, str(at), str(value.get("id", "")))
    return max(values, key=order, default=None)


def _action(kind, target, enabled, reason="尚无可操作的记录"):
    return {"kind": kind, "target_id": _id(target), "enabled": bool(enabled and _id(target)),
            "reason": None if enabled and _id(target) else reason}


def build_workflow(project, run, proposals, evidence, builds) -> dict:
    """Project one scoped lineage from immutable inputs without any side effects.

    A selected run joins proposals only through proposal_created evidence. Builds
    join through that lineage's verification, or the exact accepted output state.
    With no selected run, actual manual proposal/build records remain inspectable.
    ``verified_identity`` and ``artifacts_verified`` are admissions supplied by
    the API after independently checking receipt identity and artifact bytes.
    """
    project = _dict(project)
    project_id = _id(project.get("id"))
    current = _id(project.get("state_revision"))
    if not project_id or not current:
        raise ValueError("Workflow projection requires a project and current state revision")
    main = project.get("main_file")
    has_run = run is not None
    run = _dict(run)
    run_id = _id(run.get("id"))
    task = _dict(run.get("task"))
    run_matches = bool(run_id and task.get("projectId") == project_id)
    input_state = _id(task.get("baseStateRevision")) if run_matches else None
    input_main = task.get("main_file") if run_matches else None
    run_bound = bool(run_matches and input_state and _id(task.get("baseRevision"))
                     and _id(task.get("sourceSnapshotId")) and _id(task.get("sourceSnapshotDigest"))
                     and isinstance(task.get("sourceManifest"), dict))

    def scoped(values):
        return [value for value in (values or []) if isinstance(value, dict)
                and value.get("project_id", project_id) == project_id]

    proposals, evidence, builds = scoped(proposals), scoped(evidence), scoped(builds)
    proposal_by_id = {value.get("id"): value for value in proposals if _id(value.get("id"))}
    build_by_id = {value.get("id"): value for value in builds if _id(value.get("id"))}
    proposal = link = None
    if run_matches:
        link = _latest([item for item in evidence if item.get("kind") == "proposal_created"
                        and _dict(item.get("result")).get("run_id") == run_id])
        if link is not None:
            proposal = proposal_by_id.get(_dict(link.get("result")).get("proposal_id"))
    elif not has_run:
        proposal = _latest(proposals)

    proposal_id = _id(proposal.get("id")) if proposal else None
    approved = bool(proposal and proposal.get("status") == "accepted")
    output_state = _id(proposal.get("result_state_revision")) if approved else None
    if not has_run:
        input_state = _id(proposal.get("base_state_revision")) if proposal else current
        input_main = proposal.get("main_file", main) if proposal else main
    fresh_input = bool(input_state and input_state == current and input_main == main)
    lineage_consistent = bool(not proposal or not has_run or (run_matches and
        proposal.get("base_state_revision") == input_state and proposal.get("main_file") == input_main))
    if proposal and link:
        linked = _dict(link.get("result"))
        lineage_consistent = lineage_consistent and (
            not linked.get("proposal_digest") or linked["proposal_digest"] == proposal.get("digest"))
        lineage_consistent = lineage_consistent and (
            not link.get("state_revision") or link["state_revision"] == input_state)

    verifications = [item for item in evidence if item.get("kind") == "research_verification"]
    if proposal_id:
        verifications = [item for item in verifications if _dict(item.get("result")).get("proposal_id") == proposal_id
                         and (not has_run or _dict(item.get("result")).get("run_id") == run_id)]
    elif run_matches:
        verifications = [item for item in verifications if _dict(item.get("result")).get("run_id") == run_id
                         and not _dict(item.get("result")).get("proposal_id")]
    elif not has_run:
        verifications = [item for item in verifications if not _dict(item.get("result")).get("run_id")
                         and not _dict(item.get("result")).get("proposal_id")]
    else:
        verifications = []
    verification = _latest(verifications)
    verification_value = _dict(verification.get("result")) if verification else {}
    verification_lineage = True
    if verification and (has_run or proposal):
        expected_state = output_state if proposal else input_state
        expected_main = proposal.get("main_file") if proposal else input_main
        verification_lineage = bool(expected_state and verification.get("state_revision") == expected_state
                                    and verification.get("main_file") == expected_main)
    build = None
    build_association = None
    if verification_value.get("build_id"):
        build = build_by_id.get(verification_value["build_id"])
        build_association = "verification"
    elif not has_run or output_state:
        expected = output_state or input_state or current
        expected_main = proposal.get("main_file") if proposal else input_main or main
        build = _latest([item for item in builds if item.get("state_revision") == expected
                         and item.get("main_file") == expected_main])
        build_association = "approved_output_state" if output_state else "manual_source_state"
        # A historical manual receipt remains visible when no current receipt
        # exists. Its state is displayed and mutation actions are disabled.
        if build is None and not has_run and not proposal:
            build = _latest(builds)
            build_association = "manual_history"

    nodes = {}

    def node(stage, status="not_run", state=None, entry=None, refs=(), details=None, actions=()):
        value = {"id": stage, "label": dict(STAGES)[stage], "status": status,
                 "state_revision": _id(state),
                 "historical": bool(state and (state != current or (entry is not None and entry != main))),
                 "evidence_refs": [{"kind": kind, "id": identity} for kind, identity in refs if _id(identity)],
                 "details": deepcopy(details or {}), "actions": list(actions)}
        nodes[stage] = value
        return value

    source_bound = run_bound if has_run else bool(input_state)
    node("source", "passed" if source_bound else "blocked", input_state, input_main,
         refs=[("source_snapshot", task.get("sourceSnapshotId"))] if run_matches else [],
         details={"main_file": input_main, "input_state_revision": input_state,
                  "current_state_revision": current, "current_main_file": main,
                  "source_snapshot_id": task.get("sourceSnapshotId") if run_matches else None,
                  "current_state_matches": fresh_input, "source_binding_available": source_bound},
         actions=[_action("inspect_source", project_id, True), _action("submit_run", project_id, True)])

    context = _dict(task.get("context")) if run_matches else {}
    selected = _dict(context.get("selection"))
    coverage = _dict(selected.get("coverage"))
    domain = _dict(context.get("domain"))
    context_present = bool(context and (selected or task.get("contextSnapshotId")))
    node("context", "passed" if context_present else "blocked" if has_run else "not_run", input_state, input_main,
         refs=[("context_snapshot", task.get("contextSnapshotId"))] if run_matches else [],
         details={"context_snapshot_id": task.get("contextSnapshotId") if run_matches else None,
                  "source_file_count": len(coverage),
                  "complete_file_count": sum(bool(_dict(item).get("complete")) for item in coverage.values()),
                  "truncated": selected.get("truncated"), "context_chars": selected.get("context_chars"),
                  "context_bytes": selected.get("context_bytes"), "omission_count": len(selected.get("omissions", []))},
         actions=[_action("inspect_context", run_id, run_matches and context_present, "尚无此任务的冻结上下文")])

    tool_evidence = domain.get("tool_evidence", [])
    tool_evidence = tool_evidence if isinstance(tool_evidence, list) else []
    assessment = _dict(domain.get("domain_acceptance"))
    structural = _dict(domain.get("structural_verification"))
    tool_status = "not_run"
    if domain:
        tool_status = "failed" if assessment.get("failed") or structural.get("status") == "failed" else "passed"
        if tool_status != "failed" and (assessment.get("uncertain") or any(
                _dict(_dict(item).get("result")).get("status") in {"not_run", "unsupported", "partial", "timeout"}
                for item in tool_evidence)):
            tool_status = "blocked"
    elif has_run:
        tool_status = "blocked"
    node("tools", tool_status, input_state, input_main,
         refs=[("domain_tool_evidence", context.get("domain_evidence_id"))],
         details={"tool_receipt_count": len(tool_evidence), "domain_acceptance": assessment,
                  "notes": domain.get("notes", []), "research_accepted": False},
         actions=[_action("inspect_tools", run_id, run_matches and bool(domain), "尚无此任务的独立工具记录")])

    runtime_status = run.get("status") if run_matches else None
    runtime_states = {"queued": "running", "running": "running", "completed": "passed",
                      "failed": "failed", "interrupted": "interrupted"}
    model_status = runtime_states.get(runtime_status, "blocked") if has_run else "not_run"
    result = _dict(run.get("result")) if run_matches else {}
    changes = result.get("changes", [])
    change_count = len(changes) if isinstance(changes, list) else 0
    node("model", model_status, input_state, input_main, refs=[("run", run_id)] if run_matches else [],
         details={"provider": task.get("provider") if run_matches else None,
                  "mode": task.get("mode") if run_matches else None, "runtime_status": runtime_status,
                  "error": run.get("error") if run_matches else "任务不属于当前项目" if has_run else None,
                  "proposed_change_count": change_count, "research_accepted": False,
                  "completion_means": "provider_generation_finished"},
         actions=[_action("inspect_run", run_id, run_matches, "尚无此项目的科研任务"),
                  _action("submit_run", project_id, True)])

    proposal_refs = [("proposal", proposal_id), ("proposal_created", link.get("id") if link else None)]
    proposal_fresh = bool(proposal and proposal.get("base_state_revision") == current
                          and proposal.get("main_file") == main and lineage_consistent)
    if proposal:
        proposal_status = "passed" if lineage_consistent else "blocked"
        approval_status = {"pending": "pending_review" if proposal_fresh else "blocked",
                           "accepted": "passed" if output_state and lineage_consistent else "blocked",
                           "rejected": "failed"}.get(proposal.get("status"), "blocked")
        node("proposal", proposal_status, proposal.get("base_state_revision"), proposal.get("main_file"),
             refs=proposal_refs, details={"stored_proposal": True, "proposal_id": proposal_id,
                 "status": proposal.get("status"), "digest": proposal.get("digest"), "summary": proposal.get("summary"),
                 "lineage_consistent": lineage_consistent},
             actions=[_action("review_proposal", proposal_id, proposal.get("status") == "pending" and proposal_fresh,
                              "提案已审阅或输入状态/入口已变化，禁止过时审批")])
        node("approval", approval_status, output_state if approved else proposal.get("base_state_revision"),
             proposal.get("main_file"), refs=proposal_refs,
             details={"decision": proposal.get("status"), "output_state_revision": output_state,
                      "result_revision": proposal.get("result_revision"), "research_accepted": False,
                      "human_content_approval": approved, "pending_approval_is_not_acceptance": True},
             actions=[_action("review_proposal", proposal_id, proposal.get("status") == "pending" and proposal_fresh,
                              "提案已审阅或输入状态/入口已变化，禁止过时审批")])
    else:
        draft = runtime_status == "completed" and change_count > 0
        dangling = link is not None
        node("proposal", "blocked" if dangling else "pending_review" if draft else "not_run", input_state, input_main,
             refs=proposal_refs, details={"stored_proposal": False, "proposed_change_count": change_count,
                                         "missing_linked_proposal": dangling},
             actions=[_action("create_proposal", run_id, draft and fresh_input and run_bound and not dangling,
                              "模型没有修改建议，或输入状态已过时；不会自动创建/批准提案")])
        node("approval", "blocked" if draft or dangling else "not_run", input_state, input_main,
             details={"decision": None, "research_accepted": False, "reason": "审批需要真实待审提案及其摘要"})

    build_id = _id(build.get("id")) if build else None
    expected_compile_state = verification.get("state_revision") if verification else output_state or input_state
    expected_compile_main = verification.get("main_file") if verification else proposal.get("main_file") if proposal else input_main
    if build and build_association == "manual_history":
        expected_compile_state, expected_compile_main = build.get("state_revision"), build.get("main_file")
    bound_build = bool(build and build.get("verified_identity") is True and build.get("state_revision")
                       and build.get("state_revision") == expected_compile_state
                       and build.get("main_file") == expected_compile_main)
    pdf_checked = bool(build and build.get("artifacts_verified") is True and any(
        str(_dict(item).get("name", "")).endswith(".pdf") and _dict(item).get("sha256")
        for item in build.get("artifacts", [])))
    if build:
        build_status = build.get("status")
        compile_status = {"queued": "running", "running": "running", "failed": "failed",
                          "timeout": "failed", "interrupted": "interrupted"}.get(build_status, "blocked")
        if build_status == "success":
            compile_status = "passed" if bound_build and pdf_checked else "blocked"
        if not bound_build and compile_status == "running":
            compile_status = "blocked"
    else:
        compile_status = "not_run"
    if build and (not lineage_consistent or not verification_lineage):
        compile_status = "blocked"
    compile_state = build.get("state_revision") if build else expected_compile_state
    compile_main = build.get("main_file") if build else expected_compile_main
    can_compile = bool(compile_state == current and compile_main == main and (not has_run or run_matches)
                       and (not has_run or output_state)
                       and lineage_consistent and verification_lineage
                       and not (build and build.get("status") in {"queued", "running"}))
    compile_reason = ("请使用独立验证任务来绑定输入编译，或接受提案后编译" if has_run and not output_state
                      else "源状态/入口已变化或编译正在运行；禁止过时操作")
    node("compile", compile_status, compile_state, compile_main, refs=[("build", build_id)],
         details={"build_id": build_id, "receipt_status": build.get("status") if build else None,
                  "error": build.get("error") if build else None, "association": build_association,
                  "main_file": compile_main, "engine": build.get("engine") if build else None,
                  "verified_identity": bound_build, "artifacts_verified": pdf_checked,
                  "lineage_consistent": lineage_consistent and verification_lineage,
                  "research_accepted": False},
         actions=[_action("inspect_build", build_id, bool(build), "尚无本流程的编译记录"),
                  _action("compile", project_id, can_compile, compile_reason)])

    scientific = _dict(verification.get("verification")) if verification else {}
    if verification:
        if not lineage_consistent or not verification_lineage:
            verification_status = "blocked"
        elif verification_value.get("verification_status") == "failed" or scientific.get("status") == "failed":
            verification_status = "failed"
        elif build and build.get("status") == "interrupted":
            verification_status = "interrupted"
        elif build and build.get("status") in {"queued", "running"} and bound_build:
            verification_status = "running"
        elif scientific.get("status") in {"human_review_required", "passed"}:
            verification_status = "pending_review" if bound_build and pdf_checked else "blocked"
        elif verification_value.get("verification_status") == "pending":
            verification_status = "running"
        else:
            verification_status = "blocked"
        verification_state = verification.get("state_revision")
        verification_main = verification.get("main_file")
    else:
        verification_status = "not_run"
        verification_state, verification_main = expected_compile_state, expected_compile_main
    verification_id = _id(verification.get("id")) if verification else None
    can_retry = bool(verification_id and verification_status in {"failed", "interrupted"}
                     and verification_state == current and verification_main == main
                     and verification.get("current_state_matches") is not False
                     and lineage_consistent and verification_lineage
                     and not (build and build.get("status") in {"queued", "running"}))
    node("verification", verification_status, verification_state, verification_main,
         refs=[("research_verification", verification_id)],
         details={"verification_id": verification_id, "record_status": verification_value.get("verification_status"),
                  "assessment_status": scientific.get("status"), "human_review_required": True,
                  "research_accepted": False, "model_completion_is_acceptance": False,
                  "lineage_consistent": lineage_consistent and verification_lineage,
                  "missing": scientific.get("missing", []), "failed": scientific.get("failed", []),
                  "uncertain": scientific.get("uncertain", []),
                  "error": verification_value.get("verification_error") or verification_value.get("build_error")},
         actions=[_action("inspect_verification", verification_id, bool(verification), "尚无本流程的科研验收记录"),
                  _action("verify_run", run_id, runtime_status == "completed" and fresh_input and run_bound,
                          "验证需要已完成任务及相同输入状态；应用后请查看或重试对应验收记录"),
                  _action("retry_verification", verification_id, can_retry,
                          "重试需要失败记录且当前状态/入口与原验收相同")])

    if has_run and not run_matches:
        for stage in ("context", "tools", "model", "proposal", "approval", "compile", "verification"):
            nodes[stage]["status"] = "blocked"
            nodes[stage]["details"]["reason"] = "任务不属于当前项目"
            for action in nodes[stage]["actions"]:
                if action["kind"] != "submit_run":
                    action.update(enabled=False, reason="任务不属于当前项目")
    return {"contract_version": CONTRACT_VERSION, "project_id": project_id, "run_id": run_id,
            "input_state_revision": input_state, "output_state_revision": output_state,
            "current_state_revision": current, "nodes": [nodes[stage] for stage, _ in STAGES],
            "edges": [{"source": left[0], "target": right[0]} for left, right in zip(STAGES, STAGES[1:])]}
