import io
import json
from pathlib import Path
import tempfile
import sys
import time
import zipfile

import pytest
from fastapi.testclient import TestClient

from lemfolio.api import create_app


@pytest.fixture
def app_client():
    base = Path(__file__).resolve().parents[1] / '.test-data'
    base.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=base) as directory:
        app = create_app(Path(directory))
        with TestClient(app, base_url='http://127.0.0.1') as client:
            session = client.get('/api/session').json()
            client.headers['x-csrf-token'] = session['csrf']
            yield client, app


def test_origin_cookie_csrf_host_and_body_limits(app_client):
    client, app = app_client
    assert client.get('/api/health').json()['status'] == 'ok'
    assert client.get('/api/projects', headers={'Host': 'attacker.example'}).status_code == 403
    assert client.post('/api/projects', json={'name': 'X'}, headers={'Origin': 'https://attacker.example'}).status_code == 403
    assert client.post('/api/projects', json={'name': 'X'}, headers={'x-csrf-token': 'wrong'}).status_code == 403
    assert client.post('/api/projects', json={'name': 'X'}, headers={'Content-Length': 'bad'}).status_code == 400
    assert client.post('/api/projects', content=b'x' * (4 * 1024 * 1024 + 1)).status_code == 413
    client.cookies.clear()
    assert client.get('/api/projects').status_code == 401


def test_project_edit_approval_conflict_git_import_assets_and_evidence(app_client):
    client, app = app_client
    project = client.post('/api/projects', json={'name': 'API paper'}).json()
    pid = project['id']
    route = '/api/projects/' + pid
    main = client.get(route + '/file', params={'path': 'main.tex'}).json()
    proposal = client.post(route + '/proposals', json={'base_revision': project['revision'], 'summary': 'Add method', 'changes': [{'path': 'main.tex', 'content': main['content'].replace('Start your research here.', 'New method.')}]}).json()
    assert proposal['status'] == 'pending'
    assert client.get(route + '/file', params={'path': 'main.tex'}).json()['content'] == main['content']
    rich_diff = client.get(route + f"/proposals/{proposal['id']}/diff").json()
    assert rich_diff['status'] == 'available'
    endpoint = route + f"/proposals/{proposal['id']}/decide"
    assert client.post(endpoint, json={'decision': 'accept', 'digest': 'tampered'}).status_code == 409
    accepted = client.post(endpoint, json={'decision': 'accept', 'digest': proposal['digest']})
    assert accepted.status_code == 200, accepted.text
    verification = accepted.json()['independent_verification']
    assert verification['result']['build_id']
    assert verification['verification']['research_accepted'] is False
    assert client.get('/api/verifications/' + verification['id']).status_code == 200
    after = client.get(route + '/file', params={'path': 'main.tex'}).json()
    assert 'New method.' in after['content']
    assert client.put(route + '/file', json={'path': 'main.tex', 'content': 'bad overwrite'}).status_code == 409
    current = client.get(route).json()
    stale = client.post(route + '/proposals', json={'base_revision': current['revision'], 'summary': 'Later', 'changes': [{'path': 'later.tex', 'content': 'Later'}]}).json()
    asset = client.put(route + '/asset', params={'path': 'figures/结果图.png'}, content=b'\x89PNG\r\n\x1a\n')
    assert asset.status_code == 200, asset.text
    download = client.get(route + '/asset', params={'path': 'figures/结果图.png'})
    assert download.content.startswith(b'\x89PNG')
    assert "filename*=UTF-8''" in download.headers['content-disposition']
    assert client.post(route + f"/proposals/{stale['id']}/decide", json={'decision': 'accept', 'digest': stale['digest']}).status_code == 409
    assert client.get(route + '/index').status_code == 200
    verified = client.post(route + '/verify').json()
    assert verified['result']['mathematical_correctness'] == 'not_verified'
    assert client.get(route + '/evidence').json()
    history = client.get(route + '/history').json()
    assert history and history[0]['commit']
    assert client.post(route + '/restore', params={'revision': project['revision'], 'base_revision': client.get(route).json()['revision']}).status_code == 200
    assert client.get(route + '/file', params={'path': 'main.tex'}).json()['content'] == main['content']
    exported = client.get(route + '/export')
    assert exported.status_code == 200
    imported = client.post('/api/import?name=Imported', content=exported.content)
    assert imported.status_code == 201, imported.text
    assert client.get('/api/projects').json()[0]['id']
    assert client.post(route + '/runs', json={'intent': 'hello', 'provider': 'evil'}).status_code == 400
    assert client.post(route + '/runs', json={'intent': 'hello', 'mode': 'evil'}).status_code == 400


def test_runtime_proposal_binds_result_and_original_project_revision(app_client):
    client, app = app_client
    project = client.post('/api/projects', json={'name': 'Runtime contract'}).json()
    # Inject a recorded completed result to test API/store wiring independently of quota/network.
    run_id = 'f' * 32
    # The fixture now includes the real immutable source identity required by
    # the hardened API; the original approval and unchanged-source assertions
    # below remain unchanged.
    from lemfolio.harness import select_context
    snap=app.state.store.capture_project(project['id'])
    archive_digest=app.state.evidence.save_snapshot(snap)
    task={'projectId':project['id'],'baseRevision':project['revision'],'baseStateRevision':snap['state_revision'],'sourceSnapshotId':snap['snapshot_id'],'sourceSnapshotDigest':archive_digest,'sourceManifest':snap['source_hashes'],'main_file':snap['main_file'],'context':{'selection':select_context(snap['text_files'],'Add note',source_hashes=snap['source_hashes'])}}
    record = {'id': run_id, 'status': 'completed', 'task': task, 'result': {'answer': 'Propose', 'changes': [{'path': 'note.tex', 'content': 'Proposed note'}], 'checks': []}, 'resultDigest': 'bound-result'}
    app.state.runtime.inspect = lambda _: record
    decisions = []
    app.state.runtime.approve = lambda _, decision: decisions.append(decision)
    result = client.post(f'/api/runs/{run_id}/proposal')
    assert result.status_code == 201, result.text
    assert result.json()['status'] == 'pending'
    assert decisions == [], 'Creating a proposal does not accept its contents'
    evidence = client.get('/api/projects/' + project['id'] + '/evidence').json()
    assert evidence[0]['result']['result_digest'] == 'bound-result'
    assert evidence[0]['result']['content_accepted'] is False
    assert app.state.store.get_project(project['id'])['revision'] == project['revision']


def test_api_submits_through_real_runtime_contract_and_replays_terminal_event(app_client, monkeypatch):
    client, app = app_client
    project = client.post('/api/projects', json={'name': 'Runtime submission'}).json()
    result = {'answer': 'CONTRACT_OK', 'changes': [], 'checks': ['Independent compile not requested']}
    event = {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': json.dumps(result)}}
    code = 'import sys; sys.stdin.read(); print(' + repr(json.dumps(event)) + ')'
    monkeypatch.setattr(app.state.runtime, '_command', lambda *_: [sys.executable, '-c', code])
    # This transport test owns a synthetic subprocess, not a subscription; keep
    # its admission independent of the host's real account/login availability.
    monkeypatch.setattr(app.state.runtime,'capabilities',lambda:{'capabilities':['research.generate']})
    submitted = client.post('/api/projects/' + project['id'] + '/runs', json={'intent': 'Check the supplied context', 'mode': 'math', 'provider': 'codex', 'math_checks': [{'lhs': '(x+1)**2', 'rhs': 'x**2+2*x+1'}]})
    assert submitted.status_code == 202, submitted.text
    run_id = submitted.json()['id']
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        run = client.get('/api/runs/' + run_id).json()
        if run['status'] not in {'queued', 'running'}:
            break
        time.sleep(.02)
    assert run['status'] == 'completed', run
    assert run['result']['answer'] == 'CONTRACT_OK'
    assert run['task']['requiredCapabilities'] == ['research.generate']
    assert run['task']['context']['selection']['files']
    tools = run['task']['context']['domain']['tool_evidence']
    assert tools[0]['tool'] == 'math_check'
    assert tools[0]['result']['status'] == 'symbolically_equivalent'
    assert tools[0]['provenance']['model_claim'] is False
    events = client.get('/api/runs/' + run_id + '/event-log').json()
    assert events[-1]['type'] == 'completed'
    stream = client.get('/api/runs/' + run_id + '/events')
    assert 'event: done' in stream.text and 'CONTRACT_OK' in stream.text
    verified = client.post('/api/runs/' + run_id + '/verify')
    assert verified.status_code == 202, verified.text
    report = verified.json()
    assert report['result']['build_id']
    assert report['result']['tool_evidence'][0]['result']['status'] == 'symbolically_equivalent'
    assert report['verification']['research_accepted'] is False


def test_proposal_conflict_reports_real_drift(app_client):
    client, _ = app_client
    project = client.post('/api/projects', json={'name': 'Conflict'}).json()
    route = f"/api/projects/{project['id']}"
    file = client.get(route + '/file', params={'path': 'main.tex'}).json()
    original = file['content']
    proposal = client.post(route + '/proposals', json={'base_revision': project['revision'], 'summary': 'Add a line', 'changes': [{'path': 'main.tex', 'content': original + '% from the proposal\n'}]}).json()
    same = client.get(route + f"/proposals/{proposal['id']}/conflict").json()
    assert same['stale'] is False and same['files'] == [{'path': 'main.tex', 'changed': False, 'diff': None}]
    client.put(route + '/file', json={'path': 'main.tex', 'content': '% edited after the proposal\n' + original, 'expected_hash': file['sha256']})
    moved = client.get(route + f"/proposals/{proposal['id']}/conflict").json()
    assert moved['stale'] is True and moved['base_revision'] == project['revision'] and moved['current_revision'] != project['revision']
    assert moved['files'][0]['changed'] is True and '+% edited after the proposal' in moved['files'][0]['diff']
    assert '% from the proposal' not in moved['files'][0]['diff']
    assert moved['changes_elsewhere'] is False
    assert client.post(route + f"/proposals/{proposal['id']}/decide", json={'decision': 'accept', 'digest': proposal['digest']}).status_code == 409
    assert '% from the proposal' not in client.get(route + '/file', params={'path': 'main.tex'}).json()['content']
