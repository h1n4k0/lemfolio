import { t } from './i18n.js';
import { LemfolioClient } from './client.js';
import type { ModelCatalog } from './client.js';
import type { ProposalConflict, Build, HistoryEntry, PaperFile, Project, Proposal, Run, RunSummary, SyncTexBox, SyncTexPosition, TextFile, VerificationRecord, WorkflowAction, WorkflowEvidenceRef, WorkflowView } from './client.js';

const preferredProvider = (status: unknown, current: string) => {
  // Only a logged-in official CLI may replace the default; the API fallback stays an explicit choice.
  const providers = (status as {runtime?: {providers?: Record<string, {authenticated?: boolean}>}} | null)?.runtime?.providers;
  return providers?.[current]?.authenticated ? current : ['codex', 'claude'].find(name => providers?.[name]?.authenticated) || current;
};
export const display = (value: unknown) => value === undefined || value === null ? '' : typeof value === 'string' ? value : JSON.stringify(value, null, 2);
interface Selection { path: string; start_line: number; end_line: number }
export interface SourceJump { path: string; line: number; nonce: number }
export interface PdfTarget { buildId: string; boxes: SyncTexBox[]; nonce: number }
export type ResearchKind = 'index' | 'verify' | 'math' | 'reference' | 'search' | 'evidence' | '';
export interface FormValues {
  projectId: string; projectName: string; filePath: string; newPath: string; mainFile: string; engine: string; synctexLine: string;
  mathLhs: string; mathRhs: string; mathAssumptions: string; literatureQuery: string;
  mode: string; provider: string; model: string; intent: string; runId: string; buildId: string; snapshotMessage: string;
}
export interface WorkspaceState {
  busy: boolean; ready: boolean; message: string; status: unknown; modelCatalog: ModelCatalog | null; projects: Project[]; project?: Project; files: PaperFile[];
  file?: TextFile; source: string; dirty: boolean; selection: Selection | null;
  runs: RunSummary[]; run?: Run; runOutput: unknown; events: unknown[];
  builds: Build[]; build?: Build; buildNotice: string; buildHistorical: boolean;
  proposals: Proposal[]; proposalDiffs: Record<string, unknown>; proposalConflicts: Record<string, ProposalConflict>; verifications: VerificationRecord[];
  research: unknown; history: HistoryEntry[]; historyOutput: unknown; form: FormValues;
  workflow?: WorkflowView; workflowNotice: string; workflowNodeId: string; workflowEvidence: unknown; reviewProposalId: string;
  saving: boolean; draftPaths: string[]; index: unknown; indexNotice: string; evidenceRecords: VerificationRecord[]; researchKind: ResearchKind;
  jump: SourceJump | null; pdfTarget: PdfTarget | null; synctexBusy: boolean; synctexNotice: string;
}
const initial = (): WorkspaceState => ({
  busy: false, ready: false, message: '', status: null, modelCatalog: null, projects: [], files: [], source: '', dirty: false, selection: null,
  runs: [], runOutput: null, events: [], builds: [], buildNotice: '', buildHistorical: false,
  proposals: [], proposalDiffs: {}, proposalConflicts: {}, verifications: [], research: null, history: [], historyOutput: null,
  workflowNotice: '', workflowNodeId: 'source', workflowEvidence: null, reviewProposalId: '',
  saving: false, draftPaths: [], index: null, indexNotice: '', evidenceRecords: [], researchKind: '',
  jump: null, pdfTarget: null, synctexBusy: false, synctexNotice: '',
  form: {projectId: '', projectName: '', filePath: '', newPath: '', mainFile: 'main.tex', engine: 'pdflatex', synctexLine: '1', mathLhs: '', mathRhs: '', mathAssumptions: '', literatureQuery: '', mode: 'chat', provider: 'codex', model: '', intent: '', runId: '', buildId: '', snapshotMessage: ''},
});

/** Observable application state. React owns all rendered controls; tokens bind
 * asynchronous results to the exact project and explicitly selected operation. */
export class Workspace {
  private state = initial();
  private listeners = new Set<() => void>();
  private projectToken = 0;
  private fileToken = 0;
  private runToken = 0;
  private runListToken = 0;
  private buildToken = 0;
  private workflowToken = 0;
  private indexToken = 0;
  private synctexToken = 0;
  private jumpNonce = 0;
  private editorVersion = 0;
  // Unsaved buffers of files other than the visible one, each with the hash it was read at.
  private drafts = new Map<string, {file: TextFile; source: string}>();
  private stream?: EventSource;
  private poll?: ReturnType<typeof setTimeout>;
  private started = false;
  private disposed = false;
  constructor(readonly client = new LemfolioClient()) {}
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => this.listeners.delete(listener); };
  getSnapshot = () => this.state;
  private update(value: Partial<WorkspaceState>) {
    if (this.disposed) return;
    this.state = {...this.state, ...value}; this.listeners.forEach(listener => listener());
  }
  form<K extends keyof FormValues>(key: K, value: FormValues[K]) {
    if (['mainFile', 'engine'].includes(key) && value !== this.state.form[key]) this.clearBuild(t('入口或引擎已变化，请重新编译；历史记录仍可查看'));
    if (key === 'buildId' && value !== this.state.form.buildId) this.clearBuild(t('已选择其他编译，请点击查看编译记录'));
    const changedRun = key === 'runId' && value !== this.state.form.runId;
    if (changedRun) { this.stopRun(); this.clearWorkflow(); this.update({run: undefined, runOutput: null, events: []}); }
    this.update({form: {...this.state.form, [key]: value}});
    if (changedRun) this.refreshWorkflowInBackground();
  }
  edit(source: string) { this.editorVersion++; this.update({source, dirty: !this.state.file?.sha256 || source !== this.state.file.content, selection: null}); }
  select(start: number, end: number) {
    const {file, source} = this.state;
    this.update({selection: !file || start === end ? null : {path: file.path, start_line: source.slice(0, start).split('\n').length, end_line: source.slice(0, end).split('\n').length}});
  }
  private required() { if (!this.state.project) throw new Error(t('请先打开项目')); return this.state.project; }
  private path(suffix: string, pid = this.required().id) { return `/api/projects/${pid}/${suffix}`; }
  private current(scope: number) { return !this.disposed && scope === this.projectToken; }
  private anyDirty() { return this.state.dirty || this.drafts.size > 0; }
  private mayDiscard() { return !this.anyDirty() || window.confirm(t('当前编辑尚未保存，是否放弃这些编辑？')); }
  private syncDrafts() { this.update({draftPaths: [...this.drafts.keys()]}); }
  private stashDraft() {
    const {file, source, dirty} = this.state;
    if (file && dirty) this.drafts.set(file.path, {file, source}); else if (file) this.drafts.delete(file.path);
  }
  async perform(action: () => Promise<unknown>) {
    if (this.state.busy || this.disposed) return;
    this.update({busy: true, message: t('处理中…')});
    try { const message = await action(); this.update({message: typeof message === 'string' ? message : t('完成')}); }
    catch (error) { this.update({message: String(error)}); }
    finally { this.update({busy: false}); this.refreshWorkflowInBackground(); }
  }
  /** For short queries that must not lock the workspace; errors still reach the status line. */
  background(task: () => Promise<unknown>) { void task().catch(error => this.update({message: String(error)})); }
  private stopRun() { this.runToken++; this.stream?.close(); this.stream = undefined; }
  private clearBuild(message = '') { this.buildToken++; this.synctexToken++; clearTimeout(this.poll); this.poll = undefined; this.update({build: undefined, buildNotice: message, buildHistorical: false, pdfTarget: null, synctexBusy: false, synctexNotice: ''}); }
  private clearWorkflow() { this.workflowToken++; this.update({workflow: undefined, workflowNotice: '', workflowEvidence: null}); }
  dispose() { this.disposed = true; this.projectToken++; this.stopRun(); clearTimeout(this.poll); this.buildToken++; this.workflowToken++; this.indexToken++; this.synctexToken++; }
  async start() {
    if (this.started) return;
    this.started = true;
    await this.perform(async () => {
      await this.client.connect();
      if (this.disposed) return;
      await this.refreshProjects();
      const status = await this.client.request('/api/status');
      this.update({status, ready: true, form: {...this.state.form, provider: preferredProvider(status, this.state.form.provider)}});
      void this.refreshModels();
      const id = new URLSearchParams(location.hash.slice(1)).get('project');
      if (id && /^[a-f0-9]{32}$/.test(id)) await this.openProject(id);
      return t('本地会话已建立');
    });
  }
  async refreshStatus() { this.update({status: await this.client.request('/api/status')}); await this.refreshModels(true); }
  /** Listing can take seconds and is optional: the CLI default and a typed model ID work without it. */
  async refreshModels(refresh = false) {
    try { this.update({modelCatalog: await this.client.request<ModelCatalog>(`/api/runtime/models${refresh ? '?refresh=true' : ''}`)}); }
    catch { this.update({modelCatalog: null}); }
  }
  chooseProvider(provider: string) { this.update({form: {...this.state.form, provider, model: provider === this.state.form.provider ? this.state.form.model : ''}}); }
  private async refreshProjects(selected?: string) {
    const projects = await this.client.projects();
    this.update({projects, form: {...this.state.form, projectId: selected || this.state.form.projectId || projects[0]?.id || ''}});
  }
  async createProject() { const project = await this.client.create(this.state.form.projectName); await this.refreshProjects(project.id); await this.openProject(project.id); }
  async importProject(upload: File) { const project = await this.client.import(upload.name.replace(/\.zip$/i, ''), upload); await this.refreshProjects(project.id); await this.openProject(project.id); }
  async openProject(id = this.state.form.projectId) {
    if (!this.mayDiscard()) return;
    const scope = ++this.projectToken; this.fileToken++; this.indexToken++; this.stopRun(); this.clearBuild(); this.clearWorkflow(); this.editorVersion++; this.drafts.clear();
    this.update({project: undefined, file: undefined, files: [], source: '', dirty: false, selection: null, run: undefined, runOutput: null, events: [], runs: [], builds: [], proposals: [], proposalDiffs: {}, proposalConflicts: {}, verifications: [], research: null, researchKind: '', history: [], historyOutput: null, workflowNodeId: 'source', reviewProposalId: '', draftPaths: [], index: null, indexNotice: '', evidenceRecords: [], jump: null});
    const project = await this.client.project(id);
    if (!this.current(scope)) return;
    // A reload, including the one a language switch needs, stays on this project.
    history.replaceState(null, '', `#project=${encodeURIComponent(id)}`);
    this.update({project, form: {...this.state.form, projectId: id, mainFile: project.main_file, filePath: '', runId: '', buildId: ''}});
    await this.refreshProject();
    if (!this.current(scope)) return;
    if (this.state.files.some(file => file.path === project.main_file)) await this.openFile(project.main_file);
    await Promise.all([this.refreshRuns(), this.refreshProposals(), this.refreshBuilds(), this.refreshVerifications()]);
    if (this.current(scope)) { void this.refreshIndex().catch(() => {}); await this.refreshWorkflow(); }
  }
  async refreshProject() {
    const scope = this.projectToken, pid = this.required().id;
    const project = await this.client.project(pid);
    const files = await this.client.files(pid);
    if (!this.current(scope)) return;
    if (this.state.project?.state_revision !== project.state_revision) {
      this.clearBuild(t('项目状态已变化，请重新编译；历史记录仍可查看'));
      this.clearWorkflow();
      this.update({research: null, researchKind: ''});
      void this.refreshIndex().catch(() => {});
    }
    this.update({project, files, form: {...this.state.form, mainFile: project.main_file, filePath: this.state.file?.path || files[0]?.path || ''}});
  }
  /** The outline describes saved source, so it follows state_revision rather than keystrokes. */
  async refreshIndex() {
    if (!this.state.project) return;
    const scope = this.projectToken, token = ++this.indexToken;
    try {
      const index = await this.client.request(this.path('index'));
      if (this.current(scope) && token === this.indexToken) this.update({index, indexNotice: ''});
    } catch (error) {
      if (this.current(scope) && token === this.indexToken) this.update({index: null, indexNotice: t('结构索引读取失败：{0}', String(error))});
      throw error;
    }
  }
  /** Asks the editor to reveal a line; the nonce repeats a jump to the same place. */
  reveal(path: string, line: number) { this.update({jump: {path, line, nonce: ++this.jumpNonce}}); }
  async openAt(path: string, line: number) { await this.openFile(path); if (this.state.file?.path === path) this.reveal(path, line); }
  /** Switching files keeps unsaved text as a draft; it never prompts or writes to disk. */
  async openFile(path = this.state.form.filePath, reload = false) {
    if (!reload && this.state.file?.path === path) return;
    const info = this.state.files.find(file => file.path === path);
    if (info && !info.text) { window.open(this.path('asset?path=' + encodeURIComponent(path)), '_blank', 'noopener'); return; }
    const draft = this.drafts.get(path);
    if (draft) {
      this.stashDraft(); this.drafts.delete(path); this.fileToken++; this.editorVersion++;
      this.update({file: draft.file, source: draft.source, dirty: true, selection: null, form: {...this.state.form, filePath: path}});
      this.syncDrafts(); return;
    }
    const scope = this.projectToken, token = ++this.fileToken, version = this.editorVersion;
    const file = await this.client.read(this.required().id, path);
    if (!this.current(scope) || token !== this.fileToken || version !== this.editorVersion) return;
    if (!reload) this.stashDraft();
    this.update({file, source: file.content, dirty: false, selection: null, form: {...this.state.form, filePath: path}});
    this.syncDrafts();
  }
  async newFile() {
    this.stashDraft(); this.fileToken++; this.editorVersion++;
    const file = {path: this.state.form.newPath, content: '', sha256: ''};
    this.update({file, source: '', dirty: true, selection: null, form: {...this.state.form, filePath: file.path}});
    this.syncDrafts();
  }
  async saveFile() {
    const {file, source} = this.state;
    if (!file) throw new Error(t('请先读取或新建文件'));
    const scope = this.projectToken, version = this.editorVersion;
    this.update({saving: true});
    try {
      const saved = await this.client.save(this.required().id, {...file, content: source});
      if (!this.current(scope) || this.state.file?.path !== file.path) return;
      this.update({file: saved, dirty: version !== this.editorVersion, source: version === this.editorVersion ? saved.content : this.state.source});
    } finally { this.update({saving: false}); }
    await this.refreshProject();
  }
  /** Each draft keeps its own compare-and-swap hash, so a concurrent change stops here and stays a draft. */
  async saveAll() {
    if (this.state.dirty) await this.saveFile();
    const scope = this.projectToken;
    for (const [path, draft] of [...this.drafts]) {
      await this.client.save(this.required().id, {...draft.file, content: draft.source});
      if (!this.current(scope)) return;
      this.drafts.delete(path); this.syncDrafts();
    }
    await this.refreshProject();
  }
  async deleteFile() {
    const {file} = this.state;
    if (!file || !window.confirm(file.sha256 ? t('删除 {0}？可从 Git 历史恢复。', file.path) : t('放弃未保存的新文件 {0}？', file.path))) return;
    if (file.sha256) await this.client.request(this.path(`file?path=${encodeURIComponent(file.path)}&expected_hash=${file.sha256}`), 'DELETE');
    this.fileToken++; this.editorVersion++;
    this.update({file: undefined, source: '', dirty: false, selection: null}); await this.refreshProject();
  }
  async uploadAsset(asset: File) {
    const path = window.prompt(t('项目内资源路径'), 'figures/' + asset.name); if (!path) return;
    const existing = this.state.files.find(file => file.path === path);
    await this.client.request(this.path(`asset?path=${encodeURIComponent(path)}${existing ? '&expected_hash=' + existing.sha256 : ''}`), 'PUT', asset);
    await this.refreshProject();
  }
  async setMain() {
    const project = this.required();
    await this.client.request(this.path(`main?path=${encodeURIComponent(this.state.form.mainFile)}&base_revision=${project.revision}&base_state_revision=${project.state_revision}`), 'PUT');
    this.clearBuild(t('项目入口已变化，请重新编译；历史记录仍可查看')); this.update({research: null, researchKind: ''}); await this.refreshProject();
  }
  async proposeEdit() {
    const {file, files, source} = this.state; if (!file) throw new Error(t('请先读取文件'));
    const saved = files.find(item => item.path === file.path);
    if ((file.sha256 && saved?.sha256 !== file.sha256) || (!file.sha256 && saved)) throw new Error(t('此文件的已保存版本发生变化，请先备份草稿并重新读取文件'));
    const project = this.required();
    await this.client.propose(project.id, [{path: file.path, content: source}], project.revision, 'Source editor proposal', project.state_revision);
    await this.refreshProposals();
  }
  async research(suffix: string, method = 'GET', body?: unknown, global = false, kind: ResearchKind = '') {
    const scope = this.projectToken; const value = await this.client.request(global ? suffix : this.path(suffix), method, body);
    if (this.current(scope)) this.update({research: value, researchKind: kind});
    if (method === 'POST') await this.refreshVerifications();
  }
  private mathInputs() {
    const {mathLhs: lhs, mathRhs: rhs, mathAssumptions} = this.state.form;
    return {lhs, rhs, assumptions: mathAssumptions ? JSON.parse(mathAssumptions) : null};
  }
  async math() { await this.research('math', 'POST', this.mathInputs(), false, 'math'); }
  async lookup(identifier = this.state.form.literatureQuery) { await this.research('reference?identifier=' + encodeURIComponent(identifier), 'POST', undefined, false, 'reference'); }
  async literatureSearch() { await this.research('/api/literature/search?q=' + encodeURIComponent(this.state.form.literatureQuery), 'GET', undefined, true, 'search'); }
  async verifyStructure() { await this.research('verify', 'POST', undefined, false, 'verify'); }
  async refreshRuns() {
    const scope = this.projectToken, token = ++this.runListToken;
    const runs = await this.client.request<RunSummary[]>(`/api/runs?project_id=${this.required().id}`);
    if (!this.current(scope) || token !== this.runListToken) return;
    // The list refreshes statuses; it only chooses a run when nothing is selected.
    // Selection is read after the await, so it is the user's latest choice.
    this.update({runs, form: {...this.state.form, runId: this.state.form.runId || this.state.run?.id || runs[0]?.id || ''}});
  }
  private async loadRun(id: string, token: number, scope: number) {
    const run = await this.client.run(id);
    if (!this.current(scope) || token !== this.runToken || run.task.projectId !== this.required().id) return;
    this.update({run, runOutput: run.result || run.error || run, form: {...this.state.form, runId: id}});
    await this.refreshRuns();
    if (!this.current(scope) || token !== this.runToken) return;
    const events = await this.client.request<unknown[]>(`/api/runs/${id}/event-log`);
    if (this.current(scope) && token === this.runToken) { this.update({events}); this.refreshWorkflowInBackground(); }
  }
  async inspectRun(id = this.state.form.runId) {
    if (!id) return;
    this.stopRun(); this.update({run: undefined, runOutput: null, events: []});
    await this.loadRun(id, this.runToken, this.projectToken);
  }
  async submitRun() {
    if (this.anyDirty()) throw new Error(t('请先保存当前文件，科研上下文使用已保存版本'));
    this.stopRun();
    const scope = this.projectToken, token = this.runToken;
    const {mode, intent, provider, model, engine, literatureQuery, mathLhs, mathRhs} = this.state.form;
    const math_checks = mode === 'math' && mathLhs && mathRhs ? [this.mathInputs()] : [];
    const reference_identifiers = ['literature', 'review'].includes(mode) && /^(?:https:\/\/(?:doi\.org|arxiv\.org)\/|10\.\d{4,9}\/|arxiv:|\d{4}\.\d{4,5})/i.test(literatureQuery) ? [literatureQuery] : [];
    const run = await this.client.request<Run>(this.path('runs'), 'POST', {intent, mode, provider, model: model || null, engine, selection: this.state.selection, math_checks, reference_identifiers});
    if (!this.current(scope) || token !== this.runToken || run.task.projectId !== this.required().id) return;
    this.update({run, runOutput: run, events: [], form: {...this.state.form, runId: run.id}}); await this.refreshRuns();
    if (!this.current(scope) || token !== this.runToken) return;
    this.stream = this.client.events(run.id,
      event => { if (this.current(scope) && token === this.runToken) { this.update({events: [...this.state.events, event]}); if (event.type === 'status') this.refreshWorkflowInBackground(); } },
      () => { if (this.current(scope) && token === this.runToken) void this.loadRun(run.id, token, scope).catch(error => { if (this.current(scope) && token === this.runToken) this.update({message: String(error)}); }); },
      error => { if (this.current(scope) && token === this.runToken) this.update({message: String(error)}); });
  }
  private selectedRun() { if (!this.state.run) throw new Error(t('请先选择任务')); return this.state.run; }
  async interruptRun() { const run = this.selectedRun(); await this.client.request(`/api/runs/${run.id}/interrupt`, 'POST'); await this.inspectRun(run.id); }
  async recoverRun() { const run = this.selectedRun(); this.update({runOutput: await this.client.request(`/api/runs/${run.id}/recover`, 'POST')}); }
  async runProposal() { const run = this.selectedRun(); await this.client.request(`/api/runs/${run.id}/proposal`, 'POST'); await Promise.all([this.refreshProposals(), this.refreshVerifications()]); }
  async verifyRun() { const run = this.selectedRun(); this.clearBuild(); await this.client.request(`/api/runs/${run.id}/verify?engine=${this.state.form.engine}`, 'POST'); await Promise.all([this.refreshVerifications(), this.refreshBuilds()]); }
  async refreshVerifications() {
    const scope = this.projectToken;
    const records = await this.client.request<VerificationRecord[]>(this.path('evidence'));
    const relevant = records.filter(record => record.kind === 'research_verification').slice(0, 10);
    const verifications = await Promise.all(relevant.map(record => this.client.request<VerificationRecord>(`/api/verifications/${record.id}`)));
    if (this.current(scope)) this.update({verifications, evidenceRecords: records});
  }
  async retryVerification(record: VerificationRecord) {
    this.clearBuild();
    const result = await this.client.request<VerificationRecord>(`/api/verifications/${record.id}/retry?engine=${encodeURIComponent(this.state.form.engine)}`, 'POST');
    await Promise.all([this.refreshVerifications(), this.refreshBuilds()]);
    if (result.result.verification_status === 'failed') return t('独立验证重试仍失败：{0}', String(result.result.verification_error || t('请查看验收记录')));
    return t('独立验证已重试，源码未重复应用；请查看验收记录');
  }
  private refreshWorkflowInBackground() {
    if (this.state.project && !this.disposed) void this.refreshWorkflow().catch(() => {});
  }
  async refreshWorkflow() {
    const project = this.state.project; if (!project) return;
    const scope = this.projectToken, token = ++this.workflowToken, runId = this.state.form.runId;
    this.update({workflowNotice: t('正在读取科研流程…')});
    try {
      const workflow = await this.client.request<WorkflowView>(this.path('workflow' + (runId ? '?run_id=' + encodeURIComponent(runId) : ''), project.id));
      if (!this.current(scope) || token !== this.workflowToken) return;
      if (workflow.project_id !== project.id || (runId && workflow.run_id !== runId)) throw new Error(t('科研流程所属项目或任务不匹配'));
      const previous = this.state.workflow;
      const changedIdentity = !previous || ['project_id', 'run_id', 'input_state_revision', 'output_state_revision', 'current_state_revision'].some(key => previous[key as keyof WorkflowView] !== workflow[key as keyof WorkflowView]);
      this.update({workflow, workflowNotice: '', workflowEvidence: changedIdentity ? null : this.state.workflowEvidence, workflowNodeId: workflow.nodes.some(node => node.id === this.state.workflowNodeId) ? this.state.workflowNodeId : workflow.nodes[0]?.id || ''});
    } catch (error) {
      if (this.current(scope) && token === this.workflowToken) this.update({workflow: undefined, workflowEvidence: null, workflowNotice: t('科研流程读取失败：{0}', String(error))});
      throw error;
    }
  }
  selectWorkflowNode(id: string) {
    if (this.state.workflow?.nodes.some(node => node.id === id)) this.update({workflowNodeId: id, workflowEvidence: null});
  }
  async inspectWorkflowEvidence(reference: WorkflowEvidenceRef) {
    const node = this.state.workflow?.nodes.find(item => item.id === this.state.workflowNodeId);
    if (!node?.evidence_refs.some(item => item.kind === reference.kind && item.id === reference.id)) throw new Error(t('此证据已不属于当前流程节点，请刷新'));
    const scope = this.projectToken, token = this.workflowToken, nodeId = node.id;
    let value: unknown;
    if (reference.kind === 'source_snapshot') { this.update({workflowEvidence: {source_snapshot_id: reference.id, details: node.details}}); return; }
    if (reference.kind === 'run') value = await this.client.run(reference.id);
    else if (reference.kind === 'proposal') value = await this.client.request<Proposal>(this.path(`proposals/${reference.id}`));
    else if (reference.kind === 'build') value = await this.client.build(reference.id);
    else if (['verification', 'research_verification'].includes(reference.kind)) value = await this.client.request<VerificationRecord>(`/api/verifications/${reference.id}`);
    else value = await this.client.request<VerificationRecord>(`/api/evidence/${reference.id}`);
    if (this.current(scope) && token === this.workflowToken && nodeId === this.state.workflowNodeId) this.update({workflowEvidence: value});
  }
  async workflowAction(nodeId: string, requested: WorkflowAction) {
    const workflow = this.state.workflow;
    const node = workflow?.nodes.find(item => item.id === nodeId);
    const action = node?.actions.find(item => item.kind === requested.kind && item.target_id === requested.target_id);
    if (!workflow || workflow.project_id !== this.required().id || !action?.enabled) throw new Error(action?.reason || t('此流程操作不可用，请刷新流程'));
    const scope = this.projectToken, token = this.workflowToken, selectedNode = this.state.workflowNodeId;
    const showEvidence = (value: unknown) => { if (this.current(scope) && token === this.workflowToken && selectedNode === this.state.workflowNodeId) this.update({workflowEvidence: value}); };
    const target = action.target_id;
    if (['inspect_source', 'submit_run', 'compile'].includes(action.kind) && target !== this.required().id) throw new Error(t('流程操作所属项目不匹配'));
    switch (action.kind) {
      case 'inspect_source': await this.openFile(this.required().main_file); break;
      case 'submit_run': await this.submitRun(); break;
      case 'compile': await this.compile(); break;
      case 'inspect_run': if (target) await this.inspectRun(target); break;
      case 'inspect_build': if (target) await this.inspectBuild(target); break;
      case 'verify_run': if (target) { await this.inspectRun(target); await this.verifyRun(); } break;
      case 'create_proposal': if (target) { await this.inspectRun(target); await this.runProposal(); } break;
      case 'review_proposal':
        await this.refreshProposals();
        if (!this.state.proposals.some(proposal => proposal.id === target)) throw new Error(t('提案不存在，请刷新流程'));
        this.update({reviewProposalId: target || '', workflowNodeId: 'approval', workflowEvidence: null}); break;
      case 'inspect_context': case 'inspect_tools': {
        if (!target) throw new Error(t('此节点没有任务证据'));
        const run = await this.client.run(target);
        if (run.task.projectId !== this.required().id) throw new Error(t('任务证据所属项目不匹配'));
        showEvidence(action.kind === 'inspect_context' ? {run_id: run.id, source_snapshot_id: run.task.sourceSnapshotId, context_snapshot_id: run.task.contextSnapshotId, context: run.task.context} : {run_id: run.id, domain: run.task.context.domain, domain_tool_inputs: run.task.domainToolInputs}); break;
      }
      case 'inspect_verification': if (target) showEvidence(await this.client.request<VerificationRecord>(`/api/verifications/${target}`)); break;
      case 'retry_verification': if (target) return this.retryVerification(await this.client.request<VerificationRecord>(`/api/verifications/${target}`)); break;
    }
  }
  async refreshProposals() {
    const scope = this.projectToken; const proposals = await this.client.request<Proposal[]>(this.path('proposals'));
    if (this.current(scope)) this.update({proposals, proposalDiffs: {}, proposalConflicts: {}});
  }
  async proposalDiff(proposal: Proposal) {
    const scope = this.projectToken; const value = await this.client.request(this.path(`proposals/${proposal.id}/diff`));
    if (this.current(scope)) this.update({proposalDiffs: {...this.state.proposalDiffs, [proposal.id]: value}});
  }
  /** Why a proposal no longer applies, for the state it was asked about; a later save asks again. */
  async proposalConflict(proposal: Proposal) {
    const scope = this.projectToken; const value = await this.client.request<ProposalConflict>(this.path(`proposals/${proposal.id}/conflict`));
    if (this.current(scope)) this.update({proposalConflicts: {...this.state.proposalConflicts, [proposal.id]: value}});
  }
  async decide(proposal: Proposal, decision: 'accept' | 'reject') {
    if (decision === 'accept' && !this.mayDiscard()) return;
    const result = await this.client.decide(this.required().id, proposal, decision, this.state.form.engine);
    try {
      if (result.apply_status === 'applied') {
        this.drafts.clear(); this.update({dirty: false, draftPaths: []}); await this.reloadEditor();
      }
      await this.refreshProposals();
      if (result.apply_status === 'applied') await Promise.all([this.refreshBuilds(), this.refreshVerifications()]);
    } catch (error) {
      // A failed follow-up GET cannot change a successful commit into a failed
      // apply. Preserve that distinction even if the service drops afterwards.
      if (result.apply_status === 'applied') return t('修改已应用；界面状态刷新失败：{0}。请重新读取文件和验收记录，请勿重复应用此提案。', String(error));
      throw error;
    }
    if (result.apply_status === 'applied' && result.verification_status === 'failed') return t('修改已应用；独立验证启动失败：{0}。请勿重复应用此提案。', result.verification_error?.message || t('请查看验收记录'));
    if (result.apply_status === 'applied') return t('修改已应用；独立验证单独记录，仍需人工审阅');
  }
  private async reloadEditor() {
    const path = this.state.file?.path; await this.refreshProject();
    if (path && this.state.files.some(file => file.path === path)) await this.openFile(path, true);
    else this.update({file: undefined, source: '', dirty: false, selection: null, form: {...this.state.form, filePath: ''}});
  }
  async snapshot() { this.update({historyOutput: await this.client.request(this.path('snapshot?message=' + encodeURIComponent(this.state.form.snapshotMessage || 'Manual snapshot')), 'POST'), history: []}); }
  async history() {
    const scope = this.projectToken; const history = await this.client.request<HistoryEntry[]>(this.path('history'));
    if (this.current(scope)) this.update({history, historyOutput: null});
  }
  async restore(item: HistoryEntry) {
    if (!this.mayDiscard() || !window.confirm(t('恢复此快照？当前磁盘版本会先保存为快照。'))) return;
    const project = this.required();
    await this.client.request(this.path(`restore?revision=${encodeURIComponent(item.commit)}&base_revision=${project.revision}&base_state_revision=${project.state_revision}`), 'POST');
    this.drafts.clear(); this.update({dirty: false, draftPaths: []}); await this.reloadEditor();
  }
  private buildMatches(build: Build) {
    return build.legacy_identity !== true && typeof build.snapshot_id === 'string' && build.snapshot_id.length > 0 && typeof build.identity_digest === 'string' && build.identity_digest.length > 0
      && build.state_revision === this.state.project?.state_revision && build.main_file === this.state.form.mainFile && build.engine === this.state.form.engine;
  }
  private async loadBuild(id: string, token: number, scope: number, historical: boolean) {
    const build = await this.client.build(id);
    if (!this.current(scope) || token !== this.buildToken || build.project_id !== this.required().id) return;
    const statusChanged = this.state.build?.id !== build.id || this.state.build.status !== build.status;
    const builds = this.state.builds.map(item => item.id === build.id ? build : item);
    if (!historical && !this.buildMatches(build)) { this.update({builds, build: undefined, buildNotice: t('此编译属于历史入口或版本，请从编译历史显式查看')}); return; }
    this.update({builds, build, buildHistorical: !this.buildMatches(build), buildNotice: '', form: {...this.state.form, buildId: id}});
    if (statusChanged && !['queued', 'running'].includes(build.status)) this.refreshWorkflowInBackground();
    if (['queued', 'running'].includes(build.status)) {
      this.poll = setTimeout(() => { void this.loadBuild(id, token, scope, historical).catch(error => { if (this.current(scope) && token === this.buildToken) this.update({message: String(error)}); }); }, 700);
    }
  }
  async inspectBuild(id = this.state.form.buildId) {
    if (!id) return;
    this.clearBuild(); await this.loadBuild(id, this.buildToken, this.projectToken, true);
  }
  async refreshBuilds() {
    const scope = this.projectToken, token = this.buildToken;
    const builds = await this.client.request<Build[]>(this.path('builds'));
    if (!this.current(scope) || token !== this.buildToken) return;
    this.update({builds, form: {...this.state.form, buildId: this.state.build?.id || builds[0]?.id || ''}});
    const latest = builds[0];
    if (latest && this.buildMatches(latest) && !this.state.buildHistorical) {
      this.clearBuild(); await this.loadBuild(latest.id, this.buildToken, scope, false);
    } else if (!this.state.build && latest) this.update({buildNotice: t('最近编译属于历史入口或版本，请从编译历史显式查看')});
  }
  async compile() {
    if (this.anyDirty()) throw new Error(t('请先保存源码'));
    this.clearBuild(); const token = this.buildToken, scope = this.projectToken;
    const build = await this.client.request<Build>(this.path('builds'), 'POST', {main_file: this.state.form.mainFile, engine: this.state.form.engine});
    if (!this.current(scope) || token !== this.buildToken) return;
    this.update({builds: [build, ...this.state.builds.filter(item => item.id !== build.id)], form: {...this.state.form, buildId: build.id}});
    await this.loadBuild(build.id, token, scope, false);
  }
  async cancelBuild() {
    const build = this.state.build; if (!build) return;
    await this.client.request(`/api/builds/${build.id}/interrupt`, 'POST');
    await this.inspectBuild(build.id);
  }
  /** SyncTeX is only trustworthy when the displayed PDF was built from exactly the saved editor text. */
  private synctexBuild() {
    const {build} = this.state;
    if (!build || build.status !== 'success') throw new Error(t('SyncTeX 需要一次成功的编译'));
    if (this.state.buildHistorical) throw new Error(t('这是历史编译，其源码与当前文件可能不同，不能用于定位'));
    if (this.anyDirty()) throw new Error(t('有未保存的修改，行号可能已偏移；请先保存并重新编译'));
    return build;
  }
  private async synctexQuery(build: Build, suffix: string) {
    const scope = this.projectToken, token = ++this.synctexToken;
    this.update({synctexBusy: true, synctexNotice: ''});
    const stale = () => !this.current(scope) || token !== this.synctexToken || this.state.build?.id !== build.id;
    try {
      const position = await this.client.request<SyncTexPosition>(`/api/builds/${build.id}/synctex${suffix}`);
      if (stale()) return null;
      if (position.build_id !== build.id || position.state_revision !== build.state_revision) throw new Error(t('SyncTeX 结果与当前编译的身份不符'));
      this.update({synctexBusy: false});
      return position;
    } catch (error) {
      if (!stale()) this.update({synctexBusy: false, synctexNotice: t('SyncTeX 定位失败：{0}', String(error))});
      return null;
    }
  }
  /** Source to PDF: highlight where this saved line appears. */
  async synctex(line = Number(this.state.form.synctexLine), column = 1) {
    const build = this.synctexBuild(), file = this.state.file;
    if (!file) throw new Error(t('请先打开源文件'));
    const position = await this.synctexQuery(build, `?source=${encodeURIComponent(file.path)}&line=${line}&column=${column}`);
    if (!position) return;
    if (position.status !== 'matched' || !position.boxes.length) { this.update({synctexNotice: t('第 {0} 行在此 PDF 中没有对应位置', line)}); return; }
    this.update({pdfTarget: {buildId: build.id, boxes: position.boxes, nonce: ++this.jumpNonce}, synctexNotice: t('源码第 {0} 行 → PDF 第 {1} 页', line, position.boxes[0].page), form: {...this.state.form, synctexLine: String(line)}});
  }
  /** PDF to source: open the file and line that produced this point. */
  async synctexInverse(page: number, x: number, y: number) {
    const build = this.synctexBuild();
    const position = await this.synctexQuery(build, `/inverse?page=${page}&x=${x.toFixed(2)}&y=${y.toFixed(2)}`);
    if (!position) return;
    if (position.status !== 'matched' || !position.file || !position.line) { this.update({synctexNotice: t('PDF 第 {0} 页此处没有对应源码', page)}); return; }
    await this.openAt(position.file, position.line);
    this.update({synctexNotice: t('PDF 第 {0} 页 → {1}:{2}', page, position.file, position.line)});
  }
}
