"""Live-test an explicitly selected existing Claude CLI without changing installs."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
from unittest.mock import patch
import uuid

from lemfolio.compiler import Compiler
from lemfolio.coordinator import prepare_context, assess_verification
from lemfolio.harness import WORKFLOWS, select_context, verify_project
import lemfolio.runtime as runtime_module
from lemfolio.runtime import LocalRuntime
from lemfolio.store import Store


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--executable', type=Path, required=True)
    parser.add_argument('--expected-version', default='2.1.283')
    parser.add_argument('--timeout', type=int, default=150)
    args = parser.parse_args()
    executable = args.executable.resolve(strict=True)
    if executable.suffix.lower() != '.exe':
        raise SystemExit('Select an existing native Claude executable')
    # These settings affect only this verification process and its children.
    os.environ['DISABLE_AUTOUPDATER'] = '1'
    os.environ['DISABLE_UPDATES'] = '1'
    clean_env = dict(os.environ)
    for name in list(clean_env):
        if name in {'OPENAI_API_KEY', 'CODEX_API_KEY', 'ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN'} or name.startswith(('CLAUDECODE', 'LEMFOLIO_API_KEY')):
            clean_env.pop(name, None)
    options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
    version = subprocess.run([str(executable), '--version'], env=clean_env, capture_output=True, text=True, timeout=15, check=True, **options).stdout.strip()
    if version.split()[0] != args.expected_version:
        raise SystemExit('Selected executable version does not match requested version')
    before_hash = hashlib.sha256(executable.read_bytes()).hexdigest()
    auth_call = subprocess.run([str(executable), 'auth', 'status'], env=clean_env, capture_output=True, text=True, timeout=15, **options)
    auth = json.loads(auth_call.stdout)
    safe_auth = {key: auth.get(key) for key in ('loggedIn', 'authMethod', 'apiProvider', 'subscriptionType')}
    if auth_call.returncode or not auth.get('loggedIn') or auth.get('authMethod') != 'claude.ai':
        raise SystemExit('The selected CLI does not report an active Claude subscription login')

    root = Path(__file__).resolve().parents[1]
    validation_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8]
    test_root = root / 'data' / 'claude-validation' / validation_id
    test_root.mkdir(parents=True)
    report_path = root / 'reports' / 'claude-2.1.283-validation.json'
    report = {'schema_version': 1, 'created_at': datetime.now(timezone.utc).isoformat(), 'executable': str(executable), 'version': version, 'executable_sha256': before_hash, 'authentication': safe_auth, 'data_root': str(test_root), 'scope': 'Real official Claude CLI through the unmodified LocalRuntime adapter, with executable resolution selected only inside this validation process. Main server, PATH and installations are unchanged.', 'runs': [], 'status': 'running', 'model_checks_are_independent_evidence': False}

    def save():
        report_path.parent.mkdir(exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')

    original_resolver = runtime_module._resolve_cli
    def resolve(name):
        return [str(executable)] if name == 'claude' else original_resolver(name)

    runtime = None
    compiler = None
    save()
    try:
        with patch.object(runtime_module, '_resolve_cli', side_effect=resolve):
            runtime = LocalRuntime(test_root / 'runtime')
            runtime.timeout = max(10, min(args.timeout, 180))
            store = Store(test_root / 'store')
            project = store.create_project('Claude 2.1.283 synthetic acceptance', template=False)
            source = '\\documentclass{article}\n\\usepackage{amsmath}\n\\begin{document}\n\\section{Validation}\\label{sec:validation}\nA synthetic validation paper. $ (x+1)^2 = x^2+2x+1 $.\n\\end{document}\n'
            store.write_file(project['id'], 'main.tex', source, None)
            for mode, intent in [
                ('chat', 'Reply with answer exactly LEMFOLIO_CLAUDE_283_OK, an empty changes array, and no claims of executed checks.'),
                ('edit', 'Append exactly one comment line % LEMFOLIO_CLAUDE_283_EDIT_OK after the last line of main.tex. Preserve all existing source lines and do not alter any other file. Return main.tex as a complete replacement file; do not claim compilation or proof.')]:
                project = store.get_project(project['id'])
                files = store.text_files(project['id'])
                domain = prepare_context(files, intent, mode)
                context = {'selection': select_context(files, intent), 'workflow': WORKFLOWS[mode], 'verification': verify_project(files), 'domain': domain}
                context_id = hashlib.sha256(json.dumps(context, sort_keys=True).encode()).hexdigest()
                started = time.monotonic()
                run = runtime.submit({'projectId': project['id'], 'intent': intent, 'mode': mode, 'provider': 'claude', 'baseRevision': project['revision'], 'contextSnapshotId': context_id, 'context': context, 'requiredCapabilities': ['research.generate'], 'verificationPlan': ['schema', 'no_unapproved_writes', 'human_diff_approval', 'isolated_compile']})
                print(json.dumps({'mode': mode, 'run_id': run['id'], 'status': run['status']}), flush=True)
                deadline = time.monotonic() + runtime.timeout + 15
                while run['status'] in {'queued', 'running'} and time.monotonic() < deadline:
                    time.sleep(.25)
                    run = runtime.inspect(run['id'])
                if run['status'] in {'queued', 'running'}:
                    runtime.interrupt(run['id'])
                    run = runtime.inspect(run['id'])
                original_unchanged = store.get_project(project['id'])['revision'] == project['revision']
                item = {'mode': mode, 'run_id': run['id'], 'status': run['status'], 'elapsed_seconds': round(time.monotonic()-started, 3), 'error': run['error'], 'result': run['result'], 'result_digest': run['resultDigest'], 'source_unchanged_before_approval': original_unchanged, 'event_types': [event['type'] for event in runtime.events(run['id'])]}
                report['runs'].append(item)
                save()
                if run['status'] != 'completed':
                    raise ValueError(f'Claude {mode} run failed: {run["error"]}')
                assert original_unchanged
                if mode == 'chat':
                    assert run['result']['answer'].strip() == 'LEMFOLIO_CLAUDE_283_OK'
                    assert run['result']['changes'] == []
                else:
                    assert len(run['result']['changes']) == 1 and run['result']['changes'][0]['path'] == 'main.tex'
                    proposal = store.propose(project['id'], run['result']['changes'], project['revision'], 'Synthetic Claude subscription adapter validation')
                    assert store.get_project(project['id'])['revision'] == project['revision']
                    store.decide(project['id'], proposal['id'], 'accept', proposal['digest'])
                    after = store.read_file(project['id'], 'main.tex')
                    normalized = after['content'].replace('\r\n', '\n')
                    assert normalized.startswith(source)
                    assert normalized[len(source):].strip() == '% LEMFOLIO_CLAUDE_283_EDIT_OK'
                    structure = verify_project(store.text_files(project['id']))
                    assert structure['status'] == 'passed'
                    compiler = Compiler(test_root / 'compiler', store, timeout=90)
                    build = compiler.submit(project['id'])
                    deadline = time.monotonic() + 110
                    while build['status'] in {'queued', 'running'} and time.monotonic() < deadline:
                        time.sleep(.25)
                        build = compiler.inspect(build['id'])
                    assert build['status'] == 'success', build.get('error')
                    pdf = compiler.artifact(build['id'], 'main.pdf')
                    assert pdf.read_bytes().startswith(b'%PDF-')
                    assessment = assess_verification(structure, build, [])
                    assert assessment['research_accepted'] is False and assessment['human_review_required']
                    item.update(proposal_id=proposal['id'], proposal_digest=proposal['digest'], approved_revision=store.get_project(project['id'])['revision'], build_id=build['id'], build_status=build['status'], build_image_id=build['image_id'], artifacts=build['artifacts'], independent_verification=assessment)
                save()
            report['executable_unchanged'] = hashlib.sha256(executable.read_bytes()).hexdigest() == before_hash
            assert report['executable_unchanged']
            report['status'] = 'passed'
    except Exception as exc:
        report.update(status='failed', failure=f'{type(exc).__name__}: {exc}')
        raise
    finally:
        if runtime is not None:
            runtime.close()
        if compiler is not None:
            compiler.close()
        save()
        print(json.dumps({'report': str(report_path), 'status': report['status']}), flush=True)


if __name__ == '__main__':
    main()
