// Exercise the actual bundled React UI in a real DOM implementation. Fixtures
// supply isolated HTTP/SSE boundaries; no model, Docker, or production data.
import assert from 'node:assert/strict';
import { pathToFileURL } from 'node:url';
import { resolve } from 'node:path';
import { spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { JSDOM } from 'jsdom';

const scenarios = [
  'reject-preserves-draft', 'restore-warns-about-unsaved-draft', 'project-switch-detaches-runtime',
  'async-save-keeps-later-keystrokes-dirty', 'stale-editor-cannot-bind-a-new-proposal',
  'new-build-clears-old-pdf', 'late-build-poll-cannot-replace-selection', 'entry-switch-invalidates-preview',
  'old-run-completion-cannot-reselect', 'protocol-version-mismatch-refused', 'missing-session-version-refused',
  'malformed-build-refused', 'malformed-project-refused', 'applied-proposal-verification-failure-is-explicit',
  'history-build-is-labeled', 'legacy-build-is-history', 'bad-event-is-refused',
  'applied-proposal-refresh-failure-is-explicit', 'response-header-mismatch-refused',
  'dropdown-run-selection-detaches-previous', 'dropdown-build-selection-clears-preview',
  'csp-no-eval-app-connects', 'restore-exact-snapshot-with-shared-content-revision',
  'verification-retry-does-not-reapply-source',
  'workflow-node-view-does-not-apply', 'workflow-approval-keeps-draft-guard', 'workflow-late-project-response-is-ignored',
  'workflow-late-run-response-is-ignored', 'workflow-historical-nodes-are-labeled', 'workflow-create-proposal-is-explicit',
  'workflow-invalid-response-is-refused', 'workflow-refreshes-after-save',
  'late-run-list-keeps-explicit-selection', 'late-run-list-cannot-replace-newer-list',
  'draft-survives-file-switch', 'stale-proposal-conflict-blocks-accept', 'synctex-forward-binds-current-build', 'synctex-late-response-after-new-build',
  'synctex-blocked-while-dirty', 'evidence-keeps-claims-separate', 'api-provider-is-never-auto-selected', 'editor-history-and-search-are-real',
];
const scenario = process.argv[2];
if (scenario === 'all') {
  for (const name of scenarios) {
    const flags = name === 'csp-no-eval-app-connects' ? ['--disallow-code-generation-from-strings'] : [];
    const result = spawnSync(process.execPath, [...flags, process.argv[1], name], {cwd: process.cwd(), encoding: 'utf8', timeout: 20000});
    process.stdout.write(result.stdout || ''); process.stderr.write(result.stderr || '');
    assert.equal(result.status, 0, `Web regression failed: ${name}`);
  }
  process.exit(0);
}
assert.ok(scenarios.includes(scenario), 'Known web regression scenario');
if (scenario === 'csp-no-eval-app-connects' && !process.execArgv.includes('--disallow-code-generation-from-strings')) {
  const result = spawnSync(process.execPath, ['--disallow-code-generation-from-strings', process.argv[1], scenario], {cwd: process.cwd(), encoding: 'utf8', timeout: 20000});
  process.stdout.write(result.stdout || ''); process.stderr.write(result.stderr || '');
  assert.equal(result.status, 0, 'entire app connects while string code generation is prohibited'); process.exit(0);
}
const dom = new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>', {url: 'http://127.0.0.1:18765/', pretendToBeVisual: true});
dom.window.localStorage.setItem('lemfolio.locale', 'zh');
for (const name of ['window', 'document', 'location', 'history', 'HTMLElement', 'HTMLInputElement', 'HTMLTextAreaElement', 'HTMLSelectElement', 'Event', 'MouseEvent', 'Node', 'Element', 'Window', 'Text', 'Range', 'DocumentFragment', 'MutationObserver', 'getComputedStyle', 'requestAnimationFrame', 'cancelAnimationFrame', 'localStorage']) Object.defineProperty(globalThis, name, {value: name === 'window' ? dom.window : typeof dom.window[name] === 'function' && !/^[A-Z]/.test(name) ? dom.window[name].bind(dom.window) : dom.window[name], configurable: true});
// jsdom performs no layout; CodeMirror only needs these to exist for its measure cycle.
dom.window.Range.prototype.getClientRects = () => []; dom.window.Range.prototype.getBoundingClientRect = () => ({left: 0, right: 0, top: 0, bottom: 0, width: 0, height: 0});
dom.window.Element.prototype.scrollIntoView = () => {};
globalThis.matchMedia = () => ({matches: false, addEventListener() {}, removeEventListener() {}});
Object.defineProperty(globalThis, 'navigator', {value: dom.window.navigator, configurable: true});
const get = id => document.getElementById(id);
const streams = [];
globalThis.EventSource = class {
  constructor(url) { this.url = url; this.closed = false; this.handlers = {}; streams.push(this); }
  addEventListener(name, callback) { this.handlers[name] = callback; }
  close() { this.closed = true; }
};
let confirm = () => true;
window.confirm = text => confirm(text); window.prompt = () => null; window.open = () => {};
const a = 'a'.repeat(32), b = 'b'.repeat(32), runId = 'c'.repeat(32), otherRunId = 'e'.repeat(32);
const buildA = '1'.repeat(32), buildB = '2'.repeat(32);
const projects = new Map([[a, {id: a, name: 'A', main_file: 'main.tex', revision: 'revision-a', state_revision: 'state-a', generation: 0}], [b, {id: b, name: 'B', main_file: 'main.tex', revision: 'revision-b', state_revision: 'state-b', generation: 0}]]);
const texts = new Map([[a, {path: 'main.tex', content: 'Disk A', sha256: 'hash-a'}], [b, {path: 'main.tex', content: 'Disk B', sha256: 'hash-b'}]]);
const proposal = {id: 'd'.repeat(32), project_id: a, base_revision: 'revision-a', base_state_revision: 'state-a', digest: 'proposal-digest', diff: 'proposed changes', status: 'pending', summary: 'Existing proposal', changes: [{path: 'main.tex', content: 'Proposed'}]};
const pythonCandidate = resolve('.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const python = process.env.LEMFOLIO_TEST_PYTHON || (existsSync(pythonCandidate) ? pythonCandidate : 'python');
// Build synthetic frozen ZIP bytes in memory so the task manifest and archive
// digest are complete and internally consistent, rather than placeholder proof.
const frozen = spawnSync(python, ['-B', '-c', `import hashlib,io,json,sys,zipfile
from lemfolio.harness import select_context
output={}
for identity,content in json.load(sys.stdin).items():
    data=content.encode('utf-8'); stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w',compression=zipfile.ZIP_STORED) as archive:
        archive.writestr(zipfile.ZipInfo('main.tex',date_time=(2026,1,1,0,0,0)),data)
    manifest={'main.tex':hashlib.sha256(data).hexdigest()}
    output[identity]={'manifest':manifest,'digest':hashlib.sha256(stream.getvalue()).hexdigest(),'revision':hashlib.sha256(json.dumps(manifest,sort_keys=True,separators=(',',':')).encode()).hexdigest(),'context':{'selection':select_context({'main.tex':content},'Read A',source_hashes=manifest)}}
print(json.dumps(output))`], {cwd: process.cwd(), encoding: 'utf8', input: JSON.stringify({[a]: 'Disk A', [b]: 'Disk B'}), timeout: 10000});
assert.equal(frozen.status, 0, frozen.stderr || 'synthetic source snapshots built in memory');
const frozenSources = JSON.parse(frozen.stdout);
const run = (id, pid = a) => ({id, status: 'completed', task: {projectId: pid, intent: 'Read A', baseRevision: frozenSources[pid].revision, baseStateRevision: pid === a ? 'state-a' : 'state-b', contextSnapshotId: '4'.repeat(32), sourceSnapshotId: (pid === a ? '6' : '7').repeat(32), sourceSnapshotDigest: frozenSources[pid].digest, sourceManifest: frozenSources[pid].manifest, main_file: 'main.tex', engine: 'pdflatex', context: frozenSources[pid].context, provider: 'codex', mode: scenario === 'workflow-create-proposal-is-explicit' ? 'edit' : 'chat'}, result: {answer: id === runId ? 'A result' : 'Other result', changes: scenario === 'workflow-create-proposal-is-explicit' ? [{path: 'main.tex', content: 'Suggested content'}] : [], checks: []}, error: null});
const summary = id => ({id, projectId: a, intent: 'Read A', mode: 'chat', provider: 'codex', status: 'completed', created_at: '2026-10-30T00:00:00Z', updated_at: '2026-10-30T00:00:00Z', error: null});
const makeBuild = (id, status = 'success', overrides = {}) => ({id, project_id: a, base_revision: 'revision-a', state_revision: 'state-a', snapshot_id: 'snapshot-' + id, main_file: 'main.tex', engine: 'pdflatex', identity_digest: 'identity-' + id, status, artifacts: status === 'success' ? [{name: 'main.pdf', size: 10, sha256: 'pdf-hash'}] : [], error: status === 'failed' ? 'TeX failed' : null, ...overrides});
const verificationId = 'f'.repeat(32);
const verification = {id: verificationId, project_id: a, kind: 'research_verification', base_revision: 'revision-a', state_revision: 'state-a', snapshot_id: 'snapshot', main_file: 'main.tex', engine: 'pdflatex', created_at: 1, result: {verification_status: 'failed', engine: 'pdflatex'}, verification: {status: 'failed'}, current_revision_matches: true, current_state_matches: true};
const contextEvidenceId = '4'.repeat(32);
let workflowProposalCreated = scenario !== 'workflow-create-proposal-is-explicit';
const makeWorkflow = (pid, selectedRun = pid === a ? runId : null) => {
  const historical = scenario === 'workflow-historical-nodes-are-labeled';
  const project = projects.get(pid), selected = selectedRun ? run(selectedRun, pid) : null;
  if (historical && selected) selected.task.baseStateRevision = 'historical-input-state';
  const stored = pid === a && workflowProposalCreated ? [{...proposal, main_file: 'main.tex', base_state_revision: selected?.task.baseStateRevision || project.state_revision, ...(historical ? {status: 'accepted', result_state_revision: 'historical-output-state'} : {})}] : [];
  const evidence = stored.length && selectedRun ? [{id: '5'.repeat(32), project_id: pid, kind: 'proposal_created', created_at: 1, result: {run_id: selectedRun, proposal_id: proposal.id}}] : [];
  // Use the real, pure projection implementation for HTTP fixture responses.
  // -B and JSON-only inputs keep this free of data writes, provider calls, or
  // a second executor; the browser still validates the shared wire schema.
  const projected = spawnSync(python, ['-B', '-c', `import json,sys
from lemfolio.workflow import build_workflow
from lemfolio.runtime import validate_task,validate_result_for_task
value=json.load(sys.stdin)
if value['run']:
    validate_task(value['run']['task'])
    validate_result_for_task(value['run']['result'],value['run']['task'])
print(json.dumps(build_workflow(**value)))`], {cwd: process.cwd(), encoding: 'utf8', input: JSON.stringify({project, run: selected, proposals: stored, evidence, builds: []}), timeout: 10000});
  assert.equal(projected.status, 0, projected.stderr || 'pure workflow projection completed');
  return JSON.parse(projected.stdout);
};
const buildRecords = new Map();
const buildScenarios = ['new-build-clears-old-pdf', 'late-build-poll-cannot-replace-selection', 'entry-switch-invalidates-preview', 'history-build-is-labeled', 'legacy-build-is-history', 'dropdown-build-selection-clears-preview', 'synctex-forward-binds-current-build', 'synctex-late-response-after-new-build', 'synctex-blocked-while-dirty', 'evidence-keeps-claims-separate'];
if (buildScenarios.includes(scenario)) buildRecords.set(buildA, makeBuild(buildA));
if (scenario === 'late-build-poll-cannot-replace-selection') { buildRecords.set(buildA, makeBuild(buildA, 'running')); buildRecords.set(buildB, makeBuild(buildB)); }
if (scenario === 'dropdown-build-selection-clears-preview') buildRecords.set(buildB, makeBuild(buildB));
if (scenario === 'history-build-is-labeled') buildRecords.set(buildA, makeBuild(buildA, 'success', {state_revision: 'old-state', main_file: 'old.tex'}));
if (scenario === 'legacy-build-is-history') buildRecords.set(buildA, makeBuild(buildA, 'success', {state_revision: null, snapshot_id: null, identity_digest: null, legacy_identity: true}));
let delaySynctex = false, resolveSynctex, delaySave = false, resolveSave, delayPoll = false, resolvePoll, buildARequests = 0, decisionApplied = false, delayWorkflow = false, resolveWorkflow, delayRuns = false, resolveRuns;
const requests = [];
const response = (value, status = 200) => new Response(JSON.stringify(value), {status, headers: {'Content-Type': 'application/json', 'X-Lemfolio-Contract-Version': '1.0.0'}});
globalThis.fetch = async (url, options = {}) => {
  const parsed = new URL(url, 'http://127.0.0.1:18765');
  const path = parsed.pathname, method = options.method || 'GET';
  requests.push({path, method, body: options.body, headers: options.headers, query: parsed.searchParams});
  if (path === '/api/session') {
    if (scenario === 'protocol-version-mismatch-refused') return response({csrf: 'csrf', version: '0.1.0', contract_version: '2.0.0'});
    if (scenario === 'missing-session-version-refused') return response({csrf: 'csrf', version: '0.1.0'});
    return response({csrf: 'csrf', version: '0.1.0', contract_version: '1.0.0'});
  }
  if (path === '/api/runtime/models') return response({providers: [
    {provider: 'codex', status: 'listed', source: 'codex debug models', models: [{id: 'model-a', name: 'Model A', description: 'First', efforts: ['low', 'high'], default_effort: 'low'}, {id: 'model-b', name: 'Model B', description: null, efforts: [], default_effort: null}]},
    {provider: 'claude', status: 'aliases', source: 'claude --help', models: [{id: 'opus', name: 'Opus', description: null, efforts: ['low', 'max'], default_effort: null}]},
    {provider: 'api', status: 'unavailable', source: 'LEMFOLIO_API_MODEL', models: []},
  ]});
  if (path === '/api/status') return response(scenario === 'api-provider-is-never-auto-selected'
    ? {runtime: {runtime: 'local', providers: {codex: {installed: true, authenticated: false, login_command: 'codex login'}, claude: {installed: false, authenticated: false, login_command: 'claude auth login'}, api: {installed: true, authenticated: true, configured: true}}}}
    : {status: 'fixture'});
  if (path === '/api/projects') {
    if (scenario === 'response-header-mismatch-refused') return new Response(JSON.stringify([...projects.values()]), {headers: {'X-Lemfolio-Contract-Version': '2.0.0'}});
    return response([...projects.values()]);
  }
  if (path === '/api/runs') {
    const listed = parsed.searchParams.get('project_id') === a ? [summary(runId), summary(otherRunId)] : [];
    if (delayRuns) { delayRuns = false; return new Promise(resolve => { resolveRuns = () => resolve(response(listed.map(item => ({...item, status: 'running'})))); }); }
    return response(listed);
  }
  if (path === `/api/verifications/${verificationId}/retry`) {
    verification.result.verification_status = 'queued'; verification.verification.status = 'partial';
    return response(verification);
  }
  if (path === `/api/verifications/${verificationId}`) return response(verification);
  if (path === `/api/evidence/${contextEvidenceId}`) return response({id: contextEvidenceId, project_id: a, kind: 'context_snapshot', base_revision: 'revision-a', state_revision: 'state-a', created_at: 1, result: {marker: 'Persisted context evidence'}});
  if (/^\/api\/runs\/[^/]+\/proposal$/.test(path)) { workflowProposalCreated = true; return response(proposal); }
  if (/^\/api\/runs\/[^/]+\/event-log$/.test(path)) return response([]);
  if (/^\/api\/runs\/[^/]+\/interrupt$/.test(path)) return response({...run(path.split('/')[3]), status: 'interrupted'});
  if (/^\/api\/runs\/[^/]+$/.test(path)) return response(run(path.split('/')[3]));
  if (/^\/api\/builds\/[^/]+\/synctex(?:\/inverse)?$/.test(path)) {
    const inverse = path.endsWith('/inverse');
    const position = {status: 'matched', boxes: inverse ? [] : [{page: 1, x: 72, y: 100, width: 300, height: 12}], file: 'main.tex', line: inverse ? 1 : Number(parsed.searchParams.get('line')), column: 1, build_id: path.split('/')[3], base_revision: 'revision-a', state_revision: 'state-a', snapshot_id: 'snapshot-' + path.split('/')[3], identity_digest: 'identity-' + path.split('/')[3]};
    if (delaySynctex) { delaySynctex = false; return new Promise(resolve => { resolveSynctex = () => resolve(response(position)); }); }
    return response(position);
  }
  if (/^\/api\/builds\/[^/]+$/.test(path)) {
    const id = path.split('/')[3];
    if (id === buildA && ++buildARequests > 1 && delayPoll) return new Promise(resolve => { resolvePoll = () => resolve(response(makeBuild(buildA))); });
    return response(buildRecords.get(id));
  }
  const parts = path.split('/'), pid = parts[3], suffix = parts.slice(4).join('/');
  if (!projects.has(pid)) throw new Error(`Unexpected fixture request ${method} ${path}`);
  if (!suffix) {
    if (scenario === 'applied-proposal-refresh-failure-is-explicit' && decisionApplied) return response({detail: 'Fixture follow-up GET unavailable'}, 503);
    if (scenario === 'malformed-project-refused') return response({...projects.get(pid), state_revision: 7});
    return response(projects.get(pid));
  }
  if (suffix === 'files') return response([{path: 'main.tex', size: texts.get(pid).content.length, sha256: texts.get(pid).sha256, text: true}, ...(scenario === 'draft-survives-file-switch' ? [{path: 'sections/intro.tex', size: 5, sha256: 'hash-intro', text: true}] : [])]);
  if (suffix === 'file' && method === 'GET') return response(parsed.searchParams.get('path') === 'sections/intro.tex' ? {path: 'sections/intro.tex', content: 'Intro', sha256: 'hash-intro'} : texts.get(pid));
  if (suffix === 'index') return response({base_revision: projects.get(pid).revision, state_revision: projects.get(pid).state_revision, main_file: 'main.tex', snapshot_id: 'snapshot', index: {schema_version: 1, nodes: [], labels: [], references: [], citations: [], bibliography: [], includes: [], edges: [], diagnostics: [], limitations: []}});
  if (suffix === `proposals/${proposal.id}/diff`) return response({status: 'unavailable', engine: 'keel-rust', reason: 'Fixture has no native binary'});
  if (suffix === 'file' && method === 'PUT') {
    const sent = JSON.parse(options.body), result = {path: sent.path, content: sent.content, sha256: 'new-hash'};
    if (delaySave) return new Promise(resolve => { resolveSave = () => { texts.set(pid, result); resolve(response(result)); }; });
    texts.set(pid, result);
    if (scenario === 'workflow-refreshes-after-save') { projects.get(pid).revision = 'saved-source-revision'; projects.get(pid).state_revision = 'saved-source-state'; projects.get(pid).generation++; }
    return response(result);
  }
  if (suffix === 'proposals') return response(workflowProposalCreated ? [proposal] : []);
  if (suffix === `proposals/${proposal.id}`) return response(proposal);
  if (suffix === 'workflow') {
    const workflow = makeWorkflow(pid, parsed.searchParams.get('run_id') || (pid === a ? runId : null));
    if (scenario === 'workflow-invalid-response-is-refused') workflow.nodes[2].status = 'completed';
    if (delayWorkflow && pid === a) { delayWorkflow = false; return new Promise(resolve => { resolveWorkflow = () => resolve(response(workflow)); }); }
    return response(workflow);
  }
  if (suffix === 'builds' && method === 'GET') return response(pid === a ? [...buildRecords.values()] : []);
  if (suffix === 'builds' && method === 'POST') {
    if (scenario === 'malformed-build-refused') return response({id: buildB, project_id: pid, status: 'success', artifacts: []});
    const build = makeBuild(buildB, 'failed'); buildRecords.set(buildB, build);
    return response(scenario === 'new-build-clears-old-pdf' ? makeBuild(buildB, 'queued') : build);
  }
  if (suffix === 'evidence' && scenario === 'workflow-create-proposal-is-explicit' && workflowProposalCreated) return response([{id: '5'.repeat(32), project_id: a, kind: 'proposal_created', base_revision: 'revision-a', state_revision: 'state-a', created_at: 1, result: {run_id: runId, proposal_id: proposal.id}}]);
  if (suffix === 'evidence') return response(scenario === 'verification-retry-does-not-reapply-source' ? [verification] : []);
  if (suffix === 'asset' && method === 'PUT') return response({path: parsed.searchParams.get('path'), sha256: 'asset-hash'});
  if (suffix.endsWith('/decide')) {
    const decision = JSON.parse(options.body).decision;
    proposal.status = decision === 'accept' ? 'accepted' : 'rejected';
    if (decision === 'accept') { decisionApplied = true; texts.set(pid, {path: 'main.tex', content: 'Proposed', sha256: 'proposed-hash'}); }
    return response({...proposal, apply_status: decision === 'accept' ? 'applied' : 'rejected', verification_status: decision === 'accept' ? 'failed' : 'not_requested', independent_verification: null, verification_error: decision === 'accept' ? {code: 'verification_start_failed', message: 'Fixture compiler unavailable', retryable: true} : null});
  }
  if (suffix === 'history') return response(scenario === 'restore-exact-snapshot-with-shared-content-revision'
    ? [{revision: 'shared-content-revision', commit: '1'.repeat(40), message: 'Older main.tex entry', main_file: 'main.tex'}, {revision: 'shared-content-revision', commit: '2'.repeat(40), message: 'Newer other.tex entry', main_file: 'other.tex'}]
    : [{revision: 'old-revision', commit: '1234567890abcdef', message: 'Saved'}]);
  if (suffix === 'restore') { texts.set(pid, {path: 'main.tex', content: 'Restored disk', sha256: 'restored'}); return response({revision: 'old-revision'}); }
  if (suffix === 'runs') return response(run(runId, pid));
  if (suffix === 'main') { projects.get(pid).main_file = parsed.searchParams.get('path'); projects.get(pid).state_revision = 'changed-entry-state'; projects.get(pid).generation++; return response(projects.get(pid)); }
  throw new Error(`Unexpected fixture request ${method} ${path}`);
};
if (scenario === 'csp-no-eval-app-connects') {
  assert.throws(() => new Function('return 1'), EvalError, 'the environment blocks Function constructor compilation');
  assert.throws(() => eval('1'), EvalError, 'the environment blocks eval');
}
await import(pathToFileURL(resolve('web/app.js')).href);
const workspaceSync = () => { get('synctex').click(); };
const tick = () => new Promise(resolve => setTimeout(resolve, 0));
async function until(predicate, message, timeout = 5000) { const start = Date.now(); while (!predicate() && Date.now() - start < timeout) await tick(); assert.ok(predicate(), message); }
async function settle() { await tick(); await until(() => get('message')?.textContent !== '处理中…', 'UI operation settled'); await tick(); }
async function emit(id, name = 'click') {
  const element = typeof id === 'string' ? get(id) : id;
  assert.ok(element, `Control exists: ${id}`);
  if (element.matches(':disabled')) return;
  if (name === 'click') { if (typeof element.click === 'function') element.click(); else element.dispatchEvent(new MouseEvent('click', {bubbles: true})); }
  else element.dispatchEvent(new Event(name, {bubbles: true}));
  await settle();
}
async function value(id, next) {
  const element = get(id), prototype = element.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : element.tagName === 'SELECT' ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(prototype, 'value').set.call(element, next);
  element.dispatchEvent(new Event(element.tagName === 'SELECT' ? 'change' : 'input', {bubbles: true})); await tick();
}
async function open(pid) { await value('projects', pid); await emit('open-project'); }
// The editor is a real CodeMirror view: read its document and apply edits as transactions.
const editorView = () => get('source').editorView;
const sourceText = () => editorView().state.doc.toString();
async function edit(next) { const view = editorView(); view.dispatch({changes: {from: 0, to: view.state.doc.length, insert: next}}); await tick(); }
await until(() => get('message') && get('message').textContent !== '' && get('message').textContent !== '处理中…', 'React boot settled');
if (['protocol-version-mismatch-refused', 'missing-session-version-refused'].includes(scenario)) {
  assert.match(get('message').textContent, /契约|版本/);
  assert.equal(requests.some(request => request.path === '/api/projects'), false, 'an incompatible session must stop before project access');
  assert.ok(get('open-project').matches(':disabled'));
  console.log('PASS', scenario); dom.window.close(); process.exit(0);
}
if (scenario === 'response-header-mismatch-refused') {
  assert.match(get('message').textContent, /接口契约版本已变化/);
  assert.ok(get('open-project').matches(':disabled'), 'a response version mismatch stops workspace startup');
  assert.equal(requests.some(request => request.path === '/api/status'), false);
  console.log('PASS', scenario); dom.window.close(); process.exit(0);
}
await open(a);
if (scenario === 'malformed-project-refused') {
  assert.match(get('message').textContent, /契约 Project 验证失败/);
  assert.ok(get('workspace').hidden, 'invalid core DTO cannot become active workspace state');
  console.log('PASS', scenario); dom.window.close(); process.exit(0);
}
assert.equal(sourceText(), 'Disk A', 'fixture opened A using the actual app');

switch (scenario) {
  case 'reject-preserves-draft': {
    await edit('Unsaved draft');
    const detail = get('proposals').children[0];
    const reject = [...detail.querySelectorAll('button')].find(child => child.textContent === '拒绝修改');
    assert.ok(reject, 'actual app created proposal controls');
    confirm = () => false;
    await emit(reject);
    assert.equal(sourceText(), 'Unsaved draft', 'rejecting a proposal must not discard an unrelated draft');
    break;
  }
  case 'restore-warns-about-unsaved-draft': {
    await edit('Unsaved draft'); await emit('history');
    confirm = text => !/未保存|放弃|草稿|尚未保存/.test(text);
    const restore = get('history-output').querySelector('button');
    await emit(restore);
    assert.equal(sourceText(), 'Unsaved draft', 'declining draft discard must preserve the editor through restore');
    assert.equal(requests.filter(r => r.path.endsWith('/restore')).length, 0, 'declining draft discard must prevent restore');
    break;
  }
  case 'project-switch-detaches-runtime': {
    await value('intent', 'Read A'); await emit('submit-run');
    assert.equal(streams.length, 1);
    await open(b);
    assert.ok(streams[0].closed, 'switching projects must detach the previous project event stream');
    await emit('interrupt-run');
    assert.equal(requests.filter(r => r.path.endsWith('/interrupt')).length, 0, 'the new project must not act on the old selected run');
    break;
  }
  case 'async-save-keeps-later-keystrokes-dirty': {
    await edit('First draft'); delaySave = true;
    const saving = emit('save-file');
    await until(() => resolveSave, 'save reached the delayed HTTP boundary');
    // :disabled includes the enclosing fieldset, just as native browser input does.
    const typingAllowed = !editorView().state.readOnly;
    if (typingAllowed) await edit('First draft plus later keystrokes');
    resolveSave(); await saving;
    if (typingAllowed) {
      confirm = () => false; await open(b);
      assert.equal(sourceText(), 'First draft plus later keystrokes', 'a completed earlier save cannot clear the dirty state of later typing');
      assert.equal(requests.filter(r => r.path === `/api/projects/${b}`).length, 0);
    }
    assert.equal(JSON.parse(requests.find(r => r.path.endsWith('/file') && r.method === 'PUT').body).expected_hash, 'hash-a', 'saving retains source compare-and-swap');
    break;
  }
  case 'stale-editor-cannot-bind-a-new-proposal': {
    await edit('Draft based on old disk contents');
    texts.set(a, {path: 'main.tex', content: 'Concurrent external edit', sha256: 'external-hash'});
    projects.get(a).revision = 'new-project-revision'; projects.get(a).state_revision = 'new-state';
    const asset = new Blob(['image']); asset.name = 'chart.png';
    Object.defineProperty(get('upload-asset'), 'files', {value: [asset], configurable: true}); window.prompt = () => 'figures/chart.png';
    await emit('upload-asset', 'change'); await emit('propose-edit');
    assert.equal(requests.filter(r => r.path.endsWith('/proposals') && r.method === 'POST').length, 0,
      'an unrelated refresh cannot rebase an old editor draft over a concurrent source edit');
    assert.equal(sourceText(), 'Draft based on old disk contents');
    break;
  }
  case 'new-build-clears-old-pdf': {
    assert.equal(get('pdf').hidden, false); assert.match(get('pdf').dataset.src, new RegExp(buildA));
    await emit('compile');
    assert.equal(get('pdf').hidden, true, 'failed new build must clear the previous successful preview');
    assert.equal(get('pdf').hasAttribute('data-src'), false, 'old PDF URL must be removed');
    assert.match(get('build-output').textContent, new RegExp(buildB));
    assert.match(get('builds').selectedOptions[0].textContent, /编译失败/, 'the selected history option reflects the terminal receipt rather than the queued submission');
    break;
  }
  case 'late-build-poll-cannot-replace-selection': {
    delayPoll = true;
    await until(() => resolvePoll, 'old running build reached delayed polling boundary');
    await value('builds', buildB); await emit('inspect-build');
    assert.match(get('pdf').dataset.src, new RegExp(buildB));
    resolvePoll(); await tick(); await tick();
    assert.match(get('pdf').dataset.src, new RegExp(buildB), 'late old polling response must not replace the explicitly selected build');
    assert.match(get('build-output').textContent, new RegExp(buildB));
    break;
  }
  case 'entry-switch-invalidates-preview': {
    assert.equal(get('pdf').hidden, false);
    await value('main-file', 'other.tex');
    assert.ok(get('pdf').hidden, 'changing entry immediately invalidates current preview');
    await emit('set-main');
    assert.ok(get('pdf').hidden); assert.match(get('build-context').textContent, /重新编译|历史/);
    const request = requests.find(r => r.path.endsWith('/main'));
    assert.equal(request.query.get('base_state_revision'), 'state-a', 'entry mutation binds the complete previous state');
    break;
  }
  case 'old-run-completion-cannot-reselect': {
    await value('intent', 'Read A'); await emit('submit-run');
    await value('runs', otherRunId); await emit('inspect-run');
    assert.ok(streams[0].closed, 'manual task selection detaches the previous stream');
    streams[0].handlers.done(); await tick(); await tick();
    assert.match(get('run-output').textContent, /Other result/);
    await emit('interrupt-run');
    assert.equal(requests.filter(r => r.path.endsWith('/interrupt')).at(-1).path, `/api/runs/${otherRunId}/interrupt`, 'follow-up actions keep the selected run');
    break;
  }
  case 'late-run-list-keeps-explicit-selection': {
    // R1: the completed old run asks for the run list; the response returns only
    // after the user chose another run, while no run record is loaded yet.
    await value('intent', 'Read A'); await emit('submit-run');
    delayRuns = true; streams[0].handlers.done();
    await until(() => resolveRuns, 'old run completion reached the delayed run-list boundary');
    await value('runs', otherRunId);
    assert.equal(get('runs').value, otherRunId);
    const logsBefore = requests.filter(r => r.path === `/api/runs/${runId}/event-log`).length;
    resolveRuns(); await tick(); await tick();
    assert.equal(get('runs').value, otherRunId, 'a late list response cannot overwrite the latest explicit selection');
    await until(() => get('workflow-context')?.textContent.includes(otherRunId.slice(0, 8)), 'workflow follows the explicit selection');
    assert.equal(requests.filter(r => r.path === `/api/runs/${runId}/event-log`).length, logsBefore, 'the deselected run stops loading');
    await emit('inspect-run');
    assert.match(get('run-output').textContent, /Other result/, 'inspection targets the explicitly selected run');
    assert.equal(get('runs').value, otherRunId);
    await emit('interrupt-run');
    assert.equal(requests.filter(r => r.path.endsWith('/interrupt')).at(-1).path, `/api/runs/${otherRunId}/interrupt`, 'follow-up actions keep the explicit selection');
    break;
  }
  case 'late-run-list-cannot-replace-newer-list': {
    await value('intent', 'Read A'); await emit('submit-run');
    delayRuns = true; streams[0].handlers.done();
    await until(() => resolveRuns, 'first run list reached the delayed boundary');
    await value('runs', otherRunId); await emit('inspect-run');
    const summaryText = () => [...get('runs').options].map(option => option.textContent).join('|');
    assert.doesNotMatch(summaryText(), /运行中/, 'the newer list is rendered');
    resolveRuns(); await tick(); await tick();
    assert.doesNotMatch(summaryText(), /运行中/, 'an older list response cannot restore stale run statuses');
    assert.equal(get('runs').value, otherRunId);
    break;
  }
  case 'draft-survives-file-switch': {
    await edit('Unsaved main draft');
    confirm = () => { throw new Error('switching files must not prompt or discard'); };
    const row = name => [...document.querySelectorAll('.tree-file')].find(item => item.textContent.includes(name));
    await emit(row('intro.tex'));
    assert.equal(sourceText(), 'Intro', 'the other file opens from disk');
    assert.match(row('main.tex').innerHTML, /dirty-dot/, 'the unsaved file stays marked in the tree');
    await emit('compile');
    assert.equal(requests.filter(r => r.path.endsWith('/builds') && r.method === 'POST').length, 0, 'compile refuses while any file has a draft');
    await emit(row('main.tex'));
    assert.equal(sourceText(), 'Unsaved main draft', 'returning restores the draft');
    assert.equal(requests.filter(r => r.path.endsWith('/file') && r.method === 'PUT').length, 0, 'switching files never writes to disk');
    await emit('save-file');
    assert.equal(JSON.parse(requests.find(r => r.path.endsWith('/file') && r.method === 'PUT').body).expected_hash, 'hash-a', 'a restored draft keeps its original compare-and-swap hash');
    assert.doesNotMatch(row('main.tex').innerHTML, /dirty-dot/);
    break;
  }
  case 'stale-proposal-conflict-blocks-accept': {
    projects.get(a).revision = 'newer-revision'; projects.get(a).state_revision = 'newer-state';
    const asset = new Blob(['image']); asset.name = 'chart.png';
    Object.defineProperty(get('upload-asset'), 'files', {value: [asset], configurable: true}); window.prompt = () => 'figures/chart.png';
    await emit('upload-asset', 'change');
    await until(() => get('proposals').textContent.includes('已过期'), 'stale proposal shows its conflict');
    assert.match(get('proposals').querySelector('[role="alert"]').textContent, /不能应用/);
    const buttons = [...get('proposals').querySelectorAll('button')];
    const accept = buttons.find(button => button.textContent === '接受此摘要绑定的修改');
    assert.ok(accept.matches(':disabled'), 'a stale proposal cannot be accepted');
    accept.click(); await tick();
    assert.equal(requests.filter(r => r.path.endsWith('/decide')).length, 0);
    assert.ok(!buttons.find(button => button.textContent === '拒绝修改').matches(':disabled'), 'a stale proposal can still be rejected');
    break;
  }
  case 'synctex-forward-binds-current-build': {
    assert.equal(get('pdf').hidden, false);
    await emit('synctex');
    const query = requests.find(r => r.path === `/api/builds/${buildA}/synctex`);
    assert.equal(query.query.get('source'), 'main.tex'); assert.equal(query.query.get('line'), '1');
    await until(() => /PDF 第 1 页/.test(get('pdf-identity').textContent), 'result reaches the PDF identity line');
    break;
  }
  case 'synctex-late-response-after-new-build': {
    delaySynctex = true; const syncing = emit('synctex');
    await until(() => resolveSynctex, 'query reached delayed boundary');
    resolveSynctex(); await syncing;
    await until(() => /PDF 第 1 页/.test(get('pdf-identity').textContent), 'baseline: response reaches current build');
    delaySynctex = true; void workspaceSync();
    await until(() => resolveSynctex && delaySynctex === false, 'second query reached delayed boundary');
    await emit('compile');
    assert.ok(get('pdf').hidden, 'failed build cleared the preview');
    resolveSynctex(); await tick(); await tick();
    assert.equal(get('pdf-identity'), null, 'a late response from the old build cannot restore a target');
    assert.doesNotMatch(get('message').textContent, /定位/);
    break;
  }
  case 'synctex-blocked-while-dirty': {
    assert.ok(!get('synctex').matches(':disabled'));
    await edit('Shifted lines');
    assert.ok(get('synctex').matches(':disabled'), 'line numbers of unsaved text do not match the PDF');
    assert.equal(requests.filter(r => r.path.includes('/synctex')).length, 0);
    assert.match(get('pdf-identity').textContent, /未保存/);
    break;
  }
  case 'evidence-keeps-claims-separate': {
    const status = id => get(`claim-${id}`).getAttribute('data-status');
    assert.equal(status('compile'), 'verified', 'the bound successful build counts as a compile');
    assert.equal(status('math'), 'unchecked', 'a successful compile must not imply a mathematical check');
    assert.equal(status('literature'), 'unchecked');
    assert.equal(status('human'), 'human', 'human review can never become verified automatically');
    assert.match(get('claim-compile').textContent, /不是数学证明/);
    break;
  }
  case 'api-provider-is-never-auto-selected': {
    const radio = value => document.querySelector(`input[name="provider"][value="${value}"]`);
    assert.equal(radio('codex').checked, true, 'a configured API key does not replace the selected CLI');
    assert.equal(radio('api').checked, false);
    assert.match(document.querySelector('.provider-list').textContent, /未通过订阅登录/);
    assert.match(document.querySelector('.provider-list').textContent, /需手动选择/);
    await value('intent', 'Read A'); await emit('submit-run');
    assert.equal(JSON.parse(requests.find(r => r.path.endsWith('/runs') && r.method === 'POST').body).provider, 'codex');
    break;
  }
  case 'editor-history-and-search-are-real': {
    await edit('Changed once');
    assert.equal(get('save-state').textContent, '未保存');
    await emit('undo');
    assert.equal(sourceText(), 'Disk A', 'undo restores the previous document');
    assert.equal(get('save-state').textContent, '已保存', 'undoing back to the saved text clears the dirty state');
    await emit('redo');
    assert.equal(sourceText(), 'Changed once');
    await emit('find');
    assert.ok(get('source').querySelector('.cm-search input[name="search"]'), 'the search panel opens');
    assert.ok(get('source').querySelector('.cm-search input[name="replace"]'), 'replace is available');
    break;
  }
  case 'dropdown-run-selection-detaches-previous': {
    await value('intent', 'Read A'); await emit('submit-run');
    await value('runs', otherRunId);
    assert.ok(streams[0].closed, 'changing the task dropdown invalidates the previous selected task immediately');
    streams[0].handlers.done(); await tick();
    await emit('interrupt-run');
    assert.equal(requests.filter(r => r.path.endsWith('/interrupt')).length, 0, 'a newly selected but unread task cannot act on the old task');
    await emit('inspect-run'); assert.match(get('run-output').textContent, /Other result/);
    break;
  }
  case 'dropdown-build-selection-clears-preview': {
    assert.equal(get('pdf').hidden, false);
    await value('builds', buildB);
    assert.ok(get('pdf').hidden, 'a newly selected build cannot display the previous build PDF');
    assert.equal(get('pdf').hasAttribute('data-src'), false);
    await emit('inspect-build'); assert.match(get('pdf').dataset.src, new RegExp(buildB));
    break;
  }
  case 'csp-no-eval-app-connects': {
    assert.equal(sourceText(), 'Disk A', 'the complete bundled React app establishes its validated session and opens a project without eval');
    assert.ok(requests.some(request => request.path === '/api/session'));
    assert.equal(get('workspace').hidden, false);
    break;
  }
  case 'restore-exact-snapshot-with-shared-content-revision': {
    await emit('history');
    const entries = get('history-output').querySelectorAll('p');
    assert.equal(entries.length, 2, 'fixture has two snapshots sharing source bytes but different entry configurations');
    await emit(entries[0].querySelector('button'));
    const restore = requests.find(request => request.path.endsWith('/restore'));
    assert.equal(restore.query.get('revision'), '1'.repeat(40), 'restore identifies the clicked snapshot by unique commit, not ambiguous content revision');
    assert.equal(restore.query.get('base_state_revision'), 'state-a', 'restore still binds the current project state');
    assert.equal(sourceText(), 'Restored disk');
    break;
  }
  case 'verification-retry-does-not-reapply-source': {
    const sourceBefore = sourceText();
    const retry = [...get('verifications').querySelectorAll('button')].find(button => button.textContent === '重试独立验证');
    assert.ok(retry && !retry.matches(':disabled'), 'a failed verification for the current approved state can be retried');
    await emit(retry);
    assert.equal(requests.filter(request => request.path === `/api/verifications/${verificationId}/retry` && request.method === 'POST').length, 1);
    assert.equal(requests.filter(request => request.path.endsWith('/decide')).length, 0, 'verification retry never reapplies a proposal');
    assert.equal(requests.filter(request => request.path.endsWith('/file') && request.method !== 'GET').length, 0, 'verification retry never writes source');
    assert.equal(sourceText(), sourceBefore);
    assert.match(get('message').textContent, /独立验证已重试/);
    assert.equal(get('verifications').querySelector('button').matches(':disabled'), true, 'queued verification cannot be retried again from this control');
    break;
  }
  case 'workflow-node-view-does-not-apply': {
    for (const id of ['source', 'context', 'tools', 'model', 'proposal', 'approval', 'compile', 'verification']) await emit(`workflow-node-${id}`);
    assert.equal(requests.filter(request => request.method !== 'GET').length, 0, 'node inspection alone never applies source or starts work');
    assert.equal(get('workflow-node-tools').getAttribute('data-status'), 'blocked', 'viewing a model result cannot fabricate independent tool evidence');
    await emit('workflow-node-tools');
    const blocked = get('workflow-actions').querySelector('button');
    assert.ok(blocked.matches(':disabled')); assert.match(get('workflow-actions').textContent, /尚无.*工具/);
    blocked.dispatchEvent(new MouseEvent('click', {bubbles: true})); await tick();
    assert.equal(requests.filter(request => request.method !== 'GET').length, 0, 'a disabled action cannot start work');
    await emit('workflow-node-context');
    await emit(get('workflow-evidence-refs').querySelector('[data-evidence-kind="context_snapshot"]'));
    assert.match(get('workflow-evidence-output').textContent, /Persisted context evidence/);
    assert.equal(get('workflow-node-tools').getAttribute('data-status'), 'blocked');
    assert.equal(requests.some(request => request.path.endsWith('/' + run(runId).task.sourceSnapshotId)), false, 'source archive bindings are metadata rather than broken download actions');
    break;
  }
  case 'workflow-approval-keeps-draft-guard': {
    await edit('Unsaved workflow draft'); await emit('workflow-node-approval');
    assert.match(get('workflow-approval').textContent, /proposed changes/);
    assert.match(get('workflow-approval').textContent, /人工审批点/);
    assert.equal(requests.filter(request => request.path.endsWith('/decide')).length, 0, 'expanding approval never applies the proposal');
    await emit(get('workflow-actions').querySelector('[data-action="review_proposal"]'));
    assert.equal(requests.filter(request => request.path.endsWith('/decide')).length, 0, 'review action only reveals the exact proposal');
    confirm = () => false;
    await emit([...get('workflow-approval').querySelectorAll('button')].find(button => button.textContent === '接受此摘要绑定的修改'));
    assert.equal(requests.filter(request => request.path.endsWith('/decide')).length, 0, 'workflow approval keeps the original dirty-draft confirmation');
    assert.equal(sourceText(), 'Unsaved workflow draft');
    break;
  }
  case 'workflow-late-project-response-is-ignored': {
    delayWorkflow = true; await emit('refresh-status');
    await until(() => resolveWorkflow, 'old project workflow reached delayed boundary');
    await open(b); await until(() => get('workflow-context')?.textContent.includes(b.slice(0, 8)), 'new project workflow loaded');
    resolveWorkflow(); await tick(); await tick();
    assert.match(get('workflow-context').textContent, new RegExp(b.slice(0, 8)), 'late old project workflow cannot replace the new graph');
    await emit('workflow-node-source'); assert.match(get('workflow-node-details').textContent, /state-b/);
    break;
  }
  case 'workflow-late-run-response-is-ignored': {
    delayWorkflow = true; await emit('refresh-status');
    await until(() => resolveWorkflow, 'old run workflow reached delayed boundary');
    await value('runs', otherRunId); await until(() => get('workflow-context')?.textContent.includes(otherRunId.slice(0, 8)), 'new selected run workflow loaded');
    resolveWorkflow(); await tick(); await tick();
    assert.match(get('workflow-context').textContent, new RegExp(otherRunId.slice(0, 8)));
    await emit('workflow-node-model'); await emit(get('workflow-actions').querySelector('[data-action="inspect_run"]'));
    assert.match(get('run-output').textContent, /Other result/, 'workflow action keeps the new run target after a stale response');
    break;
  }
  case 'workflow-historical-nodes-are-labeled': {
    assert.equal(get('workflow-node-context').getAttribute('data-historical'), 'true');
    assert.match(get('workflow-node-context').getAttribute('aria-label'), /历史.*已通过/);
    assert.match(get('workflow-identity').textContent, /historical-input-state/); assert.match(get('workflow-identity').textContent, /historical-output-state/); assert.match(get('workflow-identity').textContent, /state-a/);
    await emit('workflow-node-context'); assert.match(get('workflow-detail').querySelector('h3').textContent, /历史/);
    await emit('workflow-node-approval');
    const accept = [...get('workflow-approval').querySelectorAll('button')].find(button => button.textContent === '接受此摘要绑定的修改');
    assert.ok(accept.matches(':disabled'), 'historical workflow review cannot bypass the disabled approval action through inline controls');
    break;
  }
  case 'workflow-create-proposal-is-explicit': {
    await emit('workflow-node-proposal');
    assert.equal(requests.filter(request => request.path.endsWith('/proposal') && request.method === 'POST').length, 0);
    await emit(get('workflow-actions').querySelector('[data-action="create_proposal"]'));
    assert.equal(requests.filter(request => request.path === `/api/runs/${runId}/proposal` && request.method === 'POST').length, 1, 'explicit create action makes exactly one pending proposal');
    assert.equal(requests.filter(request => request.path.endsWith('/decide')).length, 0, 'creating a proposal never accepts it');
    assert.equal(requests.filter(request => request.path.endsWith('/file') && request.method !== 'GET').length, 0);
    assert.equal(sourceText(), 'Disk A');
    await until(() => get('workflow-node-proposal')?.getAttribute('data-status') === 'passed' && get('workflow-node-approval')?.getAttribute('data-status') === 'pending_review', 'saved workflow shows proposal creation separately from pending human approval');
    await emit('view-review');
    await until(() => get('proposals').textContent.includes(`科研任务 ${runId.slice(0, 8)}`), 'a proposal made from a run names that run as soon as it exists');
    break;
  }
  case 'workflow-invalid-response-is-refused': {
    assert.match(get('workflow-notice').textContent, /WorkflowView.*验证失败/);
    assert.equal(get('workflow-graph'), null, 'an invalid server projection cannot render trusted status nodes');
    break;
  }
  case 'workflow-refreshes-after-save': {
    const prior = requests.filter(request => request.path.endsWith('/workflow')).length;
    await edit('Saved workflow update'); await emit('save-file');
    await until(() => get('workflow-identity')?.textContent.includes('saved-source-state'), 'workflow refresh follows a saved source operation');
    assert.ok(requests.filter(request => request.path.endsWith('/workflow')).length > prior);
    break;
  }
  case 'malformed-build-refused': {
    await emit('compile'); assert.match(get('message').textContent, /契约 Build 验证失败/);
    assert.ok(get('pdf').hidden, 'unvalidated DTO cannot become a preview'); break;
  }
  case 'applied-proposal-verification-failure-is-explicit': {
    const accept = [...get('proposals').querySelectorAll('button')].find(button => button.textContent === '接受此摘要绑定的修改');
    await emit(accept);
    assert.equal(sourceText(), 'Proposed', 'committed source is reloaded even if post-commit verification failed');
    assert.match(get('message').textContent, /修改已应用.*独立验证.*失败/);
    assert.equal(requests.filter(r => r.path.endsWith('/decide')).length, 1, 'UI must not retry an already applied decision'); break;
  }
  case 'applied-proposal-refresh-failure-is-explicit': {
    const accept = [...get('proposals').querySelectorAll('button')].find(button => button.textContent === '接受此摘要绑定的修改');
    await emit(accept);
    assert.equal(texts.get(a).content, 'Proposed', 'fixture committed the source before the follow-up GET failed');
    assert.match(get('message').textContent, /修改已应用.*刷新失败.*请勿重复应用/);
    assert.equal(requests.filter(r => r.path.endsWith('/decide')).length, 1);
    break;
  }
  case 'history-build-is-labeled': case 'legacy-build-is-history': {
    assert.ok(get('pdf').hidden, 'old or unbound receipts are not selected as current evidence');
    await value('builds', buildA); await emit('inspect-build');
    assert.equal(get('pdf').hidden, false, 'explicit historical inspection retains PDF access');
    assert.match(get('build-context').textContent, /历史编译（不作为当前证据）/); break;
  }
  case 'bad-event-is-refused': {
    await value('intent', 'Read A'); await emit('submit-run');
    streams[0].onmessage({data: JSON.stringify({seq: 1, type: 'bad', at: 'now', data: 'wrong-type'})}); await tick();
    assert.match(get('message').textContent, /RuntimeEvent.*验证失败/);
    assert.ok(streams[0].closed); assert.equal(get('events').textContent, ''); break;
  }
  default: throw new Error('Unknown web regression scenario');
}
console.log('PASS', scenario);
dom.window.close(); process.exit(0);
