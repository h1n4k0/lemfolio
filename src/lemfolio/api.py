"""Loopback application API. Research content never becomes runtime authority."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import hashlib
import io
import json
import os
from pathlib import Path
import re
import secrets
import threading
import time
from urllib.parse import quote, urlsplit
import uuid

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.types import ASGIApp, Receive, Scope, Send
from fastapi.exceptions import RequestValidationError

from . import __version__
from .compiler import Compiler
from .harness import WORKFLOWS, index_project, select_context, verify_project, math_check, lookup_reference, search_literature
from .runtime import LocalRuntime, KeelRuntimeAdapter, RuntimeError as ProviderError, validate_task, validate_result_for_task
from .store import Store, StoreError
from .diff_engine import describe_diff
from .coordinator import prepare_context, assess_verification
from .evidence import EvidenceStore
from .editing import resolve_changes
from .contracts import (ProjectInput, FileInput, ProposalInput, DecisionInput, TaskInput, MathInput, CompileInput,
    Project, PaperFile, TextFile, Proposal, DecisionResponse, Run, RunSummary, Build, Session, VerificationRecord,
    RuntimeEvent, HistoryEntry, SyncTexPosition, ModelCatalog, Harness, HarnessInput, ProposalConflict, CONTRACT_VERSION, CONTRACT_SCHEMA_SHA256, protocol_schema)
from .contracts import WorkflowView
from .workflow import build_workflow


class BodyLimit:
    """Bound request bodies even for chunked requests without Content-Length."""
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        limit = 64 * 1024 * 1024 if scope['path'] == '/api/import' else 22 * 1024 * 1024 if scope['path'].endswith('/asset') else 4 * 1024 * 1024
        chunks = []
        size = 0
        while True:
            message = await receive()
            if message['type'] == 'http.disconnect':
                return
            size += len(message.get('body', b''))
            if size > limit:
                return await JSONResponse({'detail': 'Request body exceeds endpoint limit'}, 413)(scope, receive, send)
            chunks.append(message.get('body', b''))
            if not message.get('more_body', False):
                break
        sent = False
        async def replay():
            nonlocal sent
            if not sent:
                sent = True
                return {'type': 'http.request', 'body': b''.join(chunks), 'more_body': False}
            return await receive()
        return await self.app(scope, replay, send)


class ContractHeaders:
    def __init__(self,app):
        self.app=app

    async def __call__(self,scope,receive,send):
        async def reply(message):
            if message['type']=='http.response.start':
                headers=[(key,value) for key,value in message.get('headers',[]) if key.lower()!=b'x-lemfolio-contract-version']
                message={**message,'headers':headers+[(b'x-lemfolio-contract-version',CONTRACT_VERSION.encode())]}
            await send(message)
        await self.app(scope,receive,reply)


def create_app(data_root: Path | None = None):
    root = Path(data_root or os.environ.get("LEMFOLIO_DATA") or Path(__file__).resolve().parents[2] / "data").resolve()
    root.mkdir(parents=True, exist_ok=True)
    store = Store(root)
    runtime = LocalRuntime(root / "runtime")
    compiler = Compiler(root, store)
    evidence_store = EvidenceStore(root)
    session = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)
    web = Path(__file__).resolve().parents[2] / "web"

    @asynccontextmanager
    async def lifespan(app):
        yield
        for run in runtime.list_runs():
            if run["status"] in {"queued", "running"}:
                runtime.interrupt(run["id"])
        await asyncio.to_thread(compiler.close)
        runtime.close()

    app = FastAPI(title="Lemfolio",version=CONTRACT_VERSION,lifespan=lifespan,docs_url=None,redoc_url=None)
    app.state.store = store
    app.state.runtime = runtime
    app.state.compiler = compiler
    app.state.evidence = evidence_store

    @app.middleware("http")
    async def local_session(request: Request, call_next):
        try:
            host = urlsplit("http://" + request.headers.get("host", "")).hostname
        except ValueError:
            host = None
        if host not in {"127.0.0.1", "localhost", "::1"}:
            return JSONResponse({"detail": "Only loopback hosts are allowed"}, 403)
        origin = request.headers.get("origin")
        expected_origin = f"{request.url.scheme}://{request.headers.get('host')}"
        if origin and origin != expected_origin:
            return JSONResponse({"detail": "Cross-origin requests are refused"}, 403)
        requested_contract=request.headers.get('x-lemfolio-contract-version')
        if requested_contract and requested_contract!=CONTRACT_VERSION:
            return JSONResponse({'detail':'Unsupported Lemfolio contract version','supported':CONTRACT_VERSION},409)
        try:
            length = int(request.headers.get("content-length", "0"))
            if length < 0:
                raise ValueError()
        except ValueError:
            return JSONResponse({"detail": "Invalid Content-Length"}, 400)
        if length > 64 * 1024 * 1024:
            return JSONResponse({"detail": "Request exceeds 64 MiB"}, 413)
        if request.url.path.startswith("/api/") and request.url.path not in {"/api/health", "/api/session"}:
            if not secrets.compare_digest(request.cookies.get("lemfolio_session", ""), session):
                return JSONResponse({"detail": "Open Lemfolio to establish a local session"}, 401)
            if request.method not in {"GET", "HEAD", "OPTIONS"} and not secrets.compare_digest(request.headers.get("x-csrf-token", ""), csrf):
                return JSONResponse({"detail": "Missing or invalid CSRF token"}, 403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; frame-src 'self'; object-src 'self'; base-uri 'none'; frame-ancestors 'self'"
        response.headers["Cache-Control"] = "no-store"
        response.headers['X-Lemfolio-Contract-Version'] = CONTRACT_VERSION
        return response

    @app.exception_handler(RequestValidationError)
    async def request_validation(_request, exc):
        # Preserve the established unknown-provider/mode error semantics while
        # letting the generated DTO enforce its types and bounds.
        code = 400 if any(error.get('loc',())[-1:] in [('provider',),('mode',)] for error in exc.errors()) else 422
        return JSONResponse({'detail':[{'loc':list(item.get('loc',())),'type':item['type'],'msg':item['msg']} for item in exc.errors()]},status_code=code)

    @app.exception_handler(StoreError)
    async def store_error(_request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=exc.status_code)

    @app.exception_handler(ProviderError)
    async def provider_error(_request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=exc.status_code)

    @app.exception_handler(FileNotFoundError)
    async def missing(_request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=404)

    @app.exception_handler(ValueError)
    async def invalid(_request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    def save_evidence(project_id, kind, result, snapshot=None, *, identity=None):
        return evidence_store.append(project_id,kind,result,snapshot or store.capture_project(project_id),identity=identity)

    def project_evidence(project_id):
        store.get_project(project_id)
        return evidence_store.list(project_id)

    def task_source_snapshot(task):
        if not task.get('sourceSnapshotId') or not task.get('sourceSnapshotDigest') or not task.get('baseStateRevision'):
            raise StoreError('Legacy run has no verified source snapshot; submit a new task',409)
        try:
            snap=evidence_store.load_snapshot(task['sourceSnapshotId'],task['sourceSnapshotDigest'])
        except OSError as exc:
            raise StoreError('Source snapshot is unavailable; submit a new task',409) from exc
        if snap['project']['id']!=task['projectId'] or snap['revision']!=task['baseRevision'] or snap['state_revision']!=task['baseStateRevision'] or snap['source_hashes']!=task.get('sourceManifest') or snap['main_file']!=task.get('main_file'):
            raise StoreError('Task and frozen source identity do not match',409)
        return snap

    def render_verification(record):
        value = record['result']
        try:
            build = compiler.inspect(value['build_id']) if value.get('build_id') else {'status':'unavailable','error':value.get('build_error')}
            if build.get('status') == 'success':
                for artifact in build.get('artifacts',[]):
                    compiler.artifact(build['id'],artifact['name'])
        except (ValueError,StoreError,OSError) as exc:
            build = {'status':'failed','error':str(exc)}
        result = assess_verification(value.get('structure') or {},build,value.get('tool_evidence',[]))
        keys = ('project_id','snapshot_id','state_revision','base_revision','source_hashes','main_file','engine','image_id')
        digest = hashlib.sha256(json.dumps({key:build.get(key) for key in keys},ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        matching_build = bool(record.get('state_revision')) and all(build.get(key)==record.get(key) for key in ('state_revision','snapshot_id','main_file','base_revision')) and build.get('engine')==value.get('engine') and build.get('source_hashes')==value.get('source_hashes') and build.get('identity_digest')==digest
        if not matching_build:
            result = {**result,'status':'partial' if value.get('verification_status')=='pending' else 'failed','reason':'No matching complete snapshot/configuration-bound build evidence','research_accepted':False}
        if value.get('verification_status')=='failed':
            result={**result,'status':'failed','reason':value.get('verification_error'),'research_accepted':False}
        current=store.get_project(record['project_id'])
        return {**record,'verification':result,'build':{key:build.get(key) for key in ('id','status','error','base_revision','state_revision','snapshot_id','main_file','engine','artifacts')},'current_revision_matches':current['revision']==record['base_revision'],'current_state_matches':bool(record.get('state_revision')) and current['state_revision']==record['state_revision']}

    def begin_verification(project_id,run_id=None,proposal_id=None,engine='pdflatex',expected_revision=None,expected_state_revision=None,snapshot=None):
        snapshot=snapshot or store.capture_project(project_id)
        if (expected_revision and snapshot['revision']!=expected_revision) or (expected_state_revision and snapshot['state_revision']!=expected_state_revision):
            raise HTTPException(409, 'Source changed before independent verification; revalidate the current revision')
        value={'run_id':run_id,'proposal_id':proposal_id,'structure':None,'build_id':None,'build_error':None,'tool_evidence':[],'human_review':'required','model_completion_is_acceptance':False,'engine':engine,'source_hashes':snapshot['source_hashes'],'verification_status':'pending'}
        record=save_evidence(project_id,'research_verification',value,snapshot)
        try:
            value['structure']=verify_project(snapshot['text_files'])
            save_evidence(project_id,'post_edit_structure',value['structure'],snapshot)
            if run_id:
                task=runtime.inspect(run_id)['task']; inputs=task.get('domainToolInputs',{})
                value['tool_evidence']=prepare_context(snapshot['text_files'],task['intent'],task['mode'],math_checks=inputs.get('math_checks',[]),reference_identifiers=inputs.get('reference_identifiers',[]))['tool_evidence']
            build=compiler.submit(project_id,snapshot['main_file'],engine,snapshot=snapshot)
            value.update(build_id=build['id'],verification_status='queued')
        except Exception as exc:
            value.update(verification_status='failed',verification_error=f'{type(exc).__name__}: {exc}',build_error=str(exc))
        record['result']=value
        evidence_store.write(record)
        return render_verification(record)

    @app.get("/api/health")
    def health():
        return {"status": "ok", "application": "lemfolio", "version": __version__}

    @app.get("/api/session",response_model=Session)
    def get_session(response: Response):
        response.set_cookie("lemfolio_session", session, httponly=True, samesite="strict", path="/")
        return {"csrf":csrf,"version":__version__,"contract_version":CONTRACT_VERSION}

    @app.get('/api/contract')
    def contract():
        return {'contract_version':CONTRACT_VERSION,'schema_sha256':CONTRACT_SCHEMA_SHA256,'schema':protocol_schema()}

    @app.get("/api/status")
    def status():
        return {"runtime": runtime.capabilities(), "compiler": compiler.status(), "keel": {"available": False, "reason": "Adapter contract reserved; no validated Keel transport configured"}, "data_root": str(root)}

    @app.get("/api/runtime/models",response_model=ModelCatalog)
    def runtime_models(refresh: bool = False):
        return runtime.model_catalog(refresh)

    @app.post("/api/runtime/harnesses",status_code=201,response_model=Harness)
    def add_harness(body: HarnessInput):
        return runtime.add_harness(body.name, body.command, body.models_command)

    @app.delete("/api/runtime/harnesses/{harness_id}")
    def remove_harness(harness_id: str):
        runtime.remove_harness(harness_id)
        return {"removed": True}

    @app.get("/api/workflows")
    def workflows():
        return WORKFLOWS

    @app.get("/api/projects",response_model=list[Project])
    def projects():
        return store.list_projects()

    @app.post("/api/projects",status_code=201,response_model=Project)
    def create_project(body: ProjectInput):
        return store.create_project(body.name, body.template)

    @app.post("/api/import",status_code=201,response_model=Project)
    async def import_project(request: Request, name: str = "Imported paper"):
        chunks = []
        size = 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > 64 * 1024 * 1024:
                raise HTTPException(413, "ZIP exceeds 64 MiB")
            chunks.append(chunk)
        return store.import_zip(name, b"".join(chunks))

    @app.post("/api/import/archive",status_code=201)
    async def import_archive(request: Request, name: str = "Imported paper"):
        chunks = []
        size = 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > 64 * 1024 * 1024:
                raise HTTPException(413, "ZIP exceeds 64 MiB")
            chunks.append(chunk)
        return store.import_archive(name, b"".join(chunks))

    @app.get("/api/projects/{pid}",response_model=Project)
    def project(pid: str):
        return store.get_project(pid)

    @app.get('/api/projects/{pid}/workflow',response_model=WorkflowView)
    def workflow_view(pid:str,run_id:str|None=None):
        project=store.get_project(pid)
        run=None
        if run_id:
            run=runtime.inspect(run_id)
            if run['task']['projectId']!=pid:
                raise StoreError('Run belongs to another project',404)
        else:
            recent=runtime.list_runs(pid)
            if recent:run=runtime.inspect(recent[0]['id'])
        records=project_evidence(pid)
        rendered=[]
        for record in records:
            if record['kind']=='research_verification':
                try:record=render_verification(record)
                except (ValueError,StoreError,OSError):
                    record={**record,'verification':{'status':'failed','research_accepted':False},'current_state_matches':False}
            rendered.append(record)
        builds=compiler.list_builds(pid)
        # Read only the candidates the projection can show, rather than hashing
        # every historic PDF on each refresh. Receipt identity is still checked
        # for the full bounded list; actual candidates additionally check bytes.
        candidates={builds[0]['id']} if builds else set()
        candidates.update(record['result'].get('build_id') for record in rendered if record['kind']=='research_verification' and (not run or record['result'].get('run_id')==run['id']))
        checked=[]
        for build in builds:
            valid=bool(build.get('state_revision')) and build.get('identity_digest')==compiler._identity(build)
            copy={**build,'verified_identity':valid}
            if build['id'] in candidates and build['status']=='success':
                try:
                    if not valid:raise ValueError('Unbound build receipt')
                    for artifact in build.get('artifacts',[]):compiler.artifact(build['id'],artifact['name'])
                    copy['artifacts_verified']=True
                except (ValueError,StoreError,OSError) as exc:
                    copy.update(status='failed',error=str(exc),artifacts_verified=False)
            checked.append(copy)
        return build_workflow(project,run,store.proposals(pid),rendered,checked)

    @app.get('/api/evidence/{identity}',response_model=VerificationRecord)
    def evidence_record(identity:str):
        record=evidence_store.read(identity)
        store.get_project(record['project_id'])
        return render_verification(record) if record['kind']=='research_verification' else record

    @app.get("/api/projects/{pid}/export")
    def export(pid: str):
        return StreamingResponse(io.BytesIO(store.export_zip(pid)), media_type="application/zip", headers={"Content-Disposition": 'attachment; filename="lemfolio-project.zip"'})

    @app.get("/api/projects/{pid}/files",response_model=list[PaperFile])
    def files(pid: str):
        return store.files(pid)

    @app.get("/api/projects/{pid}/file",response_model=TextFile)
    def read_file(pid: str, path: str):
        return store.read_file(pid, path)

    @app.put("/api/projects/{pid}/file",response_model=TextFile)
    def write_file(pid: str, body: FileInput):
        return store.write_file(pid, body.path, body.content, body.expected_hash)

    @app.delete("/api/projects/{pid}/file")
    def delete_file(pid: str, path: str, expected_hash: str):
        return store.delete_file(pid, path, expected_hash)

    @app.get("/api/projects/{pid}/asset")
    def asset(pid: str, path: str):
        data = store.read_bytes(pid, path)
        return Response(data, media_type="application/octet-stream", headers={"Content-Disposition": "attachment; filename*=UTF-8''" + quote(Path(path).name)})

    @app.put("/api/projects/{pid}/asset")
    async def upload_asset(pid: str, path: str, request: Request, expected_hash: str | None = None):
        return store.upload_asset(pid, path, await request.body(), expected_hash)

    @app.put("/api/projects/{pid}/main")
    def set_main(pid:str,path:str,base_revision:str,base_state_revision:str|None=None):
        return store.set_main_file(pid,path,base_revision,base_state_revision=base_state_revision)

    @app.post("/api/projects/{pid}/snapshot")
    def snapshot(pid: str, message: str = "Manual snapshot"):
        return store.snapshot(pid, message[:1000])

    @app.get("/api/projects/{pid}/history",response_model=list[HistoryEntry])
    def history(pid: str):
        return store.history(pid)

    @app.post("/api/projects/{pid}/restore")
    def restore(pid:str,revision:str,base_revision:str,base_state_revision:str|None=None):
        return store.restore(pid,revision,base_revision,base_state_revision=base_state_revision)

    @app.get("/api/projects/{pid}/index")
    def index(pid: str):
        snap=store.capture_project(pid)
        return {'base_revision':snap['revision'],'state_revision':snap['state_revision'],'main_file':snap['main_file'],'snapshot_id':snap['snapshot_id'],'index':index_project(snap['text_files'])}

    @app.post("/api/projects/{pid}/verify")
    def verify(pid: str):
        snap=store.capture_project(pid)
        return save_evidence(pid,'structure_and_citations',verify_project(snap['text_files']),snap)

    @app.post("/api/projects/{pid}/math")
    def check_math(pid: str, body: MathInput):
        snap=store.capture_project(pid)
        return save_evidence(pid,'symbolic_math',{'input':body.model_dump(),**math_check(body.lhs,body.rhs,body.assumptions)},snap)

    @app.get("/api/literature/lookup")
    def lookup(identifier: str):
        return lookup_reference(identifier)

    @app.get("/api/literature/search")
    def search(q: str, limit: int = 5):
        return search_literature(q, limit)

    @app.post("/api/projects/{pid}/reference")
    def verify_reference(pid: str, identifier: str):
        snap=store.capture_project(pid)
        return save_evidence(pid,'reference_metadata',lookup_reference(identifier),snap)

    @app.get("/api/projects/{pid}/evidence")
    def evidence(pid: str):
        return project_evidence(pid)

    @app.post("/api/projects/{pid}/proposals",status_code=201,response_model=Proposal)
    def propose(pid: str, body: ProposalInput):
        snap=store.capture_project(pid)
        if snap['revision']!=body.base_revision or (body.base_state_revision and snap['state_revision']!=body.base_state_revision):
            raise StoreError('Proposal source state is stale',409)
        changes=resolve_changes([change.model_dump(exclude_unset=True) for change in body.changes],snap)
        return store.propose(pid,changes,body.base_revision,body.summary,base_state_revision=snap['state_revision'])

    @app.get("/api/projects/{pid}/proposals",response_model=list[Proposal])
    def proposals(pid: str):
        return store.proposals(pid)

    @app.get('/api/projects/{pid}/proposals/{proposal_id}',response_model=Proposal)
    def proposal(pid:str,proposal_id:str):
        return store.get_proposal(pid,proposal_id)

    @app.get("/api/projects/{pid}/proposals/{proposal_id}/diff")
    def proposal_diff(pid: str, proposal_id: str):
        proposal = store.get_proposal(pid, proposal_id)
        return describe_diff(proposal["diff"])

    @app.get("/api/projects/{pid}/proposals/{proposal_id}/conflict",response_model=ProposalConflict)
    def proposal_conflict(pid: str, proposal_id: str):
        return store.proposal_conflict(pid, proposal_id)

    @app.post("/api/projects/{pid}/proposals/{proposal_id}/decide",response_model=DecisionResponse)
    def decide(pid: str, proposal_id: str, body: DecisionInput):
        if body.engine not in {'pdflatex', 'xelatex', 'lualatex'}:
            raise ValueError('Unsupported TeX engine')
        result = store.decide(pid, proposal_id, body.decision, body.digest)
        response={**result,'apply_status':'applied' if body.decision=='accept' else 'rejected','verification_status':'not_requested','independent_verification':None,'verification_error':None}
        if body.decision=='accept':
            try:
                records=project_evidence(pid)
                existing=next((r for r in records if r['kind']=='research_verification' and r['result'].get('proposal_id')==proposal_id),None)
                if existing:
                    verification=render_verification(existing)
                else:
                    source=next((r for r in records if r['kind']=='proposal_created' and r['result'].get('proposal_id')==proposal_id),None)
                    verification=begin_verification(pid,source['result']['run_id'] if source else None,proposal_id,body.engine,result.get('result_revision'),result.get('result_state_revision'))
                response.update(independent_verification=verification,verification_status=verification['result'].get('verification_status','pending'))
                if response['verification_status']=='failed':
                    response['verification_error']={'code':'VERIFICATION_FAILED','message':str(verification['result'].get('verification_error','Independent verification failed')),'retryable':True}
            except Exception as exc:
                # The Store transaction is already committed. Its applied state
                # is authoritative even if a verifier or evidence disk fails.
                response.update(verification_status='failed',verification_error={'code':'POST_APPLY_VERIFICATION_FAILED','message':f'{type(exc).__name__}: {exc}','retryable':True})
        return response

    @app.post("/api/projects/{pid}/runs",status_code=202,response_model=Run)
    def submit_run(pid: str, body: TaskInput):
        if body.mode not in WORKFLOWS:
            raise ValueError("Unknown research mode")
        if body.provider not in runtime.provider_ids():
            raise ValueError("Unknown runtime provider")
        snap=store.capture_project(pid);files=snap['text_files'];revision=snap['revision']
        prior=[r for r in project_evidence(pid) if r.get('state_revision')==snap['state_revision'] and r.get('main_file')==snap['main_file'] and r['kind'] in {'symbolic_math','reference_metadata','structure_and_citations','post_edit_structure'}][:10]
        builds=[r for r in compiler.list_builds(pid) if r.get('state_revision')==snap['state_revision'] and r.get('main_file')==snap['main_file'] and r.get('engine')==body.engine][:3]
        domain = prepare_context(files, body.intent, body.mode, math_checks=[item.model_dump() for item in body.math_checks], reference_identifiers=body.reference_identifiers, prior_evidence=prior, compile_receipts=builds)
        domain_id=uuid.uuid4().hex;context_id=uuid.uuid4().hex
        selection=body.selection.model_dump() if body.selection is not None else None
        context={'selection':select_context(files,body.intent,selection,source_hashes=snap['source_hashes']),'workflow':WORKFLOWS[body.mode],'verification':verify_project(files),'domain':domain,'domain_evidence_id':domain_id}
        task={'projectId':pid,'intent':body.intent,'mode':body.mode,'provider':body.provider,'model':body.model,'effort':body.effort,'baseRevision':revision,'baseStateRevision':snap['state_revision'],'main_file':snap['main_file'],'engine':body.engine,'sourceSnapshotId':snap['snapshot_id'],'sourceSnapshotDigest':'0'*64,'sourceManifest':snap['source_hashes'],'contextSnapshotId':context_id,'context':context,'domainToolInputs':{'math_checks':[item.model_dump() for item in body.math_checks],'reference_identifiers':body.reference_identifiers},'requiredCapabilities':['research.generate'],'verificationPlan':['human_diff_review','compile','references','mathematical_claims_require_review']}
        validate_task(task,providers=runtime.provider_ids())
        task['sourceSnapshotDigest']=evidence_store.save_snapshot(snap)
        save_evidence(pid,'domain_tool_evidence',domain,snap,identity=domain_id)
        save_evidence(pid,'context_snapshot',context,snap,identity=context_id)
        try:
            return runtime.submit(task)
        except ProviderError as exc:
            save_evidence(pid,'submission_rejected',{'context_snapshot_id':context_id,'code':exc.status_code,'message':str(exc),'task_accepted':False},snap)
            raise

    @app.get("/api/runs",response_model=list[RunSummary])
    def runs(project_id: str | None = None):
        return runtime.list_runs(project_id)

    @app.get("/api/runs/{run_id}",response_model=Run)
    def run(run_id: str):
        return runtime.inspect(run_id)

    @app.get("/api/runs/{run_id}/event-log",response_model=list[RuntimeEvent])
    def event_log(run_id: str, after: int = 0):
        return runtime.events(run_id, after)

    @app.post('/api/runs/{run_id}/verify', status_code=202)
    def verify_run(run_id: str, engine: str = 'pdflatex'):
        record = runtime.inspect(run_id)
        if record['status'] != 'completed':
            raise HTTPException(409, 'Only a completed model result can enter independent verification')
        if not record['task'].get('baseStateRevision') or not record['task'].get('sourceSnapshotId') or not record['task'].get('sourceSnapshotDigest'):
            raise StoreError('Legacy run has no bound project state; submit a new task',409)
        task_source_snapshot(record['task'])
        return begin_verification(record['task']['projectId'],run_id,engine=engine,expected_revision=record['task']['baseRevision'],expected_state_revision=record['task'].get('baseStateRevision'))

    @app.post('/api/verifications/{verification_id}/retry',status_code=202,response_model=VerificationRecord)
    def retry_verification(verification_id:str,engine:str|None=None):
        previous=evidence_store.read(verification_id)
        if previous.get('kind')!='research_verification' or not previous.get('state_revision'):
            raise StoreError('Only a state-bound research verification can be retried',409)
        snap=store.capture_project(previous['project_id'])
        if snap['state_revision']!=previous['state_revision'] or snap['revision']!=previous['base_revision'] or snap['main_file']!=previous['main_file'] or snap['source_hashes']!=previous['result'].get('source_hashes'):
            raise StoreError('Project changed after this verification; revalidate the current source',409)
        chosen=engine or previous['result']['engine']
        if chosen not in {'pdflatex','xelatex','lualatex'}:
            raise ValueError('Unsupported TeX engine')
        return begin_verification(previous['project_id'],previous['result'].get('run_id'),previous['result'].get('proposal_id'),chosen,previous['base_revision'],previous['state_revision'],snapshot=snap)

    @app.get('/api/verifications/{verification_id}',response_model=VerificationRecord)
    def verification(verification_id: str):
        if not re.fullmatch('[a-f0-9]{32}', verification_id):
            raise ValueError('Invalid verification ID')
        record=evidence_store.read(verification_id)
        if record['kind'] != 'research_verification':
            raise ValueError('This record is not a research verification')
        return render_verification(record)

    @app.post("/api/runs/{run_id}/interrupt")
    def interrupt(run_id: str):
        return runtime.interrupt(run_id)

    @app.post("/api/runs/{run_id}/recover")
    def recover(run_id: str):
        record = runtime.inspect(run_id)
        task = record["task"]
        current=store.get_project(task['projectId'])
        return {**runtime.recover(run_id),'base_revision_matches':current['revision']==task['baseRevision'],'base_state_matches':bool(task.get('baseStateRevision')) and current['state_revision']==task['baseStateRevision'],'current_revision':current['revision'],'next_action':'Inspect saved context and submit a new task with current files; old approvals are not reused.'}

    @app.post("/api/runs/{run_id}/proposal",status_code=201,response_model=Proposal)
    def run_proposal(run_id: str):
        record = runtime.inspect(run_id)
        result = record.get("result") or {}
        if record["status"] != "completed" or not result.get("changes"):
            raise ValueError("Run has no completed changes")
        task = record["task"]
        validate_result_for_task(result,task)
        snap=task_source_snapshot(task)
        changes=resolve_changes(result['changes'],snap,coverage=task['context']['selection']['coverage'])
        proposal=store.propose(task['projectId'],changes,task['baseRevision'],result.get('answer','AI proposal'),base_state_revision=task['baseStateRevision'])
        save_evidence(task['projectId'],'proposal_created',{'run_id':run_id,'proposal_id':proposal['id'],'proposal_digest':proposal['digest'],'result_digest':record['resultDigest'],'content_accepted':False},snap)
        return proposal

    @app.get("/api/runs/{run_id}/events")
    async def events(run_id: str, after: int = 0):
        runtime.inspect(run_id)
        async def stream():
            seq = after
            while True:
                items = runtime.events(run_id, seq)
                for item in items:
                    seq = item["seq"]
                    yield f"id: {seq}\ndata: {json.dumps(item, ensure_ascii=False)}\n\n"
                record = runtime.inspect(run_id)
                if record["status"] not in {"queued", "running"} and not runtime.events(run_id, seq):
                    yield "event: done\ndata: {}\n\n"
                    return
                yield ": heartbeat\n\n"
                await asyncio.sleep(.3)
        return StreamingResponse(stream(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})

    @app.post("/api/projects/{pid}/builds",status_code=202,response_model=Build)
    def compile_project(pid: str, body: CompileInput):
        return compiler.submit(pid,body.main_file,body.engine,snapshot=store.capture_project(pid))

    @app.get("/api/projects/{pid}/builds",response_model=list[Build])
    def builds(pid: str):
        store.get_project(pid)
        return compiler.list_builds(pid)

    @app.get("/api/builds/{build_id}",response_model=Build)
    def build(build_id: str):
        return compiler.inspect(build_id)

    @app.post("/api/builds/{build_id}/interrupt")
    def cancel_build(build_id: str):
        return compiler.interrupt(build_id)

    @app.get("/api/builds/{build_id}/artifacts/{name}")
    def artifact(build_id: str, name: str):
        return FileResponse(compiler.artifact(build_id, name), media_type="application/pdf" if name.endswith(".pdf") else "application/octet-stream")

    @app.get("/api/builds/{build_id}/synctex",response_model=SyncTexPosition)
    def synctex(build_id: str, source: str, line: int = 1, column: int = 1):
        return compiler.synctex(build_id, source, line, column)

    @app.get("/api/builds/{build_id}/synctex/inverse",response_model=SyncTexPosition)
    def synctex_inverse(build_id: str, page: int, x: float, y: float):
        return compiler.synctex_inverse(build_id, page, x, y)

    app.mount("/", StaticFiles(directory=str(web), html=True), name="workspace")
    app.add_middleware(BodyLimit)
    app.add_middleware(ContractHeaders)
    original_openapi=app.openapi
    def openapi():
        schema=original_openapi()
        schema['info']['x-lemfolio-contract']={'version':CONTRACT_VERSION,'schema_sha256':CONTRACT_SCHEMA_SHA256,'source':'contracts/v1/schema.json'}
        def references(value):
            if isinstance(value,dict):
                return {key:(item.replace('#/$defs/','#/components/schemas/') if key=='$ref' else references(item)) for key,item in value.items()}
            if isinstance(value,list):return [references(item) for item in value]
            return value
        # Retain canonical conditional constraints (for example explicit change
        # operations) that model-level validators cannot express automatically.
        schema.setdefault('components',{}).setdefault('schemas',{}).update({name:references(definition) for name,definition in protocol_schema()['$defs'].items()})
        return schema
    app.openapi=openapi
    return app
