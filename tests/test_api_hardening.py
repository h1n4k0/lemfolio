"""Adversarial API regressions for snapshot identity and post-apply semantics."""
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import lemfolio.api as api_module
from lemfolio.api import create_app
from lemfolio.editing import resolve_changes
from lemfolio.harness import select_context
from lemfolio.runtime import validate_result_for_task
from lemfolio.contracts import CONTRACT_VERSION


@pytest.fixture
def hard_app():
    base=Path(__file__).resolve().parents[1]/'.test-data';base.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=base) as directory:
        app=create_app(Path(directory))
        with TestClient(app,base_url='http://127.0.0.1') as client:
            client.headers['x-csrf-token']=client.get('/api/session').json()['csrf']
            yield client,app,Path(directory)


def project(client):
    return client.post('/api/projects',json={'name':'Hardening synthetic paper'}).json()


def proposal(client,p,content='Changed safely'):
    r=client.post(f"/api/projects/{p['id']}/proposals",json={'base_revision':p['revision'],'base_state_revision':p['state_revision'],'summary':'Synthetic change','changes':[{'path':'main.tex','content':content}]})
    assert r.status_code==201,r.text
    return r.json()


def task_fixture(app,p,*,max_chars=60000):
    snap=app.state.store.capture_project(p['id'])
    digest=app.state.evidence.save_snapshot(snap)
    context=select_context(snap['text_files'],'Edit selected content',max_chars=max_chars,source_hashes=snap['source_hashes'])
    task={'projectId':p['id'],'intent':'Edit selected content','provider':'codex','mode':'edit','baseRevision':snap['revision'],'baseStateRevision':snap['state_revision'],'main_file':snap['main_file'],'sourceManifest':snap['source_hashes'],'sourceSnapshotId':snap['snapshot_id'],'sourceSnapshotDigest':digest,'contextSnapshotId':'snapshot-test','context':{'selection':context}}
    return task,snap


def test_api_index_uses_one_capture_not_before_and_after_reads(hard_app,monkeypatch):
    client,app,_=hard_app;p=project(client)
    snap=app.state.store.capture_project(p['id'])
    monkeypatch.setattr(app.state.store,'capture_project',lambda _:snap)
    monkeypatch.setattr(app.state.store,'text_files',lambda _:pytest.fail('separate text read recreates ABA'))
    monkeypatch.setattr(app.state.store,'get_project',lambda _:pytest.fail('separate revision read recreates ABA'))
    result=client.get(f"/api/projects/{p['id']}/index")
    assert result.status_code==200,result.text
    assert result.json()['snapshot_id']==snap['snapshot_id']
    assert result.json()['state_revision']==snap['state_revision']


@pytest.mark.parametrize('failure_at',['list','write','verifier','compiler'])
def test_committed_apply_stays_successful_when_independent_phase_fails(hard_app,monkeypatch,failure_at):
    client,app,_=hard_app;p=project(client);change=proposal(client,p)
    def fail(*_,**__):raise OSError('synthetic verification disk/tool failure')
    if failure_at=='list':monkeypatch.setattr(app.state.evidence,'list',fail)
    elif failure_at=='write':monkeypatch.setattr(app.state.evidence,'write',fail)
    elif failure_at=='verifier':monkeypatch.setattr(api_module,'verify_project',fail)
    else:monkeypatch.setattr(app.state.compiler,'submit',fail)
    endpoint=f"/api/projects/{p['id']}/proposals/{change['id']}/decide"
    response=client.post(endpoint,json={'decision':'accept','digest':change['digest']})
    assert response.status_code==200,response.text
    accepted=response.json()
    assert accepted['status']=='accepted' and accepted['apply_status']=='applied'
    assert accepted['verification_status']=='failed'
    assert accepted['verification_error']['retryable'] is True
    assert app.state.store.read_file(p['id'],'main.tex')['content']=='Changed safely'
    first_state=app.state.store.get_project(p['id'])['state_revision']
    again=client.post(endpoint,json={'decision':'accept','digest':change['digest']})
    assert again.status_code==200 and again.json()['apply_status']=='applied'
    assert app.state.store.get_project(p['id'])['state_revision']==first_state


def test_entry_switch_excludes_old_compile_and_tool_receipts(hard_app,monkeypatch):
    client,app,_=hard_app;p=project(client);pid=p['id']
    app.state.store.write_file(pid,'alternate.tex','\\documentclass{article}\\begin{document}B\\end{document}',None)
    before=app.state.store.capture_project(pid)
    app.state.evidence.append(pid,'symbolic_math',{'input':{'lhs':'x','rhs':'x'},'status':'symbolically_equivalent'},before)
    old={'id':'a'*32,'base_revision':before['revision'],'state_revision':before['state_revision'],'main_file':'main.tex','engine':'pdflatex','status':'success','artifacts':[]}
    app.state.store.set_main_file(pid,'alternate.tex',before['revision'],base_state_revision=before['state_revision'])
    assert app.state.store.get_project(pid)['revision']==before['revision']
    monkeypatch.setattr(app.state.compiler,'list_builds',lambda _:[old])
    submitted=[]
    def submit(task):
        submitted.append(task);return {'id':'b'*32,'status':'queued','task':task,'result':None,'error':None}
    monkeypatch.setattr(app.state.runtime,'submit',submit)
    response=client.post(f'/api/projects/{pid}/runs',json={'intent':'Inspect current entry','provider':'codex'})
    assert response.status_code==202,response.text
    task=submitted[0]
    assert task['main_file']=='alternate.tex'
    assert task['baseStateRevision']!=before['state_revision']
    assert task['context']['domain']['compile_evidence']==[]
    assert task['context']['domain']['prior_evidence_ids']==[]


def test_full_replacement_of_truncated_file_is_rejected_before_proposal(hard_app,monkeypatch):
    client,app,_=hard_app;p=project(client);pid=p['id']
    main=app.state.store.read_file(pid,'main.tex')
    app.state.store.write_file(pid,'main.tex','KEEP_UNSEEN\n'+'x'*90000+'\nTAIL',main['sha256'])
    task,snap=task_fixture(app,p,max_chars=1000)
    result={'answer':'Unsafe','changes':[{'path':'main.tex','content':task['context']['selection']['files'][0]['content']}],'checks':[]}
    monkeypatch.setattr(app.state.runtime,'inspect',lambda _:{'status':'completed','task':task,'result':result,'resultDigest':'digest'})
    response=client.post('/api/runs/'+'c'*32+'/proposal')
    assert response.status_code in {400,409},response.text
    assert app.state.store.get_project(pid)['revision']==snap['revision']
    assert app.state.store.proposals(pid)==[]


def test_guarded_local_edit_preserves_large_unseen_source_and_bom(hard_app,monkeypatch):
    client,app,_=hard_app;p=project(client);pid=p['id']
    main=app.state.store.read_file(pid,'main.tex')
    source='\ufeffANCHOR unique 保留\r\n'+('UNSEEN_'*17000)+'\r\nEND'
    app.state.store.write_file(pid,'main.tex',source,main['sha256'])
    task,snap=task_fixture(app,p,max_chars=1000)
    result={'answer':'Bounded edit','changes':[{'path':'main.tex','content':None,'edits':[{'old_text':'ANCHOR unique 保留','new_text':'CHANGED unique 保留'}]}],'checks':[]}
    validate_result_for_task(result,task)
    resolved=resolve_changes(result['changes'],snap,coverage=task['context']['selection']['coverage'])
    assert resolved[0]['content']==source.replace('ANCHOR unique 保留','CHANGED unique 保留',1)
    monkeypatch.setattr(app.state.runtime,'inspect',lambda _:{'status':'completed','task':task,'result':result,'resultDigest':'digest'})
    response=client.post('/api/runs/'+'d'*32+'/proposal')
    assert response.status_code==201,response.text
    pending=response.json()
    assert pending['changes'][0]['content']==resolved[0]['content']
    assert app.state.store.get_project(pid)['revision']==snap['revision']


@pytest.mark.parametrize('source,edits',[('duplicate duplicate',[{'old_text':'duplicate','new_text':'X'}]),('abcdef',[{'old_text':'abc','new_text':'X'},{'old_text':'bcde','new_text':'Y'}])])
def test_ambiguous_or_overlapping_local_edits_are_refused(hard_app,source,edits):
    client,app,_=hard_app;p=project(client);pid=p['id'];main=app.state.store.read_file(pid,'main.tex')
    app.state.store.write_file(pid,'main.tex',source,main['sha256']);snap=app.state.store.capture_project(pid)
    with pytest.raises(Exception,match='exactly once|overlap'):
        resolve_changes([{'path':'main.tex','content':None,'edits':edits}],snap)
    assert app.state.store.get_project(pid)['revision']==snap['revision']


def test_model_proposal_rejects_config_aba_and_corrupt_snapshot(hard_app,monkeypatch):
    client,app,root=hard_app;p=project(client);pid=p['id']
    app.state.store.write_file(pid,'other.tex','Other source',None)
    task,snap=task_fixture(app,p)
    result={'answer':'Propose','changes':[{'path':'note.tex','content':'New'}],'checks':[]}
    monkeypatch.setattr(app.state.runtime,'inspect',lambda _:{'status':'completed','task':task,'result':result,'resultDigest':'digest'})
    app.state.store.set_main_file(pid,'other.tex',snap['revision'])
    app.state.store.set_main_file(pid,'main.tex',snap['revision'])
    rejected=client.post('/api/runs/'+'e'*32+'/proposal')
    assert rejected.status_code==409,rejected.text
    assert app.state.store.proposals(pid)==[]
    archive=root/'source_snapshots'/(task['sourceSnapshotId']+'.zip')
    archive.write_bytes(archive.read_bytes()+b'corrupt')
    rejected=client.post('/api/runs/'+'e'*32+'/proposal')
    assert rejected.status_code==409 and 'integrity' in rejected.text
    assert client.post('/api/runs/'+'e'*32+'/verify').status_code==409


def test_failed_oversized_task_does_not_publish_prepared_evidence(hard_app,monkeypatch):
    client,app,_=hard_app;p=project(client)
    monkeypatch.setattr(api_module,'prepare_context',lambda *a,**kw:{'too_large':'x'*1_100_000})
    response=client.post(f"/api/projects/{p['id']}/runs",json={'intent':'Inspect','provider':'codex'})
    assert response.status_code==413,response.text
    assert app.state.evidence.list(p['id'])==[]
    assert app.state.runtime.list_runs(p['id'])==[]


def test_contract_version_and_generated_dto_source_do_not_drift(hard_app):
    client,_,_=hard_app
    session=client.get('/api/session')
    assert session.json()['contract_version']==CONTRACT_VERSION
    assert session.headers['x-lemfolio-contract-version']==CONTRACT_VERSION
    assert client.get('/api/projects',headers={'Host':'not-local.example'}).headers['x-lemfolio-contract-version']==CONTRACT_VERSION
    rejected=client.post('/api/projects',json={'name':'Must not be created'},headers={'X-Lemfolio-Contract-Version':'2.0.0'})
    assert rejected.status_code==409
    assert client.get('/api/projects').json()==[]
    protocol=client.get('/api/contract').json()
    raw=Path(__file__).resolve().parents[1]/'contracts/v1/schema.json'
    assert protocol['schema_sha256']==hashlib.sha256(raw.read_bytes().replace(b'\r\n',b'\n')).hexdigest()
    assert protocol['schema']['$defs']['TaskInput']['properties']['provider']['pattern']=='^[a-z][a-z0-9-]{0,31}$'
    subprocess.run([sys.executable,'scripts/generate_contracts.py','--check'],cwd=raw.parents[2],check=True,capture_output=True)
    schema=client.get('/openapi.json').json()
    assert schema['paths']['/api/projects']['get']['responses']['200']['content']['application/json']['schema']['items']['$ref'].endswith('/Project')
    assert schema['components']['schemas']['Change']['oneOf']==protocol['schema']['$defs']['Change']['oneOf']


def test_verifier_and_compiler_consume_same_snapshot_even_if_live_source_changes(hard_app,monkeypatch):
    client,app,_=hard_app;p=project(client);change=proposal(client,p,'Approved snapshot')
    original_verify=api_module.verify_project
    captured=[]
    def verify(files):
        current=app.state.store.read_file(p['id'],'main.tex')
        app.state.store.write_file(p['id'],'main.tex','Concurrent later source',current['sha256'])
        return original_verify(files)
    def submit(pid,main_file,engine,snapshot):
        captured.append(snapshot)
        record={'id':'9'*32,'project_id':pid,'base_revision':snapshot['revision'],'state_revision':snapshot['state_revision'],'snapshot_id':snapshot['snapshot_id'],'source_hashes':snapshot['source_hashes'],'main_file':main_file,'engine':engine,'image_id':None,'status':'queued','artifacts':[],'error':None}
        record['identity_digest']=app.state.compiler._identity(record)
        return record
    monkeypatch.setattr(api_module,'verify_project',verify)
    monkeypatch.setattr(app.state.compiler,'submit',submit)
    monkeypatch.setattr(app.state.compiler,'inspect',lambda _: {**submit.__dict__['receipt']})
    # Retain exactly the queued receipt returned by the frozen compilation call.
    original_submit=submit
    def recording(*args,**kwargs):
        result=original_submit(*args,**kwargs);submit.__dict__['receipt']=result;return result
    monkeypatch.setattr(app.state.compiler,'submit',recording)
    result=client.post(f"/api/projects/{p['id']}/proposals/{change['id']}/decide",json={'decision':'accept','digest':change['digest']})
    assert result.status_code==200,result.text
    record=result.json()['independent_verification']
    assert record['current_state_matches'] is False
    assert captured[0]['text_files']['main.tex']=='Approved snapshot'
    assert record['snapshot_id']==captured[0]['snapshot_id']==record['build']['snapshot_id']
    assert record['result']['source_hashes']==captured[0]['source_hashes']
    assert record['result']['structure']['metrics']==original_verify({'main.tex':'Approved snapshot','references.bib':'% Add verified BibTeX entries here.\r\n'})['metrics']


def test_prepared_verification_restart_is_not_reported_complete():
    base=Path(__file__).resolve().parents[1]/'.test-data';base.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=base) as directory:
        root=Path(directory)
        first=create_app(root)
        with TestClient(first,base_url='http://127.0.0.1') as client:
            client.headers['x-csrf-token']=client.get('/api/session').json()['csrf']
            p=project(client);snap=first.state.store.capture_project(p['id'])
            pending=first.state.evidence.append(p['id'],'research_verification',{'verification_status':'pending','engine':'pdflatex','build_id':None,'source_hashes':snap['source_hashes']},snap)
        second=create_app(root)
        with TestClient(second,base_url='http://127.0.0.1') as client:
            client.headers['x-csrf-token']=client.get('/api/session').json()['csrf']
            response=client.get('/api/verifications/'+pending['id'])
            assert response.status_code==200,response.text
            report=response.json()
            assert report['result']['verification_status']=='failed'
            assert 'restarted' in report['result']['verification_error']
            assert report['verification']['research_accepted'] is False


def test_success_receipt_requires_matching_entry_engine_identity_and_artifact_hash(hard_app):
    client,app,_=hard_app;p=project(client);snap=app.state.store.capture_project(p['id']);bid='8'*32
    directory=app.state.compiler.root/bid;directory.mkdir()
    data=b'%PDF-SYNTHETIC-HASH-CHECK';(directory/'main.pdf').write_bytes(data)
    receipt={'id':bid,'project_id':p['id'],'base_revision':snap['revision'],'state_revision':snap['state_revision'],'snapshot_id':snap['snapshot_id'],'source_hashes':snap['source_hashes'],'main_file':'main.tex','engine':'pdflatex','image_id':'sha256:synthetic','status':'success','created_at':0,'error':None,'artifacts':[{'name':'main.pdf','size':len(data),'sha256':hashlib.sha256(data).hexdigest()}]}
    receipt['identity_digest']=app.state.compiler._identity(receipt);app.state.compiler._save(receipt)
    evidence=app.state.evidence.append(p['id'],'research_verification',{'verification_status':'queued','engine':'pdflatex','build_id':bid,'structure':{'status':'passed'},'source_hashes':snap['source_hashes']},snap)
    endpoint='/api/verifications/'+evidence['id']
    assert client.get(endpoint).json()['verification']['status']=='human_review_required'
    receipt['engine']='xelatex';app.state.compiler._save(receipt)
    assert client.get(endpoint).json()['verification']['status']=='failed'
    receipt['engine']='pdflatex';app.state.compiler._save(receipt)
    (directory/'main.pdf').write_bytes(b'tampered')
    assert client.get(endpoint).json()['verification']['status']=='failed'


@pytest.mark.parametrize('change',[{'path':'other.tex'},{'path':'other.tex','edits':None},{'path':'other.tex','content':'X','edits':[{'old_text':'a','new_text':'b'}]},{'path':'other.tex','edits':[]}])
def test_missing_or_conflicting_change_operation_is_not_a_deletion(hard_app,change):
    client,app,_=hard_app;p=project(client)
    app.state.store.write_file(p['id'],'other.tex','Keep this',None)
    p=app.state.store.get_project(p['id'])
    response=client.post(f"/api/projects/{p['id']}/proposals",json={'base_revision':p['revision'],'changes':[change]})
    assert response.status_code in {400,422},response.text
    assert app.state.store.read_file(p['id'],'other.tex')['content']=='Keep this'
    assert app.state.store.proposals(p['id'])==[]


def test_explicit_null_content_keeps_manual_deletion_semantics(hard_app):
    client,app,_=hard_app;p=project(client)
    app.state.store.write_file(p['id'],'other.tex','Delete explicitly',None)
    p=app.state.store.get_project(p['id'])
    response=client.post(f"/api/projects/{p['id']}/proposals",json={'base_revision':p['revision'],'changes':[{'path':'other.tex','content':None}]})
    assert response.status_code==201,response.text
    assert response.json()['changes'][0]['content'] is None
    assert app.state.store.read_file(p['id'],'other.tex')['content']=='Delete explicitly'


def test_non_utf8_data_file_roundtrips_without_blocking_visible_source_edits(hard_app,monkeypatch):
    client,app,_=hard_app
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w') as archive:
        archive.writestr('main.tex','Visible source')
        archive.writestr('binary.dat',b'\xff\x00')
    p=app.state.store.import_zip('Mixed encoding synthetic project',stream.getvalue())
    task,snap=task_fixture(app,p)
    frozen=app.state.evidence.load_snapshot(task['sourceSnapshotId'],task['sourceSnapshotDigest'])
    assert frozen['files']['binary.dat']==b'\xff\x00'
    assert 'binary.dat' not in frozen['text_files']
    assert 'binary.dat' in task['sourceManifest']
    output={'answer':'Visible change','changes':[{'path':'main.tex','content':'Updated visible source'}],'checks':[]}
    monkeypatch.setattr(app.state.runtime,'inspect',lambda _:{'status':'completed','task':task,'result':output,'resultDigest':'digest'})
    proposed=client.post('/api/runs/'+'7'*32+'/proposal')
    assert proposed.status_code==201,proposed.text
    assert app.state.store.read_bytes(p['id'],'binary.dat')==b'\xff\x00'


def test_failed_verification_can_retry_without_reapplying_source(hard_app,monkeypatch):
    client,app,_=hard_app;p=project(client);change=proposal(client,p,'Approved state for retry')
    original=api_module.verify_project
    def fail(_):raise OSError('interrupted verifier')
    monkeypatch.setattr(api_module,'verify_project',fail)
    response=client.post(f"/api/projects/{p['id']}/proposals/{change['id']}/decide",json={'decision':'accept','digest':change['digest']})
    assert response.status_code==200 and response.json()['apply_status']=='applied'
    failed=response.json()['independent_verification']
    state=app.state.store.get_project(p['id'])['state_revision']
    monkeypatch.setattr(api_module,'verify_project',original)
    retry=client.post('/api/verifications/'+failed['id']+'/retry')
    assert retry.status_code==202,retry.text
    assert retry.json()['id']!=failed['id']
    assert retry.json()['result']['build_id']
    assert app.state.store.get_project(p['id'])['state_revision']==state
    file=app.state.store.read_file(p['id'],'main.tex')
    app.state.store.write_file(p['id'],'main.tex','Changed after approved state',file['sha256'])
    assert client.post('/api/verifications/'+failed['id']+'/retry').status_code==409


def test_legacy_completed_run_cannot_claim_current_state_verification(hard_app,monkeypatch):
    client,app,_=hard_app;p=project(client)
    monkeypatch.setattr(app.state.runtime,'inspect',lambda _:{'status':'completed','task':{'projectId':p['id'],'baseRevision':p['revision']}})
    rejected=client.post('/api/runs/'+'6'*32+'/verify')
    assert rejected.status_code==409
    assert app.state.evidence.list(p['id'])==[]


def test_provider_switch_interrupt_and_restart_keep_independent_runs(hard_app,monkeypatch):
    import time
    client,app,root=hard_app;p=project(client)
    result={'answer':'PROVIDER_OK','changes':[],'checks':[]}
    def command(provider,task,directory):
        event={'type':'result','structured_output':result} if provider=='claude' else {'type':'item.completed','item':{'type':'agent_message','text':json.dumps(result)}}
        script='import sys,time;sys.stdin.read();'+('time.sleep(15);' if provider=='codex' else '')+'print('+repr(json.dumps(event))+')'
        return [sys.executable,'-c',script]
    monkeypatch.setattr(app.state.runtime,'_command',command)
    monkeypatch.setattr(app.state.runtime,'capabilities',lambda:{'capabilities':['research.generate']})
    first=client.post(f"/api/projects/{p['id']}/runs",json={'intent':'First provider','provider':'codex'}).json()
    second=client.post(f"/api/projects/{p['id']}/runs",json={'intent':'Second provider','provider':'claude'}).json()
    deadline=time.monotonic()+10
    while time.monotonic()<deadline:
        complete=app.state.runtime.inspect(second['id'])
        if complete['status']=='completed':break
        time.sleep(.05)
    assert complete['status']=='completed'
    interrupted=client.post('/api/runs/'+first['id']+'/interrupt').json()
    assert interrupted['status']=='interrupted'
    assert app.state.runtime.inspect(second['id'])['result']['answer']=='PROVIDER_OK'
    app.state.runtime.close()
    from lemfolio.runtime import LocalRuntime
    reopened=LocalRuntime(root/'runtime')
    try:
        assert reopened.inspect(first['id'])['task']['provider']=='codex'
        assert reopened.inspect(second['id'])['task']['provider']=='claude'
        assert reopened.inspect(second['id'])['status']=='completed'
        assert reopened.recover(first['id'])['execution_resume_supported'] is False
    finally:
        reopened.close()


def test_workflow_projection_is_typed_read_only_and_never_auto_approves(hard_app):
    from lemfolio.contracts import WorkflowView
    client,app,_=hard_app;p=project(client)
    before=app.state.store.capture_project(p['id'])
    empty=client.get(f"/api/projects/{p['id']}/workflow")
    assert empty.status_code==200,empty.text
    view=WorkflowView.model_validate(empty.json())
    assert view.contract_version==CONTRACT_VERSION
    assert len(view.nodes)==8
    assert app.state.runtime.list_runs(p['id'])==[]
    assert app.state.evidence.list(p['id'])==[]
    change=proposal(client,p,'Workflow pending diff')
    pending=client.get(f"/api/projects/{p['id']}/workflow").json()
    nodes={node['id']:node for node in pending['nodes']}
    assert nodes['approval']['status']=='pending_review'
    assert nodes['approval']['details']['research_accepted'] is False
    assert app.state.store.get_proposal(p['id'],change['id'])['status']=='pending'
    assert app.state.store.capture_project(p['id'])['state_revision']==before['state_revision']
    assert all(action['kind'] not in {'accept','apply'} for node in pending['nodes'] for action in node['actions'])


def test_workflow_failed_verification_has_safe_retry_not_false_acceptance(hard_app,monkeypatch):
    client,app,_=hard_app;p=project(client);change=proposal(client,p,'Approved workflow')
    def fail(*_,**__):raise OSError('synthetic workflow verifier failure')
    monkeypatch.setattr(api_module,'verify_project',fail)
    applied=client.post(f"/api/projects/{p['id']}/proposals/{change['id']}/decide",json={'decision':'accept','digest':change['digest']}).json()
    view=client.get(f"/api/projects/{p['id']}/workflow")
    assert view.status_code==200,view.text
    nodes={node['id']:node for node in view.json()['nodes']}
    assert nodes['approval']['status']=='passed'
    assert nodes['verification']['status']=='failed'
    retry=next(action for action in nodes['verification']['actions'] if action['kind']=='retry_verification')
    assert retry['enabled'] is True
    assert retry['target_id']==applied['independent_verification']['id']
    assert nodes['verification']['details']['research_accepted'] is False


def test_workflow_cannot_join_a_run_from_another_project(hard_app,monkeypatch):
    client,app,_=hard_app;p=project(client)
    monkeypatch.setattr(app.state.runtime,'inspect',lambda _: {'id':'1'*32,'task':{'projectId':'other-project'}})
    response=client.get(f"/api/projects/{p['id']}/workflow?run_id="+'1'*32)
    assert response.status_code==404


@pytest.mark.parametrize('value',['false','true',0,1])
def test_pydantic_does_not_coerce_non_schema_booleans(hard_app,value):
    client,app,_=hard_app
    rejected=client.post('/api/projects',json={'name':'Invalid typed request','template':value})
    assert rejected.status_code==422,rejected.text
    assert app.state.store.list_projects()==[]
