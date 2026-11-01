import { t } from '../i18n.js';
import { useMemo, useRef, useState } from 'react';
import { action, Field, Fields, IconButton, ModelText, StatusMark, useDismiss } from './common.js';
import { Archive, BookOpenText, Check, ChevronDown, ChevronRight, ClipboardCheck, FilePen, History, KeyRound, MessagesSquare, PenLine, RefreshCw, ScrollText, Search, Send, ShieldCheck, Sigma, Square, Terminal, X } from './icons.js';
import type { Props } from './common.js';
import { mathStatus, referenceStatus, toolStatusLabel } from '../models/evidence-model.js';
import { readProviders, short } from '../models/project-model.js';
import type { LucideIcon } from 'lucide-react';
import type { Run } from '../client.js';

const modes = [
  {id: 'chat', icon: MessagesSquare, name: t('讨论'), english: 'Discussion', hint: t('就论文内容提问。不会产生文件修改。'), yields: t('产出：回答 · 不改文件'), placeholder: t('例如：第二节的假设是否足以支撑定理 1？')},
  {id: 'edit', icon: PenLine, name: t('语义修改'), english: 'Semantic Editing', hint: t('说明要改什么。结果进入 Diff 审阅，只有人工接受后才写入文件。'), yields: t('产出：待审提案 · 需人工接受'), placeholder: t('例如：把符号 x 统一改为 y，并同步相关标签与公式')},
  {id: 'math', icon: Sigma, name: t('数学推理'), english: 'Mathematical Reasoning', hint: t('说明要检查的推导。可附加等式，由 SymPy 独立做有限符号检查。'), yields: t('产出：推理说明 + SymPy 记录'), placeholder: t('例如：检查引理 2 的推导是否依赖了未声明的假设')},
  {id: 'literature', icon: BookOpenText, name: t('文献'), english: 'Literature', hint: t('核验 DOI / arXiv 元数据，或检索条目。元数据匹配不代表内容支持结论。'), yields: t('产出：说明 + 来源元数据记录'), placeholder: t('例如：检查引用是否覆盖了第三节的主要结论')},
  {id: 'review', icon: ClipboardCheck, name: t('论文审阅'), english: 'Paper Review', hint: t('结构化审阅论断、假设与证据。此模式不修改文件。'), yields: t('产出：审阅意见 · 不改文件'), placeholder: t('例如：审阅全文的假设是否一致，并按严重程度排序')},
] as const;
const runStates: Record<Run['status'], string> = {queued: t('排队中'), running: t('运行中'), completed: t('模型已返回'), failed: t('失败'), interrupted: t('已中断')};
const providerStates = {ready: t('可用'), login: t('未登录'), missing: t('不可用'), off: t('未启用')} as const;

const catalogNotes: Record<string, string> = {
  listed: t('目录来自 codex debug models'),
  aliases: t('Claude Code 没有目录命令，这些别名来自 claude --help；也可填写完整模型名'),
  configured: t('来自 LEMFOLIO_API_MODEL'),
  unavailable: t('此后端没有报告模型目录，可使用默认模型或手动填写'),
};
/** Provider and model chooser. Every model listed here was reported by that provider; nothing is hard-coded. */
function ModelPicker({workspace, state}: Props) {
  const [open, setOpen] = useState(false);
  const ref = useDismiss<HTMLDivElement>(open, () => setOpen(false));
  const providers = useMemo(() => readProviders(state.status), [state.status]);
  const selected = providers.find(item => item.id === state.form.provider);
  const catalog = state.modelCatalog?.providers.find(item => item.provider === state.form.provider);
  const models = catalog?.models || [];
  const isApi = state.form.provider === 'api';
  const modelName = state.form.model ? models.find(item => item.id === state.form.model)?.name || state.form.model : isApi ? t('未选择模型') : t('CLI 默认模型');
  return <div className="picker" ref={ref}>
    <button id="model-picker" className="picker-button" aria-haspopup="dialog" aria-expanded={open} onClick={() => setOpen(!open)}>
      {isApi ? <KeyRound className="icon" aria-hidden="true"/> : <Terminal className="icon" aria-hidden="true"/>}
      <span className="picker-text"><span className="picker-provider">{selected?.name || state.form.provider}<span className="provider-state" data-state={selected?.state}>{selected ? providerStates[selected.state] : t('状态未知')}</span></span><span id="model-current" className="picker-model">{modelName}</span></span>
      <ChevronDown className="icon picker-chevron" data-open={open} aria-hidden="true"/>
    </button>
    <div id="model-widget" className="widget glass picker-widget" role="dialog" aria-label={t('模型后端与模型')} hidden={!open}>
      <h3>{t('模型后端')}</h3>
      <div className="provider-list" role="radiogroup" aria-label={t('模型后端')}>{providers.map(item => <div key={item.id}>
        <label data-disabled={!item.selectable || undefined} data-checked={state.form.provider === item.id || undefined}>
          <input type="radio" name="provider" value={item.id} checked={state.form.provider === item.id} disabled={!item.selectable} onChange={() => workspace.chooseProvider(item.id)}/>
          {item.id === 'api' ? <KeyRound className="icon" aria-hidden="true"/> : <Terminal className="icon" aria-hidden="true"/>}
          <span className="provider-name">{item.name}</span><span className="provider-state" data-state={item.state}>{item.summary}</span>
          {item.hint && <span className="muted provider-hint">{item.hint}</span>}
        </label>
      </div>)}</div>
      <h3>{t('模型')}</h3>
      <div className="model-list" role="radiogroup" aria-label={t('模型')}>
        {!isApi && <button id="model-default" className="model-row" role="radio" aria-checked={!state.form.model} onClick={() => { workspace.form('model', ''); setOpen(false); }}><span className="model-row-text"><span>{t('CLI 默认模型')}</span><span className="muted">{t('不传 --model，由 CLI 自己决定')}</span></span>{!state.form.model && <Check className="icon" aria-hidden="true"/>}</button>}
        {models.map(item => <button key={item.id} className="model-row" role="radio" aria-checked={state.form.model === item.id} data-model={item.id} onClick={() => { workspace.form('model', item.id); setOpen(false); }}><span className="model-row-text"><span>{item.name}</span>{item.description && <span className="muted">{item.description}</span>}</span>{state.form.model === item.id && <Check className="icon" aria-hidden="true"/>}</button>)}
      </div>
      <p className="catalog-note muted">{catalog ? catalogNotes[catalog.status] : t('正在读取模型目录…')}</p>
      <label htmlFor="model">{t('手动填写模型 ID')}</label>
      <Field workspace={workspace} state={state} name="model" id="model" placeholder={t('例如 gpt-6-sol')} spellCheck={false}/>
      <div className="picker-foot">
        <button id="refresh-status" className="quiet" disabled={state.busy || !state.ready} onClick={action(workspace, () => workspace.refreshStatus())}><RefreshCw className="icon" aria-hidden="true"/>{t('重新检查')}</button>
      </div>
      <details className="runtime-details"><summary>{t('运行环境详情')}</summary><div id="status"><Fields value={state.status}/></div></details>
    </div>
  </div>;
}

function ModeInputs({workspace, state}: Props) {
  const {mode} = state.form;
  if (mode === 'math') return <div className="mode-inputs">
    <label>{t('可选：等式检查')}</label>
    <div className="equation-row"><Field workspace={workspace} state={state} name="mathLhs" id="math-lhs" placeholder="(x+1)**2" aria-label={t('等式左侧')} spellCheck={false}/><span aria-hidden="true">=</span><Field workspace={workspace} state={state} name="mathRhs" id="math-rhs" placeholder="x**2+2*x+1" aria-label={t('等式右侧')} spellCheck={false}/></div>
    <Field workspace={workspace} state={state} name="mathAssumptions" id="math-assumptions" placeholder={t('假设 JSON，如 {"x":{"positive":true}}')} aria-label={t('符号假设 JSON')} spellCheck={false}/>
    <button id="math" disabled={!state.form.mathLhs || !state.form.mathRhs} onClick={action(workspace, () => workspace.math())}><Sigma className="icon" aria-hidden="true"/>{t('立即做有限符号检查')}</button>
    <p className="muted">{t('不经模型，由 SymPy 直接计算。结果不是证明。')}</p>
    <ToolResult {...{workspace, state}}/>
  </div>;
  if (mode === 'literature') return <div className="mode-inputs">
    <label htmlFor="literature-query">{t('DOI、arXiv ID 或检索词')}</label>
    <Field workspace={workspace} state={state} name="literatureQuery" id="literature-query" placeholder={t('10.1000/xyz 或 2301.00001 或关键词')} spellCheck={false}/>
    <div className="button-row"><button id="lookup" disabled={!state.form.literatureQuery} onClick={action(workspace, () => workspace.lookup())}><ShieldCheck className="icon" aria-hidden="true"/>{t('核验元数据')}</button><button id="literature-search" disabled={state.form.literatureQuery.length < 2} onClick={action(workspace, () => workspace.literatureSearch())}><Search className="icon" aria-hidden="true"/>{t('检索 Crossref')}</button></div>
    <ToolResult {...{workspace, state}}/>
  </div>;
  return null;
}

function Reference({item}: {item: Record<string, unknown>}) {
  const authors = Array.isArray(item.authors) ? item.authors.join('，') : '';
  return <div className="reference">
    <p className="reference-title">{String(item.title || item.identifier || t('无题名'))}</p>
    <p className="muted">{[authors, item.year, item.venue || item.journal].filter(Boolean).join(' · ')}</p>
    {typeof item.doi === 'string' && <code>{item.doi}</code>}
  </div>;
}
function ToolResult({state}: Props) {
  const value = state.research as Record<string, unknown> | null;
  if (!value || !state.researchKind) return null;
  const body = (value.result && typeof value.result === 'object' ? value.result : value) as Record<string, unknown>;
  if (state.researchKind === 'math') {
    const counterexample = body.counterexample as {values?: unknown} | undefined;
    return <div id="research-output" className="tool-result"><StatusMark status={mathStatus(body.status)} label={toolStatusLabel(body.status)}/><p className="muted">{t('有限符号检查，不是数学证明。')}</p>
      <dl className="fields">{[[t('左侧'), body.lhs], [t('右侧'), body.rhs], [t('差'), body.difference]].filter(([, item]) => item !== undefined).map(([label, item]) => <div key={String(label)}><dt>{String(label)}</dt><dd><code>{String(item)}</code></dd></div>)}
        {counterexample?.values != null && <div><dt>{t('反例')}</dt><dd><code>{JSON.stringify(counterexample.values)}</code></dd></div>}</dl>
      <details><summary>{t('完整记录')}</summary><Fields value={body}/></details></div>;
  }
  if (state.researchKind === 'reference') {
    const metadata = (body.metadata && typeof body.metadata === 'object' ? body.metadata : null) as Record<string, unknown> | null;
    return <div id="research-output" className="tool-result"><StatusMark status={referenceStatus(body.status)} label={toolStatusLabel(body.status)}/>{metadata && <Reference item={metadata}/>}<p className="muted">{t('元数据匹配不代表文献内容支持论文中的结论。')}</p><details><summary>{t('完整记录')}</summary><Fields value={body}/></details></div>;
  }
  if (state.researchKind === 'search') {
    const results = Array.isArray(body.results) ? body.results as Record<string, unknown>[] : [];
    return <div id="research-output" className="tool-result"><p className="muted">{body.status === 'ok' ? t('Crossref 元数据，共 {0} 条；相关性需阅读原文判断', results.length) : t('检索不可用：{0}', String(body.error || body.status))}</p><ul className="plain">{results.map((item, index) => <li key={index}><Reference item={item}/></li>)}</ul></div>;
  }
  return null;
}

/** One kind of output. Groups stay apart so a model's words are never read as a tool's record. */
function ResultGroup({id, icon: Icon, title, count, note, open = true, children}: {id: string; icon: LucideIcon; title: string; count?: number; note?: string; open?: boolean; children: React.ReactNode}) {
  return <details id={id} className="result-group" open={open}>
    <summary><ChevronRight className="icon caret" aria-hidden="true"/><Icon className="icon" aria-hidden="true"/><span className="result-group-title">{title}</span>{count !== undefined && <span className="tag">{count}</span>}{note && <span className="muted result-group-note">{note}</span>}</summary>
    <div className="result-group-body">{children}</div>
  </details>;
}
/** Long answers start folded to their first lines; the whole text is one press away and is never cut. */
function LongText(props: Props & {text: string}) {
  const [expanded, setExpanded] = useState(false);
  const long = props.text.length > 480;
  return <div className="long-text" data-clamped={long && !expanded || undefined}>
    <ModelText {...props}/>
    {long && <button className="quiet long-toggle" aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>{expanded ? t('收起') : t('展开全文（{0} 字）', props.text.length)}</button>}
  </div>;
}

function RunResult({workspace, state, onReview}: Props & {onReview(): void}) {
  const run = state.run;
  if (!run) return state.runOutput ? <Fields value={state.runOutput}/> : <p className="pane-note">{t('选择工作模式并提交任务后，结果显示在这里。')}</p>;
  const mode = run.task.mode, result = run.result;
  const tools = ((run.task.context as {domain?: {tool_evidence?: unknown}}).domain?.tool_evidence);
  const evidence = Array.isArray(tools) ? tools as {tool?: unknown; result?: Record<string, unknown>}[] : [];
  const math = evidence.filter(item => item.tool === 'math_check'), references = evidence.filter(item => item.tool === 'lookup_reference');
  const current = run.task.baseStateRevision === state.project?.state_revision;
  const changes = result?.changes || [];
  const created = state.evidenceRecords.filter(record => record.kind === 'proposal_created' && (record.result as {run_id?: unknown}).run_id === run.id).map(record => String((record.result as {proposal_id?: unknown}).proposal_id ?? ''));
  const linked = state.proposals.filter(item => created.includes(item.id));
  const toolCount = math.length + references.length + (result?.checks.length || 0);
  return <div className="run-result" data-mode={mode} data-current={current}>
    <p id="run-context" className="run-context" data-current={current}>{!current && <History className="icon" aria-hidden="true"/>}{current ? t('当前项目状态的任务；模型完成不等于科研验收') : t('历史任务（不作为当前证据）')}</p>
    <div className="run-line"><span className="run-state" data-status={run.status}>{runStates[run.status]}</span><span className="muted">{modes.find(item => item.id === mode)?.name} · {run.task.provider}{run.task.model ? ` · ${String(run.task.model)}` : ''} {t('· 基于')} <code>{short(run.task.baseRevision, 8)}</code></span></div>
    {run.error && <p className="conflict" role="alert">{run.error}</p>}
    {result && <ResultGroup id="group-answer" icon={MessagesSquare} title={mode === 'review' ? t('审阅意见') : mode === 'edit' ? t('修改说明') : t('模型回答')} note={t('模型输出，未经验证')}><LongText text={result.answer} workspace={workspace} state={state}/></ResultGroup>}
    {mode === 'edit' || changes.length > 0 ? <ResultGroup id="group-proposal" icon={FilePen} title={t('提案')} count={linked.length || undefined} note={t('尚未写入文件')}>
      <h4>{t('建议修改')} {changes.length} {t('处文件')}</h4>
      <ul className="plain">{changes.map(change => <li key={change.path}><code>{change.path}</code> <span className="muted">{change.edits ? t('局部修改 {0} 处', change.edits.length) : change.content === null ? t('删除文件') : t('整文件替换')}</span></li>)}</ul>
      <p className="muted">{t('建议尚未写入文件，需先生成提案并人工审批。')}</p>
      <div className="button-row"><button id="run-proposal" disabled={!changes.length || run.status !== 'completed'} onClick={() => { void workspace.perform(async () => { await workspace.runProposal(); onReview(); return t('已生成待审提案，文件尚未改动'); }); }}><FilePen className="icon" aria-hidden="true"/>{t('生成提案并打开审阅')}</button></div>
      {linked.length > 0 && <ul className="plain linked-proposals" aria-label={t('由此任务生成的提案')}>{linked.map(item => <li key={item.id}><code>{short(item.id, 8)}</code> <span className="proposal-state" data-status={item.status}>{item.status === 'pending' ? t('待审批') : item.status === 'accepted' ? t('已接受并应用') : t('已拒绝')}</span></li>)}</ul>}
    </ResultGroup> : <button id="run-proposal" hidden disabled/>}
    {toolCount > 0 && <ResultGroup id="group-tools" icon={ShieldCheck} title={t('工具证据')} count={toolCount} open={math.length + references.length > 0}>
      {math.length > 0 && <section className="result-section"><h4>{t('独立符号检查')}</h4><ul className="plain">{math.map((item, index) => <li key={index}><StatusMark status={mathStatus(item.result?.status)} label={toolStatusLabel(item.result?.status)}/><details className="raw-record"><summary>{t('原始记录')}</summary><Fields value={item.result}/></details></li>)}</ul><p className="muted">{t('有限符号检查不是证明。')}</p></section>}
      {references.length > 0 && <section className="result-section"><h4>{t('元数据核验')}</h4><ul className="plain">{references.map((item, index) => <li key={index}><StatusMark status={referenceStatus(item.result?.status)} label={toolStatusLabel(item.result?.status)}/><details className="raw-record"><summary>{t('原始记录')}</summary><Fields value={item.result}/></details></li>)}</ul></section>}
      {result && result.checks.length > 0 && <section className="result-section"><h4>{t('模型自述的检查')} <span className="muted">{t('未经独立验证')}</span></h4><ul className="plain model-checks">{result.checks.map((check, index) => <li key={index}>{check}</li>)}</ul></section>}
    </ResultGroup>}
  </div>;
}

/** On-demand research panel. It holds no state of its own: every value shown comes from persisted runs and evidence. */
export function Assistant({workspace, state, onClose, onReview, onEvidence}: Props & {onClose(): void; onReview(): void; onEvidence(): void}) {
  const mode = modes.find(item => item.id === state.form.mode) || modes[0];
  const intent = useRef<HTMLTextAreaElement>(null);
  const running = state.run?.status === 'queued' || state.run?.status === 'running';
  return <aside className="assistant" aria-label={t('研究助手')}>
    <div className="assistant-head"><h2>{t('研究助手')}</h2><IconButton icon={X} label={t('关闭研究助手')} tip={t('关闭（Esc）')} className="tip-end" onClick={onClose}/></div>
    <ModelPicker workspace={workspace} state={state}/>
    <fieldset className="assistant-body" disabled={state.busy}>
      <div className="mode-tabs" role="radiogroup" aria-label={t('工作模式')}>{modes.map(item => <button key={item.id} id={`mode-${item.id}`} role="radio" aria-checked={state.form.mode === item.id} title={item.english} onClick={() => workspace.form('mode', item.id)}><item.icon className="icon" aria-hidden="true"/><span>{item.name}</span></button>)}</div>
      <p className="mode-hint">{mode.hint}</p>
      <p id="mode-yields" className="mode-yields">{mode.yields}</p>
      <textarea id="intent" ref={intent} rows={2} placeholder={mode.placeholder} aria-label={t('科研任务')} value={state.form.intent} onChange={event => workspace.form('intent', event.currentTarget.value)}/>
      <p id="selection" className="selection-note">{state.selection ? <>{t('将附带选区')} <code>{state.selection.path}:{state.selection.start_line}–{state.selection.end_line}</code></> : t('未选择源码；将按任务提取相关上下文')}</p>
      <div className="button-row">
        <button id="submit-run" className="primary" disabled={!state.form.intent.trim() || running} onClick={action(workspace, () => workspace.submitRun())}><Send className="icon" aria-hidden="true"/>{t('提交任务')}</button>
        <button id="interrupt-run" disabled={!state.run} onClick={action(workspace, () => workspace.interruptRun())}><Square className="icon" aria-hidden="true"/>{t('中断')}</button>
      </div>
      <ModeInputs workspace={workspace} state={state}/>
      <div id="run-output" className="run-output"><RunResult workspace={workspace} state={state} onReview={onReview}/></div>
      <div className="button-row">
        <button id="verify-run" disabled={state.run?.status !== 'completed'} onClick={() => { void workspace.perform(async () => { await workspace.verifyRun(); onEvidence(); return t('已提交独立验证，结果在证据页查看'); }); }}><ShieldCheck className="icon" aria-hidden="true"/>{t('独立验证此任务')}</button>
        <button id="recover-run" className="quiet" disabled={!state.run} onClick={action(workspace, () => workspace.recoverRun())}><RefreshCw className="icon" aria-hidden="true"/>{t('检查恢复状态')}</button>
      </div>
      <details className="run-history">
        <summary><Archive className="icon" aria-hidden="true"/><label htmlFor="runs">{t('历史任务（{0}）', state.runs.length)}</label></summary>
        <div className="input-row">
          <select id="runs" value={state.form.runId} onChange={event => workspace.form('runId', event.currentTarget.value)}>{state.runs.map(run => <option key={run.id} value={run.id}>{run.id.slice(0, 8)} {runStates[run.status]} {run.intent.slice(0, 40)}</option>)}</select>
          <button id="inspect-run" onClick={action(workspace, () => workspace.inspectRun())}>{t('查看')}</button>
        </div>
        <p className="muted">{t('查看历史任务不会改变当前版本；其结果不作为当前证据。')}</p>
      </details>
      <details className="events-log"><summary><ScrollText className="icon" aria-hidden="true"/>{t('运行事件（{0}）', state.events.length)}</summary><ol id="events" className="plain">{(state.events as {seq?: number; type?: string; at?: string; data?: {text?: unknown; status?: unknown}}[]).map((event, index) => <li key={event.seq ?? index}><code>{event.type}</code> <span className="muted">{typeof event.data?.status === 'string' ? event.data.status : typeof event.data?.text === 'string' ? event.data.text.slice(0, 200) : ''}</span></li>)}</ol></details>
    </fieldset>
  </aside>;
}
