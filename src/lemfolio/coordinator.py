"""Finite research-tool preparation and independent verification assessment.

This layer does not give a model execution tools. It runs a bounded set of
domain checks, records their provenance and supplies evidence to LocalRuntime.
Callers must filter stored receipts to the exact current project revision.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone

from .harness import WORKFLOWS, index_project, lookup_reference, math_check, search_literature, verify_project

MAX_MATH_CHECKS = 3
MAX_REFERENCE_LOOKUPS = 3


def _receipt(tool, request, result, *, prior=None):
    body = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    receipt = {"tool": tool, "request": request, "result": result,
               "provenance": {"executor": "lemfolio.research_coordinator", "recorded_at": datetime.now(timezone.utc).isoformat(),
                              "result_sha256": hashlib.sha256(body.encode()).hexdigest(), "model_claim": False}}
    if prior:
        receipt["prior_evidence"] = {key: prior.get(key) for key in ("id", "kind", "base_revision", "created_at")}
    return receipt


def _compact_build(receipt):
    result = {key: receipt.get(key) for key in ("id", "project_id", "base_revision", "status", "engine", "image_id", "artifacts", "error", "created_at", "completed_at")}
    result["log_tail"] = str(receipt.get("log", ""))[-12000:]
    result["evidence_class"] = "isolated_compilation_receipt"
    return result


def _identifiers(files, intent, explicit):
    values = list(explicit)
    for identifier in re.findall(r"(?:https://(?:dx\.)?doi\.org/|doi:\s*)?10\.\d{4,9}/[^\s<>\"{}]+", intent, re.I):
        identifier = identifier.rstrip(".,;")
        while identifier.endswith(")") and identifier.count(")") > identifier.count("("):
            identifier = identifier[:-1]
        values.append(identifier)
    values.extend(re.findall(r"(?:arxiv:\s*|https://arxiv\.org/(?:abs|pdf)/)(?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?", intent, re.I))
    for match in re.findall(r"\b\d{4}\.\d{4,5}(?:v\d+)?\b", intent):
        if 1 <= int(match[2:4]) <= 12:
            values.append(match)
    index = index_project(files)
    cited = {item["key"] for item in index["citations"]}
    for entry in sorted(index["bibliography"], key=lambda item: item["key"] not in cited):
        fields = entry["fields"]
        if fields.get("doi"):
            values.append(fields["doi"])
        elif fields.get("eprint") and fields.get("archiveprefix", "arxiv").lower() == "arxiv":
            values.append("arxiv:" + fields["eprint"])
        elif re.match(r"https://(?:arxiv\.org/(?:abs|pdf)/|(?:dx\.)?doi\.org/)", fields.get("url", ""), re.I):
            values.append(fields["url"])
    unique, seen = [], set()
    for value in values:
        if not isinstance(value, str) or not value.strip() or len(value) > 512:
            continue
        value = value.strip()
        key = re.sub(r"^(?:https://(?:dx\.)?doi\.org/|doi:\s*|arxiv:\s*|https://arxiv\.org/abs/)", "", value, flags=re.I).lower()
        if key not in seen:
            unique.append(value)
            seen.add(key)
    return unique


def prepare_context(files: dict[str, str], intent: str, mode: str, math_checks: list[dict] | None = None,
                    reference_identifiers: list[str] | None = None, prior_evidence: list[dict] | None = None,
                    compile_receipts: list[dict] | None = None) -> dict:
    """Run bounded independent tools before model reasoning; never infer a proof.

    `prior_evidence` and `compile_receipts` must already be filtered by the caller
    to the current manifest revision. Unknown prior evidence kinds are excluded.
    """
    if mode not in WORKFLOWS:
        raise ValueError("Unknown research workflow")
    if not isinstance(intent, str) or not intent.strip() or len(intent) > 20000:
        raise ValueError("Research intent must contain 1-20000 characters")
    checks = math_checks or []
    identifiers = reference_identifiers or []
    if not isinstance(checks, list) or len(checks) > MAX_MATH_CHECKS:
        raise ValueError("At most three explicit math checks are allowed")
    if not isinstance(identifiers, list) or len(identifiers) > 20 or any(not isinstance(item, str) for item in identifiers):
        raise ValueError("Reference identifiers must be a list of at most twenty strings")
    tool_evidence, notes = [], []
    for check in checks:
        if not isinstance(check, dict) or not isinstance(check.get("lhs"), str) or not isinstance(check.get("rhs"), str):
            raise ValueError("Math checks require lhs and rhs expressions")
        request = {"lhs": check["lhs"], "rhs": check["rhs"], "assumptions": check.get("assumptions") or {}}
        tool_evidence.append(_receipt("math_check", request, math_check(**request)))
    prior_ids = []
    for evidence in (prior_evidence or [])[:10]:
        if not isinstance(evidence, dict) or not isinstance(evidence.get("result"), dict):
            continue
        result = dict(evidence["result"])
        if evidence.get("kind") == "symbolic_math":
            request = result.pop("input", {})
            tool_evidence.append(_receipt("math_check", request, result, prior=evidence))
            prior_ids.append(evidence.get("id"))
        elif evidence.get("kind") == "reference_metadata":
            tool_evidence.append(_receipt("lookup_reference", {"identifier": result.get("identifier")}, result, prior=evidence))
            prior_ids.append(evidence.get("id"))
    if mode in {"literature", "review"} or identifiers:
        candidates = _identifiers(files, intent, identifiers)
        for identifier in candidates[:MAX_REFERENCE_LOOKUPS]:
            tool_evidence.append(_receipt("lookup_reference", {"identifier": identifier}, lookup_reference(identifier)))
        if len(candidates) > MAX_REFERENCE_LOOKUPS:
            notes.append(f"Reference verification is limited to three identifiers; {len(candidates) - MAX_REFERENCE_LOOKUPS} additional identifiers were not queried.")
            tool_evidence.append(_receipt("reference_coverage", {"candidate_count": len(candidates)},
                                          {"status": "partial", "checked_count": MAX_REFERENCE_LOOKUPS, "unchecked_count": len(candidates) - MAX_REFERENCE_LOOKUPS}))
        if mode == "literature" and not candidates:
            query = intent.strip()[:400]
            tool_evidence.append(_receipt("search_literature", {"query": query, "limit": 5}, search_literature(query, 5)))
    if mode == "math" and not any(item["tool"] == "math_check" for item in tool_evidence):
        notes.append("No explicit automatically checkable lhs/rhs expressions or current-revision symbolic receipts were supplied. Mathematical analysis remains model reasoning and human review; no symbolic validation was performed.")
        tool_evidence.append(_receipt("symbolic_coverage", {}, {"status": "not_run", "reason": notes[-1]}))
    builds = [_compact_build(item) for item in (compile_receipts or [])[:2] if isinstance(item, dict)]
    structural = verify_project(files)
    latest = (compile_receipts or [None])[0]
    assessment = assess_verification(structural, latest, tool_evidence)
    return {"schema_version": 1, "workflow": mode, "tool_evidence": tool_evidence,
            "structural_verification": structural, "compile_evidence": builds,
            "verification_plan": {"mode": mode, "required": ["source_structure", "human_research_review"] + (["compile_after_edit", "diff_acceptance"] if WORKFLOWS[mode]["allows_changes"] else []),
                                  "available_tools": ["math_check", "lookup_reference", "search_literature"], "independent_tool_execution": True},
            "domain_acceptance": assessment, "notes": notes, "prior_evidence_ids": prior_ids,
            "prior_receipt_policy": "Caller must supply only receipts for the exact current project revision; model statements are never independent evidence.",
            "completion_policy": "Runtime completed means model generation finished. Research acceptance requires independent evidence and explicit human review; symbolic equality and metadata matches are not mathematical proof or claim-support verification."}


def assess_verification(structural_result: dict, build_receipt: dict | None, tool_evidence: list[dict]) -> dict:
    """Report missing/failed/uncertain checks independently of human acceptance."""
    missing, failed, uncertain, satisfied = [], [], [], []
    structural_status = (structural_result or {}).get("status")
    if structural_status == "failed":
        failed.append("source_structure_or_citations")
    elif structural_status in {"passed", "warnings"}:
        satisfied.append("static_source_checks")
        if structural_status == "warnings":
            uncertain.append("source_structure_warnings")
    else:
        missing.append("static_source_checks")
    if not build_receipt:
        missing.append("compilation")
    elif build_receipt.get("status") in {"queued", "running"}:
        missing.append("compilation_pending")
    elif build_receipt.get("status") == "success":
        if any(str(item.get("name", "")).endswith(".pdf") and item.get("sha256") for item in build_receipt.get("artifacts", [])):
            satisfied.append("isolated_compilation")
        else:
            failed.append("successful_build_missing_pdf_evidence")
    else:
        failed.append("compilation_" + str(build_receipt.get("status", "unknown")))
    for index, evidence in enumerate(tool_evidence or []):
        result = evidence.get("result", {})
        status, tool = result.get("status"), evidence.get("tool", "unknown")
        label = f"{tool}:{index}"
        if tool == "math_check":
            if status == "symbolically_equivalent":
                satisfied.append(label + ":limited_symbolic_equivalence")
            elif status == "not_equivalent":
                failed.append(label + ":not_equivalent")
            else:
                uncertain.append(label + ":" + str(status))
        elif tool == "lookup_reference":
            if status == "verified_metadata":
                satisfied.append(label + ":identifier_metadata_match")
            elif status in {"not_found", "identifier_mismatch", "invalid_identifier"}:
                failed.append(label + ":" + status)
            else:
                uncertain.append(label + ":" + str(status))
        elif tool == "search_literature":
            if status == "ok" and result.get("results"):
                satisfied.append(label + ":metadata_search")
            else:
                uncertain.append(label + ":" + str(status))
        elif tool in {"reference_coverage", "symbolic_coverage"}:
            uncertain.append(label + ":" + str(status))
    human = ["Research claims, assumptions and proof obligations require human review"]
    if any(item.get("tool") in {"lookup_reference", "search_literature"} for item in tool_evidence or []):
        human.append("Metadata existence does not verify cited source content or claim support")
    return {"status": "failed" if failed else "partial" if missing or uncertain else "human_review_required",
            "research_accepted": False, "human_review_required": True, "missing": missing, "failed": failed,
            "uncertain": uncertain, "satisfied": satisfied, "human_review": human,
            "mathematical_proof": False, "runtime_completion_is_research_acceptance": False}
