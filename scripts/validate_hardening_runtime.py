"""Two tiny real subscription calls through the complete hardened local API.

Uses only existing CLIs. Claude's explicit executable is selected in this Python
process; Codex discovery, installations, PATH, main service and credentials are
unchanged. No API-key provider, project approval or Docker build is requested.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import time
from unittest.mock import patch
import uuid

from fastapi.testclient import TestClient

from lemfolio.api import create_app
from lemfolio.contracts import CONTRACT_SCHEMA_SHA256, CONTRACT_VERSION
import lemfolio.runtime as runtime_module


CLAUDE = Path(os.environ.get("LEMFOLIO_CLAUDE") or shutil.which("claude") or "claude")
SENTINELS = {"codex": "LEMFOLIO_HARDENING_CODEX_OK", "claude": "LEMFOLIO_HARDENING_CLAUDE_283_OK"}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_fingerprints(root: Path) -> dict[str, str]:
    paths = sorted((root / "src" / "lemfolio").rglob("*.py"))
    paths += [root / "contracts" / "v1" / "schema.json", Path(__file__).resolve()]
    return {path.relative_to(root).as_posix(): sha256(path) for path in paths}


def command_fingerprints(command: list[str]) -> dict[str, str]:
    return {str(Path(part).resolve()): sha256(Path(part)) for part in command if Path(part).is_file()}


def probe_version(command: list[str]) -> str:
    value = runtime_module.run_owned(command + ["--version"], env=runtime_module._clean_cli_env(),
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=15)
    if value.returncode:
        raise ValueError("Existing CLI version probe failed; no installation or retry attempted")
    return value.stdout.strip().splitlines()[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=int, default=120, help="Per-provider bound, maximum 120 seconds")
    args = parser.parse_args()
    timeout = max(10, min(args.timeout, 120))
    root = Path(__file__).resolve().parents[1]
    if root.drive.upper() != "D:":
        raise SystemExit("Live verification must run from the D: checkout")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    data_root = root / "data" / "hardening-runtime-live" / stamp
    report_path = root / "reports" / "hardening-runtime-live.json"
    before_code = source_fingerprints(root)
    report = {"schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
              "status": "running", "data_root": str(data_root), "contract_version": CONTRACT_VERSION,
              "contract_schema_sha256": CONTRACT_SCHEMA_SHA256, "per_provider_timeout_seconds": timeout,
              "scope": "Two real chat calls through create_app and TestClient, one per subscription CLI. Claude resolver is overridden only in this verification process to the existing 2.1.283 executable. Main HTTP service, installations, PATH and login files are unchanged.",
              "paid_api_provider_used": False, "model_call_limit": 2, "model_checks_are_independent_evidence": False,
              "source_code_sha256_before": before_code, "providers": {}, "runs": []}

    def save():
        report_path.parent.mkdir(exist_ok=True)
        temporary = report_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, report_path)

    original_resolver = runtime_module._resolve_cli
    commands = {}
    app = None
    save()
    try:
        # Gate both calls on the exact already-installed Claude version. This
        # operation and all model/status children use updater-disabled clean env.
        executable = CLAUDE.resolve(strict=True)
        commands["claude"] = [str(executable)]
        claude_version = probe_version(commands["claude"])
        report["providers"]["claude"] = {"command": commands["claude"], "version": claude_version,
                                             "command_file_sha256_before": command_fingerprints(commands["claude"])}
        if claude_version.split()[0] != "2.1.283":
            raise ValueError("Existing Claude version is not exactly 2.1.283; model calls refused")
        commands["codex"] = original_resolver("codex")
        if not commands["codex"]:
            raise ValueError("Existing Codex CLI discovery failed; no installation attempted")
        report["providers"]["codex"] = {"command": commands["codex"], "version": probe_version(commands["codex"]),
                                            "command_file_sha256_before": command_fingerprints(commands["codex"])}
        save()

        def resolve(name):
            return commands["claude"] if name == "claude" else original_resolver(name)

        with patch.object(runtime_module, "_resolve_cli", side_effect=resolve):
            app = create_app(data_root)
            app.state.runtime.timeout = timeout
            with TestClient(app, base_url="http://127.0.0.1") as client:
                session = client.get("/api/session")
                if session.status_code != 200 or session.json()["contract_version"] != CONTRACT_VERSION:
                    raise ValueError("Local session contract check failed")
                client.headers["x-csrf-token"] = session.json()["csrf"]
                client.headers["x-lemfolio-contract-version"] = CONTRACT_VERSION
                contract = client.get("/api/contract")
                if contract.status_code != 200 or contract.json()["schema_sha256"] != CONTRACT_SCHEMA_SHA256:
                    raise ValueError("API canonical schema identity check failed")
                auth = app.state.runtime.capabilities()["providers"]
                for provider in SENTINELS:
                    # Never retain raw auth status, init events or account fields.
                    report["providers"][provider]["authentication"] = {key: auth[provider].get(key) for key in
                        ("installed", "authenticated", "authentication", "subscription_eligible", "project_write_access")}
                created = client.post("/api/projects", json={"name": "Hardening tiny subscription verification"})
                if created.status_code != 201:
                    raise ValueError("Synthetic project creation failed")
                project = created.json()
                project_id = project["id"]
                baseline = app.state.store.capture_project(project_id)
                report["project"] = {key: baseline[key] for key in ("revision", "state_revision", "main_file", "source_hashes")}
                report["project"]["id"] = project_id
                save()

                for provider, sentinel in SENTINELS.items():
                    item = {"provider": provider, "mode": "chat", "expected_answer": sentinel, "status": "not_submitted"}
                    report["runs"].append(item)
                    started = time.monotonic()
                    try:
                        intent = ("Reply with answer exactly " + sentinel + ". Return an empty changes array and an empty checks array. "
                                  "Do not edit files or claim execution, compilation or scientific acceptance.")
                        submitted = client.post(f"/api/projects/{project_id}/runs", json={"provider": provider, "mode": "chat", "intent": intent})
                        item["submission_http_status"] = submitted.status_code
                        item["contract_header"] = submitted.headers.get("x-lemfolio-contract-version")
                        if submitted.status_code != 202:
                            item.update(status="submission_failed", error=submitted.json().get("detail"))
                            continue
                        run = submitted.json()
                        item["run_id"] = run["id"]
                        deadline = started + timeout
                        while run["status"] in {"queued", "running"} and time.monotonic() < deadline:
                            time.sleep(.2)
                            inspected = client.get(f"/api/runs/{run['id']}")
                            if inspected.status_code != 200:
                                raise ValueError("Durable run inspection failed")
                            run = inspected.json()
                        if run["status"] in {"queued", "running"}:
                            client.post(f"/api/runs/{run['id']}/interrupt")
                            run = client.get(f"/api/runs/{run['id']}").json()
                            item["deadline_interrupted"] = True
                        task, result = run["task"], run.get("result")
                        after = app.state.store.capture_project(project_id)
                        bound = app.state.evidence.load_snapshot(task["sourceSnapshotId"], task["sourceSnapshotDigest"])
                        source_unchanged = all(after[key] == baseline[key] for key in ("revision", "state_revision", "main_file", "source_hashes"))
                        binding_ok = (task["projectId"] == project_id and task["baseRevision"] == baseline["revision"]
                                      and task["baseStateRevision"] == baseline["state_revision"]
                                      and task["sourceManifest"] == baseline["source_hashes"]
                                      and task["main_file"] == baseline["main_file"]
                                      and bound["revision"] == baseline["revision"] and bound["state_revision"] == baseline["state_revision"]
                                      and bound["source_hashes"] == baseline["source_hashes"])
                        final_ok = (run["status"] == "completed" and isinstance(result, dict)
                                    and result.get("answer") == sentinel and result.get("changes") == [] and result.get("checks") == [])
                        item.update(status=run["status"], error=run.get("error"), result_digest=run.get("resultDigest"),
                                    answer=result.get("answer") if isinstance(result, dict) else None,
                                    changes=result.get("changes") if isinstance(result, dict) else None,
                                    checks=result.get("checks") if isinstance(result, dict) else None,
                                    source_unchanged=source_unchanged, task_snapshot_binding_verified=binding_ok,
                                    context_snapshot_id=task["contextSnapshotId"], source_snapshot_id=task["sourceSnapshotId"],
                                    source_snapshot_digest=task["sourceSnapshotDigest"],
                                    event_types=[event["type"] for event in app.state.runtime.events(run["id"])],
                                    passed=final_ok and binding_ok and source_unchanged and item["contract_header"] == CONTRACT_VERSION)
                    except Exception as exc:
                        # Known runtime failures are already sanitized in run.error;
                        # never serialize arbitrary process/HTTP diagnostic payloads.
                        item.update(status="validation_failed", failure_type=type(exc).__name__, passed=False)
                    finally:
                        item["elapsed_seconds"] = round(time.monotonic() - started, 3)
                        save()
                        print(json.dumps({"provider": provider, "run_id": item.get("run_id"), "status": item["status"], "passed": item.get("passed", False)}), flush=True)
    except Exception as exc:
        report.update(status="failed", failure_type=type(exc).__name__, failure_stage="preflight_or_application")
    finally:
        if app is not None:
            app.state.runtime.close()
            app.state.compiler.close()
        after_code = source_fingerprints(root)
        report["source_code_sha256_after"] = after_code
        report["source_code_unchanged"] = before_code == after_code
        cli_unchanged = True
        for provider, command in commands.items():
            if not command or provider not in report["providers"]:
                continue
            after = command_fingerprints(command)
            provider_report = report["providers"][provider]
            provider_report["command_file_sha256_after"] = after
            provider_report["command_files_unchanged"] = after == provider_report.get("command_file_sha256_before")
            cli_unchanged = cli_unchanged and provider_report["command_files_unchanged"]
        report["cli_command_files_unchanged"] = cli_unchanged
        complete = len(report["runs"]) == 2 and all(item.get("passed", False) for item in report["runs"])
        eligible = all(report["providers"].get(name, {}).get("authentication", {}).get("authenticated") for name in SENTINELS)
        report["status"] = "passed" if complete and eligible and report["source_code_unchanged"] and cli_unchanged else "failed"
        report["completed_at"] = datetime.now(timezone.utc).isoformat()
        save()
        print(json.dumps({"report": str(report_path), "status": report["status"], "model_calls_submitted": len([item for item in report["runs"] if item.get("run_id")])}), flush=True)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
