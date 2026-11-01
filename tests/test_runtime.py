import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import time

import pytest

from lemfolio.runtime import KeelRuntimeAdapter, LocalRuntime, RuntimeError, _validate_result, validate_task


def task(**overrides):
    return {"projectId": "p", "intent": "Check the selected equation", "baseRevision": "r1",
            "contextSnapshotId": "c1", "context": {"files": {"main.tex": "hello"}},
            "mode": "edit", "provider": "codex", **overrides}


def wait(runtime, run_id):
    until = time.monotonic() + 8
    while time.monotonic() < until:
        value = runtime.inspect(run_id)
        if value["status"] in {"completed", "failed", "interrupted"}:
            return value
        time.sleep(.02)
    pytest.fail("test provider did not finish")


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    value = LocalRuntime(tmp_path / "runtime")
    monkeypatch.setattr(value, "capabilities", lambda: {"capabilities": ["research.generate"]})
    yield value
    value.close()


def fake_command(monkeypatch, runtime, script):
    monkeypatch.setattr(runtime, "_command", lambda *args: [sys.executable, "-c", script])


def test_durable_run_result_events_and_revision_binding(runtime, monkeypatch):
    result = {"answer": "Proposed clarification", "changes": [{"path": "main.tex", "content": "updated"}], "checks": ["not compiled"]}
    event = {"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(result)}}
    fake_command(monkeypatch, runtime, "import sys,json; sys.stdin.read(); print(" + repr(json.dumps(event)) + ")")
    submitted = task()
    run = runtime.submit(submitted)
    submitted["context"]["files"]["main.tex"] = "mutated after submission"
    run = wait(runtime, run["id"])
    assert run["status"] == "completed"
    assert run["result"] == result
    assert run["task"]["context"]["files"]["main.tex"] == "hello"
    assert run["task"]["baseRevision"] == "r1"
    events = runtime.events(run["id"])
    assert [e["type"] for e in events] == ["queued", "running", "message", "result", "completed"]
    assert runtime.events(run["id"], after=events[-2]["seq"]) == events[-1:]
    assert runtime.approve(run["id"], {"decision": "accept", "resultDigest": run["resultDigest"]})["project_files_applied"] is False
    with pytest.raises(RuntimeError, match="exact resultDigest"):
        runtime.approve(run["id"], {"decision": "accept", "resultDigest": "stale"})
    assert runtime.recover(run["id"])["status"] == "unsupported"
    assert not list(runtime.root.rglob("main.tex"))


def test_cancel_terminates_process_and_is_terminal(runtime, monkeypatch):
    fake_command(monkeypatch, runtime, "import sys,time; sys.stdin.read(); time.sleep(60)")
    run = runtime.submit(task())
    for _ in range(100):
        if runtime._processes:
            break
        time.sleep(.01)
    processes = list(runtime._processes.values())
    assert processes
    assert runtime.interrupt(run["id"])["status"] == "interrupted"
    time.sleep(.2)
    assert processes[0].poll() is not None
    assert runtime.inspect(run["id"])["status"] == "interrupted"
    assert runtime.inspect(run["id"])["result"] is None


def test_restart_does_not_claim_resume(tmp_path):
    root = tmp_path / "persist"
    first = LocalRuntime(root)
    with sqlite3.connect(first.db_path) as db:
        db.execute("INSERT INTO runs VALUES(?,?,?,?,?,?,?)", ("orphan", json.dumps(task()), "running", None, None, "a", "a"))
    first.close()
    second = LocalRuntime(root)
    try:
        run = second.inspect("orphan")
        assert run["status"] == "interrupted"
        assert second.recover("orphan")["execution_resume_supported"] is False
        assert second.events("orphan")[0]["data"]["reason"] == "runtime_restart"
    finally:
        second.close()


@pytest.mark.parametrize("path", ["../outside.tex", "/etc/passwd", "D:/file.tex", "a\\b.tex", "a//b.tex"])
def test_unsafe_provider_paths_are_rejected(path):
    with pytest.raises(RuntimeError):
        _validate_result({"answer": "x", "changes": [{"path": path, "content": "x"}], "checks": []})


def test_malformed_response_fails_without_raw_diagnostics(runtime, monkeypatch):
    fake_command(monkeypatch, runtime, "import sys; sys.stdin.read(); print('not JSON'); print('secret-do-not-log',file=sys.stderr)")
    run = wait(runtime, runtime.submit(task())["id"])
    assert run["status"] == "failed"
    assert "schema-valid" in run["error"]
    assert "secret-do-not-log" not in json.dumps(runtime.events(run["id"]))


@pytest.mark.parametrize('mode', ['chat', 'review'])
def test_chat_cannot_propose_file_changes(runtime, monkeypatch, mode):
    result = {"answer": "x", "changes": [{"path": "main.tex", "content": "x"}], "checks": []}
    event = {"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(result)}}
    fake_command(monkeypatch, runtime, "import sys; sys.stdin.read(); print(" + repr(json.dumps(event)) + ")")
    run = wait(runtime, runtime.submit(task(mode=mode))["id"])
    assert run["status"] == "failed"
    assert "cannot propose file changes" in run["error"]


def test_cli_commands_remove_execution_and_external_customizations(runtime, monkeypatch, tmp_path):
    import lemfolio.runtime as module
    monkeypatch.setattr(module, "_resolve_cli", lambda name: [name])
    codex = runtime._command("codex", task(), tmp_path)
    assert "--ignore-user-config" in codex
    assert codex[codex.index("--sandbox") + 1] == "read-only"
    for flag in ("shell_tool", "unified_exec", "apps", "plugins", "hooks", "computer_use", "browser_use", "multi_agent"):
        assert any(codex[i:i+2] == ["--disable", flag] for i in range(len(codex)))
    assert "mcp_servers={}" in codex
    claude = runtime._command("claude", task(), tmp_path)
    assert claude[claude.index("--tools") + 1] == ""
    assert "--safe-mode" in claude
    assert "--strict-mcp-config" in claude
    assert not any("bypass" in part for part in codex + claude)


def test_provider_schema_is_derived_from_shared_agent_contract():
    from lemfolio.contracts import protocol_schema
    from lemfolio.runtime import SCHEMA
    definitions = protocol_schema()["$defs"]
    assert {key: value for key, value in SCHEMA.items() if key != "$defs"} == definitions["AgentResult"]
    assert SCHEMA["$defs"]["AgentChange"] == definitions["AgentChange"]
    assert SCHEMA["$defs"]["TextEdit"] == definitions["TextEdit"]
    assert SCHEMA["$defs"]["AgentChange"]["required"] == ["path", "content", "edits"]


def test_claude_structured_output_parser():
    result = {"answer": "ok", "changes": [], "checks": []}
    assert LocalRuntime._extract("claude", [{"type": "result", "structured_output": result}]) == result
    with pytest.raises(RuntimeError):
        LocalRuntime._extract("claude", [{"type": "result", "is_error": True}])


def test_unknown_runtime_capabilities_are_not_silently_accepted(runtime):
    with pytest.raises(RuntimeError, match="unsupported"):
        runtime.submit(task(requiredCapabilities=["filesystem.write"]))
    assert KeelRuntimeAdapter().capabilities()["available"] is False
    assert KeelRuntimeAdapter().recover()["status"] == "unsupported"
    with pytest.raises(RuntimeError) as error:
        KeelRuntimeAdapter().submit(task())
    assert error.value.status_code == 501


def test_api_fallback_is_explicit_opt_in(runtime, monkeypatch):
    monkeypatch.delenv("LEMFOLIO_API_KEY", raising=False)
    monkeypatch.delenv("LEMFOLIO_API_MODEL", raising=False)
    run = wait(runtime, runtime.submit(task(provider="api"))["id"])
    assert run["status"] == "failed"
    assert "requires LEMFOLIO_API_KEY" in run["error"]


def test_completion_rolls_back_result_and_events_on_database_failure(runtime):
    with runtime._db() as db:
        db.execute("INSERT INTO runs VALUES(?,?,?,?,?,?,?)", ("fault", json.dumps(task()), "running", None, None, "a", "a"))
        db.execute("""CREATE TRIGGER fail_completion BEFORE INSERT ON events
                      WHEN NEW.type='completed' BEGIN SELECT RAISE(ABORT,'injected completion fault'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="injected completion fault"):
        runtime._complete("fault", {"answer": "complete", "changes": [], "checks": []})
    run = runtime.inspect("fault")
    assert run["status"] == "running" and run["result"] is None
    assert runtime.events("fault") == []


def test_closing_during_admission_cannot_start_a_late_worker(runtime, monkeypatch):
    def close_during_probe():
        runtime.close()
        return {"capabilities": ["research.generate"]}
    monkeypatch.setattr(runtime, "capabilities", close_during_probe)
    with pytest.raises(RuntimeError, match="closed"):
        runtime.submit(task())
    assert runtime.list_runs() == []
    assert not runtime._threads


def test_abrupt_parent_exit_during_completion_cannot_leave_result_event_without_result(tmp_path):
    """Crash the real SQLite transaction; do not merely fake a missing status."""
    import subprocess
    import os
    root = tmp_path / "crashed-runtime"
    script = r'''
import json,os,sys
from contextlib import contextmanager
from pathlib import Path
from lemfolio.runtime import LocalRuntime
class CrashRuntime(LocalRuntime):
    @contextmanager
    def _db(self):
        with super()._db() as db:
            db.create_function("crash_process", 0, lambda: os._exit(23))
            yield db
runtime=CrashRuntime(Path(sys.argv[1]))
with runtime._db() as db:
    db.execute("INSERT INTO runs VALUES(?,?,?,?,?,?,?)", ("fault",json.dumps({"projectId":"p"}),"running",None,None,"a","a"))
    db.execute("CREATE TRIGGER crash_completion BEFORE INSERT ON events WHEN NEW.type='completed' BEGIN SELECT crash_process(); END")
runtime._complete("fault", {"answer":"generated","changes":[],"checks":[]})
'''
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
    crashed = subprocess.run([sys.executable, "-B", "-c", script, str(root)], env=env,
                             capture_output=True, timeout=15)
    assert crashed.returncode == 23
    db = sqlite3.connect(root / "runs.sqlite3")
    try:
        assert db.execute("SELECT status,result FROM runs WHERE id='fault'").fetchone() == ("running", None)
        assert db.execute("SELECT COUNT(*) FROM events WHERE run_id='fault'").fetchone()[0] == 0
        db.execute("DROP TRIGGER crash_completion")
        db.commit()
    finally:
        db.close()
    reopened = LocalRuntime(root)
    try:
        run = reopened.inspect("fault")
        assert run["status"] == "interrupted" and run["result"] is None
        assert [event["type"] for event in reopened.events("fault")] == ["interrupted"]
    finally:
        reopened.close()


def test_cli_status_and_run_share_subscription_only_environment(runtime, monkeypatch, tmp_path):
    import lemfolio.runtime as module
    for name in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
                 "ANTHROPIC_BASE_URL", "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_USE_BEDROCK"):
        monkeypatch.setenv(name, "non-secret-review-sentinel")
    monkeypatch.delenv("DISABLE_AUTOUPDATER", raising=False)
    monkeypatch.delenv("DISABLE_UPDATES", raising=False)
    child_env = module._clean_cli_env()
    assert not any(name in child_env for name in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "ANTHROPIC_API_KEY",
                                                "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
                                                "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_USE_BEDROCK"))
    assert child_env["DISABLE_AUTOUPDATER"] == child_env["DISABLE_UPDATES"] == "1"
    script = "import sys,os,json;sys.stdin.read();print(json.dumps({'type':'result','structured_output':{'answer':json.dumps({'clean':not any(k.startswith(('ANTHROPIC_','OPENAI_','CLAUDE_CODE_USE_')) for k in os.environ),'updates_disabled':os.getenv('DISABLE_AUTOUPDATER')=='1' and os.getenv('DISABLE_UPDATES')=='1'}),'changes':[],'checks':[]}}))"
    fake_command(monkeypatch, runtime, script)
    completed = wait(runtime, runtime.submit(task(provider="claude"))["id"])
    assert completed["status"] == "completed"
    assert json.loads(completed["result"]["answer"]) == {"clean": True, "updates_disabled": True}


@pytest.mark.parametrize("provider,code,status,expected", [
    ("claude", 0, {"loggedIn": True, "authMethod": "claude.ai", "apiProvider": "firstParty"}, True),
    ("claude", 1, {"loggedIn": True, "authMethod": "claude.ai", "apiProvider": "firstParty"}, False),
    ("claude", 0, {"loggedIn": True, "authMethod": "apiKey", "apiProvider": "firstParty"}, False),
    ("claude", 0, {"loggedIn": True, "authMethod": "claude.ai", "apiProvider": "bedrock"}, False),
    ("codex", 0, "Logged in using ChatGPT", True),
    ("codex", 0, "Logged in using API key", False),
    ("codex", 0, "API key authentication; ChatGPT login is also supported", False),
])
def test_authentication_probe_rejects_non_subscription_routes(tmp_path, monkeypatch, provider, code, status, expected):
    import subprocess
    import lemfolio.runtime as module
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "non-secret-review-sentinel")
    monkeypatch.setattr(module, "_resolve_cli", lambda name: [name] if name == provider else None)
    calls = []
    def run(command, **kwargs):
        calls.append(kwargs["env"])
        return subprocess.CompletedProcess(command, code, json.dumps(status) if isinstance(status, dict) else status, "")
    monkeypatch.setattr(module, "run_owned", run)
    value = LocalRuntime(tmp_path / "auth")
    try:
        report = value.capabilities()["providers"][provider]
        assert report["authenticated"] is expected
        assert calls and "ANTHROPIC_AUTH_TOKEN" not in calls[0]
        assert calls[0]["DISABLE_AUTOUPDATER"] == calls[0]["DISABLE_UPDATES"] == "1"
    finally:
        value.close()


def test_model_catalog_reports_only_what_each_cli_says(runtime, monkeypatch):
    codex = json.dumps({"models": [
        {"slug": "later", "display_name": "Later", "visibility": "list", "priority": 5, "description": "b"},
        {"slug": "hidden", "display_name": "Hidden", "visibility": "hide", "priority": 1},
        {"slug": "first", "visibility": "list", "priority": 2},
    ]})
    claude = "  --model <model>   Model for the session. Provide\n   an alias for the latest model (e.g.\n   'fable', 'opus', or 'sonnet') or a\n   model's full name.\n  -n, --name <name>  'other'"
    monkeypatch.setattr("lemfolio.runtime._resolve_cli", lambda name: [name])
    monkeypatch.setattr(runtime, "_cli_output", lambda command, arguments, env=None: codex if command == ["codex"] else claude)
    monkeypatch.delenv("LEMFOLIO_API_MODEL", raising=False)
    catalog = {item["provider"]: item for item in runtime.model_catalog(refresh=True)["providers"]}
    assert [item["id"] for item in catalog["codex"]["models"]] == ["first", "later"]
    assert catalog["codex"]["status"] == "listed"
    assert catalog["codex"]["models"][0]["name"] == "first"
    assert [item["id"] for item in catalog["claude"]["models"]] == ["fable", "opus", "sonnet"]
    assert catalog["claude"]["status"] == "aliases"
    assert catalog["api"] == {"provider": "api", "status": "unavailable", "source": "LEMFOLIO_API_MODEL", "models": []}


def test_model_catalog_reports_the_effort_levels_each_cli_says(runtime, monkeypatch):
    codex = json.dumps({"models": [
        {"slug": "a", "visibility": "list", "default_reasoning_level": "low",
         "supported_reasoning_levels": [{"effort": "low", "description": "x"}, {"effort": "ultra", "description": "y"}]},
        {"slug": "bare", "visibility": "list"},
    ]})
    claude = ("  --effort <level>    Effort level for the current session\n      (low, medium,\n      max)\n"
              "  --model <model>   Provide an alias (e.g. 'opus') or a model's full name.\n")
    monkeypatch.setattr("lemfolio.runtime._resolve_cli", lambda name: [name])
    monkeypatch.setattr(runtime, "_cli_output", lambda command, arguments, env=None: codex if command == ["codex"] else claude)
    catalog = {item["provider"]: item for item in runtime.model_catalog(refresh=True)["providers"]}
    first, bare = catalog["codex"]["models"]
    assert (first["efforts"], first["default_effort"]) == (["low", "ultra"], "low")
    assert (bare["efforts"], bare["default_effort"]) == ([], None)
    opus = catalog["claude"]["models"][0]
    assert (opus["efforts"], opus["default_effort"]) == (["low", "medium", "max"], None)


@pytest.mark.parametrize("provider,effort,expected", [
    ("claude", "high", ["--effort", "high"]),
    ("codex", "ultra", ["-c", 'model_reasoning_effort="ultra"']),
])
def test_effort_reaches_each_cli_its_own_way(runtime, monkeypatch, tmp_path, provider, effort, expected):
    monkeypatch.setattr("lemfolio.runtime._resolve_cli", lambda name: [name])
    command = runtime._command(provider, {"effort": effort}, tmp_path)
    assert expected in [command[index:index + 2] for index in range(len(command))]
    plain = runtime._command(provider, {}, tmp_path)
    assert "--effort" not in plain and not any("model_reasoning_effort" in part for part in plain)


@pytest.mark.parametrize("effort", ["HIGH", "high --tools", 'x"', "", 3, "a" * 17])
def test_effort_is_refused_unless_it_is_a_plain_level(effort):
    with pytest.raises(RuntimeError, match="effort"):
        validate_task(task(effort=effort))


def test_the_api_route_refuses_an_effort_it_cannot_apply():
    with pytest.raises(RuntimeError, match="effort"):
        validate_task(task(provider="api", effort="high"))


@pytest.mark.parametrize("output", ["not json", "{}", '{"models": [{"visibility": "list"}]}', '{"models": []}'])
def test_model_catalog_lists_nothing_when_a_cli_cannot_say(runtime, monkeypatch, output):
    monkeypatch.setattr("lemfolio.runtime._resolve_cli", lambda name: [name])
    monkeypatch.setattr(runtime, "_cli_output", lambda command, arguments: output)
    codex = runtime.model_catalog(refresh=True)["providers"][0]
    assert codex == {"provider": "codex", "status": "unavailable", "source": "codex debug models", "models": []}


RESULT = {"answer": "From a harness", "changes": [], "checks": []}


def test_opencode_command_confines_tools_and_never_reaches_the_project_config(runtime, monkeypatch, tmp_path):
    import lemfolio.runtime as module
    monkeypatch.setattr(module, "_resolve_cli", lambda name: ["opencode.exe"])
    assert runtime._command("opencode", task(provider="opencode", model="deepseek/deepseek-flash"), tmp_path) == [
        "opencode.exe", "run", "--standalone", "--format", "json", "--model", "deepseek/deepseek-flash"]
    env = module._opencode_env()
    assert json.loads(env["OPENCODE_CONFIG_CONTENT"])["permission"] == {"*": "deny"}
    assert env["OPENCODE_DISABLE_PROJECT_CONFIG"] == "1"


def test_opencode_is_listed_but_never_counts_as_a_subscription(tmp_path, monkeypatch):
    import lemfolio.runtime as module
    monkeypatch.setattr(module, "_resolve_cli", lambda name: ["fake"] if name == "opencode" else None)
    value = LocalRuntime(tmp_path / "status")
    item = value.capabilities()["providers"]["opencode"]
    value.close()
    assert item["authenticated"] is True and item["subscription_eligible"] is False and item["kind"] == "harness"


def test_opencode_result_tolerates_a_fence_and_surfaces_its_error():
    from lemfolio.runtime import LocalRuntime
    text = {"type": "text", "part": {"text": "```json\n" + json.dumps(RESULT) + "\n```"}}
    assert LocalRuntime._extract("opencode", [{"type": "step_start"}, text]) == RESULT
    failed = {"type": "error", "error": {"name": "APIError", "data": {"message": "quota exceeded"}}}
    with pytest.raises(ValueError, match="quota exceeded"):
        LocalRuntime._extract("opencode", [failed])
    with pytest.raises(ValueError, match="schema-valid"):
        LocalRuntime._extract("opencode", [{"type": "text", "part": {"text": "plain words"}}])


def test_parse_opencode_models_keeps_only_provider_model_lines():
    from lemfolio.runtime import parse_opencode_models
    models = parse_opencode_models("Loading\nopencode/free-a\ndeepseek/deepseek-flash\nopencode/free-a\n")
    assert [model["id"] for model in models] == ["opencode/free-a", "deepseek/deepseek-flash"]
    assert models[0]["efforts"] == []


def test_npm_launcher_is_read_not_run(tmp_path):
    from lemfolio.runtime import _npm_launcher_target
    program = tmp_path / "node_modules/@x/cli/bin/x.exe"
    program.parent.mkdir(parents=True)
    program.write_bytes(b"")
    launcher = tmp_path / "x.cmd"
    launcher.write_text(r'@ECHO off' + "\n" + r'"%dp0%\node_modules\@x\cli\bin\x.exe"   %*' + "\n", encoding="utf-8")
    assert _npm_launcher_target(launcher) == [str(program)]
    launcher.write_text("@ECHO off\r\ncalc.exe %*\r\n", encoding="utf-8")
    assert _npm_launcher_target(launcher) is None


def test_harness_command_is_split_without_a_shell():
    from lemfolio.runtime import parse_harness_command
    assert parse_harness_command('"D:/Program Files/x/x.exe" run --json') == ["D:/Program Files/x/x.exe", "run", "--json"]
    assert parse_harness_command("x a&&b") == ["x", "a&&b"]
    for bad in ("", "   ", 'x "open', "x\ny"):
        with pytest.raises(ValueError):
            parse_harness_command(bad)


@pytest.fixture
def unfaked(tmp_path, monkeypatch):
    """A runtime with real status reports, where no vendor CLI is installed."""
    import lemfolio.runtime as module
    resolve = module._resolve_cli
    monkeypatch.setattr(module, "_resolve_cli", lambda name: None if name in module.BUILT_IN_HARNESSES else resolve(name))
    value = LocalRuntime(tmp_path / "unfaked")
    yield value
    value.close()


def custom_script(tmp_path, body):
    script = tmp_path / "harness.py"
    script.write_text("import sys, json\nprompt = sys.stdin.read()\n" + body, encoding="utf-8")
    return f'"{sys.executable}" "{script}"'


@pytest.mark.parametrize("body", [
    f"print(json.dumps({RESULT!r}, indent=2))",
    f"print(json.dumps({RESULT!r}))",
    "print('Here is the result:'); print('```json'); print(json.dumps(" + repr(RESULT) + ")); print('```')",
    "print(json.dumps({'type': 'init'})); print(json.dumps({'type': 'result', 'result': json.dumps(" + repr(RESULT) + ")}))",
    "print(json.dumps({'event': {'message': {'content': [{'text': json.dumps(" + repr(RESULT) + ")}]}}}))",
])
def test_any_harness_output_shape_that_contains_the_result_is_read(unfaked, tmp_path, body):
    entry = unfaked.add_harness("Any", custom_script(tmp_path, body))
    completed = wait(unfaked, unfaked.submit(task(provider=entry["id"]))["id"])
    assert completed["status"] == "completed" and completed["result"]["answer"] == "From a harness"


def test_added_harness_runs_and_can_be_removed(unfaked, tmp_path):
    runtime = unfaked
    entry = runtime.add_harness(" My CLI ", custom_script(tmp_path, f"print(json.dumps({RESULT!r}, indent=2))"))
    assert entry["name"] == "My CLI" and entry["id"].startswith("custom-")
    status = runtime.capabilities()["providers"][entry["id"]]
    # A command Lemfolio cannot see into must not be reported as having no write access.
    assert status["kind"] == "custom" and status["project_write_access"] is None
    assert any(item["provider"] == entry["id"] and item["models"] == [] for item in runtime.model_catalog()["providers"])
    completed = wait(runtime, runtime.submit(task(provider=entry["id"]))["id"])
    assert completed["status"] == "completed" and completed["result"]["answer"] == "From a harness"
    runtime.remove_harness(entry["id"])
    assert entry["id"] not in runtime.capabilities()["providers"]
    with pytest.raises(ValueError, match="Unsupported"):
        runtime.submit(task(provider=entry["id"]))


def test_added_harness_that_prints_no_result_fails_plainly(unfaked, tmp_path):
    runtime = unfaked
    entry = runtime.add_harness("Silent", custom_script(tmp_path, "print('hello')"))
    failed = wait(runtime, runtime.submit(task(provider=entry["id"]))["id"])
    assert failed["status"] == "failed" and "JSON object" in failed["error"]


def listing_script(tmp_path, output):
    script = tmp_path / "models.py"
    script.write_text(f"print({output!r})", encoding="utf-8")
    return f'"{sys.executable}" "{script}"'


def test_harness_models_come_from_lines_or_json():
    from lemfolio.runtime import parse_harness_models
    assert [item["id"] for item in parse_harness_models("a/b\n\nc-1\n")] == ["a/b", "c-1"]
    listed = parse_harness_models(json.dumps({"models": [{"id": "m", "name": "M", "efforts": ["low", "HIGH", "max"], "default_effort": "low"}, "n"]}))
    assert listed[0] == {"id": "m", "name": "M", "description": None, "efforts": ["low", "max"], "default_effort": "low"}
    assert listed[1]["efforts"] == [] and listed[1]["name"] == "n"
    for bad in ("Usage: agent [options]", '{"models": 3}', '[{"name": "no id"}]'):
        with pytest.raises(ValueError):
            parse_harness_models(bad)


def test_harness_command_fills_what_was_chosen_and_drops_the_rest():
    from lemfolio.runtime import fill_harness_command
    parts = ["--json", "--model", "{model}", "--effort={effort}", "x"]
    assert fill_harness_command(parts, "m", "low") == ["--json", "--model", "m", "--effort=low", "x"]
    assert fill_harness_command(parts, None, None) == ["--json", "x"]


def test_added_harness_reports_models_and_effort_and_receives_the_choice(unfaked, tmp_path):
    runtime = unfaked
    listing = listing_script(tmp_path, json.dumps({"models": [{"id": "m1", "efforts": ["low", "high"], "default_effort": "high"}]}))
    body = f"print(json.dumps(dict({RESULT!r}, answer=' '.join(sys.argv[1:]))))"
    entry = runtime.add_harness("Listed", custom_script(tmp_path, body) + " --model {model} --effort {effort}", listing)
    reported = next(item for item in runtime.model_catalog(refresh=True)["providers"] if item["provider"] == entry["id"])
    assert reported["status"] == "listed" and reported["models"][0]["efforts"] == ["low", "high"] and reported["models"][0]["default_effort"] == "high"
    completed = wait(runtime, runtime.submit(task(provider=entry["id"], model="m1", effort="low"))["id"])
    assert completed["status"] == "completed" and completed["result"]["answer"] == "--model m1 --effort low"
    assert wait(runtime, runtime.submit(task(provider=entry["id"]))["id"])["result"]["answer"] == ""


def test_harness_levels_are_hidden_when_its_command_cannot_take_one(unfaked, tmp_path):
    listing = listing_script(tmp_path, json.dumps([{"id": "m1", "efforts": ["low"]}]))
    entry = unfaked.add_harness("No levels", custom_script(tmp_path, "pass") + " --model {model}", listing)
    reported = next(item for item in unfaked.model_catalog(refresh=True)["providers"] if item["provider"] == entry["id"])
    assert reported["models"][0]["efforts"] == []


def test_harness_listing_must_work_and_have_somewhere_to_go(unfaked, tmp_path):
    good = custom_script(tmp_path, "pass")
    with pytest.raises(RuntimeError, match="model"):
        unfaked.add_harness("No slot", good, listing_script(tmp_path, "m1"))
    with pytest.raises(RuntimeError, match="did not print any models"):
        unfaked.add_harness("Prose", good + " --model {model}", listing_script(tmp_path, "this is not a list"))
    assert unfaked.harnesses() == []


def test_added_harness_keeps_its_environment_but_not_lemfolios_key(monkeypatch):
    import lemfolio.runtime as module
    monkeypatch.setenv("LEMFOLIO_API_KEY", "x")
    monkeypatch.setenv("OPENAI_API_KEY", "own")
    env = module._harness_env()
    assert "LEMFOLIO_API_KEY" not in env and env["OPENAI_API_KEY"] == "own"


def test_add_harness_refuses_what_it_cannot_run_or_name(runtime, tmp_path):
    good = custom_script(tmp_path, "pass")
    for name, command in (("", good), ("x" * 41, good), ("Missing", "no-such-program-here")):
        with pytest.raises(ValueError):
            runtime.add_harness(name, command)
    for taken in ("OpenCode", "Codex"):
        with pytest.raises(ValueError):
            runtime.add_harness(taken, good)
    runtime.add_harness("Mine", good)
    with pytest.raises(ValueError, match="already"):
        runtime.add_harness("mine", good)


def test_harness_file_that_was_hand_edited_badly_is_ignored(runtime):
    path = runtime.root / "harnesses.json"
    path.write_text(json.dumps([{"id": "codex", "name": "x", "command": ["x"]}, {"id": "BAD ID", "name": "x", "command": ["x"]},
                                {"id": "ok", "name": "Ok", "command": ["x"]}, "junk"]), encoding="utf-8")
    assert [item["id"] for item in runtime.harnesses()] == ["ok"]
    path.write_text("not json", encoding="utf-8")
    assert runtime.harnesses() == []


def test_effort_is_refused_for_backends_without_it():
    from lemfolio.runtime import RuntimeError as ProviderError
    with pytest.raises(ProviderError, match="no effort"):
        validate_task(task(provider="opencode", effort="high"))
