"""Real paired Codex runs against fixed synthetic papers; no production data access.

Run with the project's Python environment. Results are descriptive, never proof of
harness superiority. Independent compilation/checks follow model output approval
inside an explicitly isolated benchmark store.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import difflib
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from lemfolio.compiler import Compiler
from lemfolio.harness import WORKFLOWS, select_context, verify_project
from lemfolio.runtime import LocalRuntime, _resolve_cli
from lemfolio.store import Store


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def normalized(files):
    return {path: text.replace("\r\n", "\n") for path, text in files.items()}


def changed_lines(before, after):
    removed = added = 0
    for opcode, i, j, k, l in difflib.SequenceMatcher(a=before.splitlines(), b=after.splitlines(), autojunk=False).get_opcodes():
        if opcode != "equal":
            removed += j - i
            added += l - k
    return added + removed


def wait_run(runtime, run_id, budget):
    deadline = time.monotonic() + budget + 15
    while time.monotonic() < deadline:
        run = runtime.inspect(run_id)
        if run["status"] in {"completed", "failed", "interrupted"}:
            return run
        time.sleep(.2)
    runtime.interrupt(run_id)
    return runtime.inspect(run_id)


def wait_build(compiler, build_id):
    deadline = time.monotonic() + compiler.timeout + 30
    while time.monotonic() < deadline:
        build = compiler.inspect(build_id)
        if build["status"] not in {"queued", "running"}:
            return build
        time.sleep(.2)
    compiler.interrupt(build_id)
    return compiler.inspect(build_id)


def metrics(task, before, after, check):
    before, after = normalized(before), normalized(after)
    expected = dict(before)
    for path, replacements in task["expected_replacements"].items():
        for old, new in replacements.items():
            expected[path] = expected[path].replace(old, new)
    all_paths = set(before) | set(after)
    changed = sorted(p for p in all_paths if before.get(p) != after.get(p))
    unrelated_files = sorted(set(changed) - set(task["allowed_changed_files"]))
    errors = check.get("findings", [])
    return {"goal_exact_match_ignoring_line_endings": after == expected,
            "changed_files": changed, "changed_file_count": len(changed),
            "changed_lines": sum(changed_lines(before.get(p, ""), after.get(p, "")) for p in all_paths),
            "unrelated_changed_files": unrelated_files,
            "unrelated_diff_lines": sum(changed_lines(expected.get(p, ""), after.get(p, "")) for p in set(expected) | set(after)),
            "undefined_refs": sum(e.get("code") == "unresolved_reference" for e in errors),
            "undefined_cites": sum(e.get("code") == "unresolved_citation" for e in errors),
            "independent_static_errors": check.get("metrics", {}).get("errors"),
            "line_ending_normalization": "CRLF and LF compared as LF; original result digest retained"}


def one_run(task, arm, runtime, store, compiler, model, budget, session_root):
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for path, text in task["files"].items():
            zf.writestr(path, text)
    project = store.import_zip(f"Benchmark {task['id']} {arm}", archive.getvalue())
    before = store.text_files(project["id"])
    base = store.get_project(project["id"])["revision"]
    if arm == "baseline":
        context = {"files": dict(sorted(before.items())), "description": "Ordinary complete source files, without a paper index, workflow or tool report."}
    else:
        context = {"selection": select_context(before, task["intent"], task.get("selection")),
                   "workflow": WORKFLOWS["edit"], "verification": verify_project(before)}
    research_task = {"projectId": project["id"], "intent": task["intent"], "provider": "codex", "model": model,
                     "mode": "edit", "baseRevision": base, "contextSnapshotId": digest(context),
                     "context": context, "requiredCapabilities": ["research.generate"], "verificationPlan": []}
    started = time.monotonic()
    run = wait_run(runtime, runtime.submit(research_task)["id"], budget)
    record = {"task_id": task["id"], "arm": arm, "project_id": project["id"], "run_id": run["id"],
              "provider": "codex", "model": model, "budget_seconds": budget, "elapsed_seconds": round(time.monotonic() - started, 3),
              "base_revision": base, "input_manifest_sha256": digest(normalized(before)),
              "context_sha256": digest(context), "context_bytes": len(encoded(context)),
              "runtime_status": run["status"], "runtime_error": run["error"], "result": run["result"],
              "result_sha256": run.get("resultDigest"), "model_checks_are_validation_evidence": False,
              "pre_edit_verification": verify_project(before)}
    if run["status"] == "completed":
        try:
            proposal = store.propose(project["id"], run["result"]["changes"], base, "Isolated benchmark runner accepts model proposal for independent evaluation")
            assert store.text_files(project["id"]) == before
            store.decide(project["id"], proposal["id"], "accept", proposal["digest"])
            record.update(proposal_id=proposal["id"], proposal_digest=proposal["digest"], unchanged_before_acceptance=True)
        except Exception as exc:
            record["proposal_error"] = f"{type(exc).__name__}: {exc}"
    after = store.text_files(project["id"])
    check = verify_project(after)
    record["metrics"] = metrics(task, before, after, check)
    record["post_edit_verification"] = check
    record["output_manifest_sha256"] = digest(normalized(after))
    record["output_files"] = after
    build = wait_build(compiler, compiler.submit(project["id"])["id"])
    record["compile"] = {k: build.get(k) for k in ("id", "status", "image", "image_id", "base_revision", "source_hashes", "artifacts", "exit_code", "error")}
    record["metrics"]["compile_success"] = build["status"] == "success"
    record["metrics"]["successful_task"] = bool(record["metrics"]["goal_exact_match_ignoring_line_endings"] and build["status"] == "success" and record["metrics"]["undefined_refs"] == 0 and record["metrics"]["undefined_cites"] == 0)
    record["receipt_sha256"] = digest(record)
    (session_root / f"{task['id']}-{arm}.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="gpt-6.1-sol")
    parser.add_argument("--budget-seconds", type=int, default=120)
    parser.add_argument("--output", type=Path, default=ROOT / "reports/harness-benchmark.json")
    args = parser.parse_args()
    if not 10 <= args.budget_seconds <= 600:
        parser.error("budget must be 10–600 seconds")
    os.environ["LEMFOLIO_RUN_TIMEOUT"] = str(args.budget_seconds)
    suite_path = ROOT / "evals/tasks.json"
    suite = json.loads(suite_path.read_text(encoding="utf-8"))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    session_root = ROOT / "data/benchmarks" / stamp
    session_root.mkdir(parents=True, exist_ok=False)
    store = Store(session_root / "store")
    runtime = LocalRuntime(session_root / "runtime")
    compiler = Compiler(session_root, store, timeout=120)
    cli = _resolve_cli("codex")
    version = subprocess.run(cli + ["--version"], capture_output=True, text=True, timeout=10).stdout.strip() if cli else "missing"
    report = {"schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(), "execution": "real_official_codex_cli",
              "codex_version": version, "model": args.model, "budget_seconds_per_run": args.budget_seconds,
              "suite_sha256": hashlib.sha256(suite_path.read_bytes()).hexdigest(),
              "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "data_root": str(session_root), "arms": {"baseline": "complete raw source context", "harness": "select_context + edit workflow + independent static tool report"},
              "fairness": {"same_model": True, "same_intent": True, "same_source_files": True, "same_wall_clock_limit": True,
                           "same_cli_security_flags": True, "same_generation_schema": True, "same_execution_order": False,
                           "execution_order": "alternated baseline/harness then harness/baseline", "token_budget_measured": False,
                           "note": "Same 120-second ceiling; provider token consumption is not exposed by the current persisted transport. Context size differs by design."},
              "limitations": ["Only two small synthetic tasks, one sample per arm; no inferential statistics or superiority conclusion.",
                              "Execution order is alternated but not randomized; caching and server-side nondeterminism may affect timing.",
                              "Ordinary complete-context transport is the baseline; this does not benchmark the full interactive Codex tool harness.",
                              "Static checks and compilation are independent of model statements; neither proves scientific correctness.",
                              "Only a wall-clock ceiling is equalized; token usage/cost cannot be compared from persisted transport events."],
              "runs": []}
    try:
        for index, task in enumerate(suite["tasks"]):
            for arm in (("baseline", "harness") if index % 2 == 0 else ("harness", "baseline")):
                print(f"Running {task['id']} {arm} ({args.model})", flush=True)
                result = one_run(task, arm, runtime, store, compiler, args.model, args.budget_seconds, session_root)
                report["runs"].append(result)
                print(json.dumps({"task": task["id"], "arm": arm, "runtime": result["runtime_status"], "metrics": result["metrics"]}), flush=True)
        report["summary"] = {arm: {"runs": sum(r["arm"] == arm for r in report["runs"]),
                                  "successful_tasks": sum(r["arm"] == arm and r["metrics"]["successful_task"] for r in report["runs"]),
                                  "compile_successes": sum(r["arm"] == arm and r["metrics"]["compile_success"] for r in report["runs"]),
                                  "unrelated_diff_lines": sum(r["metrics"]["unrelated_diff_lines"] for r in report["runs"] if r["arm"] == arm)} for arm in ("baseline", "harness")}
        report["conclusion"] = "Descriptive acceptance/transport evidence only. This small suite does not establish whether the Research Harness improves research quality."
    finally:
        runtime.close()
        report["receipt_sha256"] = digest(report)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Saved {args.output}", flush=True)


if __name__ == "__main__":
    main()
