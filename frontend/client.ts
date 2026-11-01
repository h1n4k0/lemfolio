import { t } from './i18n.js';
import * as validators from './generated/validators.js';
import { CONTRACT_VERSION } from './generated/contracts.js';
import type { Build, Change, DecisionResponse, PaperFile, Project, Proposal, Run, RuntimeEvent, Session, TextFile } from './generated/contracts.js';
export type { ProposalConflict, ModelCatalog, ProviderModels, Build, Change, DecisionResponse, HistoryEntry, PaperFile, Project, Proposal, Run, RunSummary, RuntimeEvent, SyncTexBox, SyncTexPosition, TextFile, VerificationRecord, WorkflowAction, WorkflowEvidenceRef, WorkflowNode, WorkflowView } from './generated/contracts.js';

// Generated from the Python-owned wire schema. Every core response is checked
// before it can become workspace state; TypeScript assertions are not parsing.
function decode<T>(name: string, value: unknown): T {
  const validate = validators[name as keyof typeof validators];
  if (!validate) throw new Error(t('接口契约缺少 {0}', name));
  if (!validate(value)) {
    const errors = validate.errors?.map(error => `response${error.instancePath} ${error.message}`).join('; ') || t('无效响应');
    throw new Error(t('接口契约 {0} 验证失败: {1}', name, errors));
  }
  return value as T;
}
function decodeList(name: string, value: unknown): unknown[] {
  if (!Array.isArray(value)) throw new Error(t('接口契约 {0}[] 验证失败: response 必须为数组', name));
  return value.map(item => decode(name, item));
}
function validateResponse(path: string, method: string, value: unknown): unknown {
  const pathname = path.split('?')[0];
  if (pathname === '/api/session') return decode('Session', value);
  if (pathname === '/api/runtime/models') return decode('ModelCatalog', value);
  if (pathname === '/api/projects') return method === 'GET' ? decodeList('Project', value) : decode('Project', value);
  if (pathname === '/api/import') return decode('Project', value);
  if (/^\/api\/projects\/[^/]+$/.test(pathname) || /\/main$/.test(pathname)) return decode('Project', value);
  if (/\/files$/.test(pathname)) return decodeList('PaperFile', value);
  if (/\/file$/.test(pathname) && method !== 'DELETE') return decode('TextFile', value);
  if (/\/proposals$/.test(pathname)) return method === 'GET' ? decodeList('Proposal', value) : decode('Proposal', value);
  if (/\/proposals\/[^/]+\/decide$/.test(pathname)) return decode('DecisionResponse', value);
  if (/\/proposals\/[^/]+\/conflict$/.test(pathname)) return decode('ProposalConflict', value);
  if (/\/proposals\/[^/]+$/.test(pathname)) return decode('Proposal', value);
  if (/^\/api\/runs\/[^/]+\/proposal$/.test(pathname)) return decode('Proposal', value);
  if (pathname === '/api/runs') return decodeList('RunSummary', value);
  if (/^\/api\/runs\/[^/]+$/.test(pathname) || /\/projects\/[^/]+\/runs$/.test(pathname)) return decode('Run', value);
  if (/\/projects\/[^/]+\/builds$/.test(pathname)) return method === 'GET' ? decodeList('Build', value) : decode('Build', value);
  if (/^\/api\/builds\/[^/]+$/.test(pathname)) return decode('Build', value);
  if (/^\/api\/builds\/[^/]+\/synctex(?:\/inverse)?$/.test(pathname)) return decode('SyncTexPosition', value);
  if (/^\/api\/verifications\/[^/]+(?:\/retry)?$/.test(pathname)) return decode('VerificationRecord', value);
  if (/^\/api\/evidence\/[^/]+$/.test(pathname)) return decode('VerificationRecord', value);
  if (/^\/api\/projects\/[^/]+\/workflow$/.test(pathname)) return decode('WorkflowView', value);
  if (/^\/api\/projects\/[^/]+\/evidence$/.test(pathname)) return decodeList('VerificationRecord', value);
  if (/\/history$/.test(pathname)) return decodeList('HistoryEntry', value);
  if (/\/event-log$/.test(pathname)) return decodeList('RuntimeEvent', value);
  return value;
}
function validateRequest(path: string, method: string, value: unknown): void {
  const pathname = path.split('?')[0];
  if (method === 'POST' && pathname === '/api/projects') decode('ProjectInput', value);
  else if (method === 'PUT' && /\/file$/.test(pathname)) decode('FileInput', value);
  else if (method === 'POST' && /\/proposals$/.test(pathname)) decode('ProposalInput', value);
  else if (method === 'POST' && /\/decide$/.test(pathname)) decode('DecisionInput', value);
  else if (method === 'POST' && /\/projects\/[^/]+\/runs$/.test(pathname)) decode('TaskInput', value);
  else if (method === 'POST' && /\/projects\/[^/]+\/builds$/.test(pathname)) decode('CompileInput', value);
}
export class LemfolioClient {
  private csrf = '';
  constructor(readonly base = '') {}
  async connect(): Promise<void> {
    const response = await fetch(this.base + '/api/session', {credentials: 'same-origin'});
    const value: unknown = await response.json();
    if (!response.ok) throw new Error(t('本地会话无法建立'));
    const session = decode<Session>('Session', value);
    if (session.contract_version !== CONTRACT_VERSION || response.headers.get('X-Lemfolio-Contract-Version') !== CONTRACT_VERSION) {
      throw new Error(t('接口契约版本不兼容，WebUI 需要 {0}', CONTRACT_VERSION));
    }
    this.csrf = session.csrf;
  }
  async request<T = unknown>(path: string, method = 'GET', body?: unknown): Promise<T> {
    validateRequest(path, method, body);
    const headers: Record<string, string> = {'X-Lemfolio-Contract-Version': CONTRACT_VERSION};
    if (method !== 'GET') headers['x-csrf-token'] = this.csrf;
    let data: BodyInit | undefined;
    if (body instanceof Blob) { data = body; headers['Content-Type'] = 'application/octet-stream'; }
    else if (body !== undefined) { data = JSON.stringify(body); headers['Content-Type'] = 'application/json'; }
    const response = await fetch(this.base + path, {method, body: data, headers, credentials: 'same-origin'});
    const value: unknown = await response.json();
    if (!response.ok) {
      const detail = value && typeof value === 'object' && 'detail' in value ? value.detail : value;
      throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
    }
    if (response.headers.get('X-Lemfolio-Contract-Version') !== CONTRACT_VERSION) throw new Error(t('接口契约版本已变化，请重新加载工作台'));
    return validateResponse(path, method, value) as T;
  }
  projects() { return this.request<Project[]>('/api/projects'); }
  project(id: string) { return this.request<Project>(`/api/projects/${id}`); }
  create(name: string) { return this.request<Project>('/api/projects', 'POST', {name}); }
  import(name: string, zip: Blob) { return this.request<Project>('/api/import?name=' + encodeURIComponent(name), 'POST', zip); }
  files(id: string) { return this.request<PaperFile[]>(`/api/projects/${id}/files`); }
  read(id: string, path: string) { return this.request<TextFile>(`/api/projects/${id}/file?path=${encodeURIComponent(path)}`); }
  save(id: string, file: TextFile) { return this.request<TextFile>(`/api/projects/${id}/file`, 'PUT', {path: file.path, content: file.content, expected_hash: file.sha256 || null}); }
  propose(id: string, changes: Change[], revision: string, summary: string, stateRevision?: string) { return this.request<Proposal>(`/api/projects/${id}/proposals`, 'POST', {changes, base_revision: revision, base_state_revision: stateRevision, summary}); }
  decide(id: string, proposal: Proposal, decision: 'accept' | 'reject', engine = 'pdflatex') { return this.request<DecisionResponse>(`/api/projects/${id}/proposals/${proposal.id}/decide`, 'POST', {decision, digest: proposal.digest, engine}); }
  run(id: string) { return this.request<Run>(`/api/runs/${id}`); }
  build(id: string) { return this.request<Build>(`/api/builds/${id}`); }
  events(id: string, onEvent: (value: RuntimeEvent) => void, onDone: () => void, onError: (error: Error) => void) {
    const source = new EventSource(this.base + `/api/runs/${id}/events`);
    let closed = false;
    const done = () => { if (closed) return; closed = true; source.close(); onDone(); };
    source.onmessage = event => {
      if (closed) return;
      try { onEvent(decode<RuntimeEvent>('RuntimeEvent', JSON.parse(event.data))); }
      catch (error) { closed = true; source.close(); onError(error instanceof Error ? error : new Error(t('任务事件无效'))); }
    };
    source.addEventListener('done', done);
    source.onerror = done;
    return source;
  }
}
