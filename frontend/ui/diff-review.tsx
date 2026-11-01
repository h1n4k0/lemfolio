import { locale, t } from '../i18n.js';
import { Check, RefreshCw, X } from './icons.js';
import { useEffect, useMemo, useRef, useState } from 'react';
import { action, StatusMark, usePreference } from './common.js';
import type { Props } from './common.js';
import type { Proposal } from '../client.js';
import { hunkLocation, parseUnified, segments, splitRows, withInlineRanges } from '../models/diff-model.js';
import type { DiffFile, DiffLine } from '../models/diff-model.js';
import { short } from '../models/project-model.js';
import { recordStatus } from '../models/evidence-model.js';

const fileStatus: Record<string, string> = {added: t('新增'), deleted: t('删除'), modified: t('修改'), renamed: t('重命名')};
const proposalStatus: Record<Proposal['status'], string> = {pending: t('待审批'), accepted: t('已接受并应用'), rejected: t('已拒绝')};

function Code({line}: {line: DiffLine}) {
  return <code>{segments(line).map((part, index) => part.changed ? <mark key={index}>{part.text}</mark> : <span key={index}>{part.text}</span>)}</code>;
}
function SplitFile({file}: {file: DiffFile}) {
  return <table className="diff-table diff-split"><colgroup><col className="n"/><col/><col className="n"/><col/></colgroup>
    <thead><tr><th colSpan={2} scope="col">{t('原始内容')}</th><th colSpan={2} scope="col">{t('新内容')}</th></tr></thead>
    <tbody>{splitRows(file.lines).map((row, index) => row.hunk !== undefined
      ? <tr key={index} className="diff-hunk"><td colSpan={4}>{hunkLocation(row.hunk)}</td></tr>
      : <tr key={index}>
        <td className="n">{row.left?.old ?? ''}</td><td className="c" data-kind={row.left ? row.left.kind : 'none'}>{row.left && <Code line={row.left}/>}</td>
        <td className="n">{row.right?.new ?? ''}</td><td className="c" data-kind={row.right ? row.right.kind : 'none'}>{row.right && <Code line={row.right}/>}</td>
      </tr>)}</tbody>
  </table>;
}
function UnifiedFile({file}: {file: DiffFile}) {
  return <table className="diff-table diff-unified"><colgroup><col className="n"/><col className="n"/><col/></colgroup>
    <thead><tr><th scope="col">{t('原')}</th><th scope="col">{t('新')}</th><th scope="col">{t('内容')}</th></tr></thead>
    <tbody>{file.lines.map((line, index) => line.kind === 'hunk'
      ? <tr key={index} className="diff-hunk"><td colSpan={3}>{hunkLocation(line.text)}</td></tr>
      : <tr key={index}><td className="n">{line.old ?? ''}</td><td className="n">{line.new ?? ''}</td><td className="c" data-kind={line.kind}><span className="diff-sign" aria-hidden="true">{line.kind === 'add' ? '+' : line.kind === 'del' ? '−' : ' '}</span><Code line={line}/></td></tr>)}</tbody>
  </table>;
}

/** The facts of a conflict: the two versions, whether this proposal wrote anything, and what really moved. */
function Conflict({workspace, state, proposal}: Props & {proposal: Proposal}) {
  const conflict = state.proposalConflicts[proposal.id];
  const project = state.project;
  useEffect(() => { void workspace.proposalConflict(proposal).catch(() => {}); }, [proposal.id, project?.state_revision]);
  const moved = useMemo(() => (conflict?.files || []).flatMap(file => file.diff ? parseUnified(file.diff) : []), [conflict]);
  return <div className="conflict conflict-panel" role="alert">
    <p><strong>{t('此提案基于旧版本，不能应用。')}</strong> {t('文件未被改动；请基于当前版本重新生成提案。')}</p>
    <dl className="version-pair">
      <div><dt>{t('提案基于')}</dt><dd><code title={proposal.base_state_revision || proposal.base_revision}>{short(proposal.base_state_revision || proposal.base_revision)}</code></dd></div>
      <div><dt>{t('项目当前')}</dt><dd><code title={project?.state_revision}>{short(project?.state_revision)}</code></dd></div>
    </dl>
    {conflict?.changes_elsewhere && <p className="muted">{t('提案涉及的文件内容未变；变化发生在项目的其他文件或入口。')}</p>}
    {moved.length > 0 && <section className="conflict-drift" aria-label={t('提案生成后的变化')}>
      <h4>{t('提案生成后，这些位置发生了变化')}</h4>
      {moved.map(file => <div key={file.path} className="diff-file"><h5><code>{file.path}</code> <span className="adds">+{file.adds}</span> <span className="dels">−{file.dels}</span></h5><UnifiedFile file={file}/></div>)}
    </section>}
  </div>;
}
/** What happened after a decision. Applying and verifying are separate results and stay separate here. */
function Outcome({state, proposal}: Props & {proposal: Proposal}) {
  if (proposal.status === 'pending') return null;
  const decided = (proposal as Proposal & {decided_at?: number | null}).decided_at;
  const verification = state.evidenceRecords.find(record => record.kind === 'research_verification' && (record.result as {proposal_id?: unknown}).proposal_id === proposal.id);
  const result = (proposal as Proposal & {result_state_revision?: string | null}).result_state_revision;
  return <dl className="proposal-outcome">
    <div><dt>{t('决定')}</dt><dd>{proposalStatus[proposal.status]}{decided ? ` · ${new Date(decided * 1000).toLocaleString(locale === 'zh' ? 'zh-CN' : 'en-US', {hour12: false})}` : ''}</dd></div>
    {proposal.status === 'accepted' && <div><dt>{t('应用后版本')}</dt><dd><code>{short(result)}</code></dd></div>}
    {proposal.status === 'accepted' && <div><dt>{t('独立验证')}</dt><dd>{verification ? <StatusMark status={recordStatus(verification)}/> : <span className="muted">{t('没有记录')}</span>}</dd></div>}
  </dl>;
}

/** Both the review pane and the workflow approval point use exactly the same digest-bound decisions and unsaved-draft guard. */
export function ProposalReview({workspace, state, proposal, decisionsEnabled = true}: Props & {proposal: Proposal; decisionsEnabled?: boolean}) {
  const [layout, setLayout] = usePreference<'split' | 'unified'>('diff-layout', 'split');
  const parsed = useMemo(() => parseUnified(proposal.diff), [proposal.diff]);
  const model = state.proposalDiffs[proposal.id];
  const files = useMemo(() => withInlineRanges(parsed, model), [parsed, model]);
  const project = state.project;
  const stale = proposal.status === 'pending' && Boolean(project) && (proposal.base_revision !== project?.revision || (proposal.base_state_revision ? proposal.base_state_revision !== project?.state_revision : false));
  const link = state.evidenceRecords.find(record => record.kind === 'proposal_created' && (record.result as {proposal_id?: unknown}).proposal_id === proposal.id);
  const runId = link ? String((link.result as {run_id?: unknown}).run_id ?? '') : '';
  const run = runId ? state.runs.find(item => item.id === runId) : undefined;
  const canDecide = decisionsEnabled && proposal.status === 'pending';
  return <div className="proposal" data-status={proposal.status} data-stale={stale || undefined}>
    <header className="proposal-head">
      <div className="proposal-title">
        <span className="proposal-state" data-status={stale ? 'stale' : proposal.status}>{stale ? t('已过期，存在冲突') : proposalStatus[proposal.status]}</span>
        <code title={proposal.digest}>{t('摘要')} {short(proposal.digest, 16)}</code>
      </div>
      <div className="proposal-actions">
        <button className="primary" disabled={!canDecide || stale} title={stale ? t('基础版本已变化，不能接受此提案') : undefined} onClick={action(workspace, () => workspace.decide(proposal, 'accept'))}><Check className="icon" aria-hidden="true"/>{t('接受此摘要绑定的修改')}</button>
        <button disabled={!canDecide} onClick={action(workspace, () => workspace.decide(proposal, 'reject'))}><X className="icon" aria-hidden="true"/>{t('拒绝修改')}</button>
      </div>
    </header>
    {stale && <Conflict workspace={workspace} state={state} proposal={proposal}/>}
    <Outcome workspace={workspace} state={state} proposal={proposal}/>
    <dl className="proposal-meta">
      <div><dt>{t('修改依据')}</dt><dd>{proposal.summary || t('未提供')}</dd></div>
      <div><dt>{t('来源')}</dt><dd>{runId ? <>{t('科研任务')} <code>{short(runId, 8)}</code>{run ? ` · ${run.intent}` : ''}</> : t('未关联科研任务')}</dd></div>
      <div><dt>{t('基础版本')}</dt><dd><code>{short(proposal.base_revision)}</code> {t('· 状态')} <code>{short(proposal.base_state_revision)}</code></dd></div>
      <div><dt>{t('修改文件')}</dt><dd>{files.length ? <nav className="diff-nav" aria-label={t('修改的文件')}>{files.map(file => <button key={file.path} className="diff-file-chip" onClick={event => event.currentTarget.closest('.proposal')?.querySelector(`[data-file="${CSS.escape(file.path)}"]`)?.scrollIntoView({block: 'start'})}>{file.path} <span className="adds">+{file.adds}</span> <span className="dels">−{file.dels}</span></button>)}</nav> : proposal.changes.map(change => change.path).join('、')}</dd></div>
    </dl>
    {files.length > 0 && <div className="toolbar diff-toolbar" role="toolbar" aria-label={t('差异显示')}>
      <div className="segmented" role="radiogroup" aria-label={t('差异布局')}>
        <button role="radio" aria-checked={layout === 'split'} onClick={() => setLayout('split')}>{t('并排')}</button>
        <button role="radio" aria-checked={layout === 'unified'} onClick={() => setLayout('unified')}>{t('合并')}</button>
      </div>
      <span className="muted diff-engine">{(model as {status?: unknown} | undefined)?.status === 'available' ? t('行内差异：Keel Rust 差异模型') : t('行内差异：本地对比')}</span>
    </div>}
    {files.length ? files.map(file => <section key={file.path} data-file={file.path} className="diff-file" aria-label={t('{0} 的差异', file.path)}>
      <h4><code>{file.path}</code><span className="tag">{fileStatus[file.status] || file.status}</span></h4>
      {layout === 'split' ? <SplitFile file={file}/> : <UnifiedFile file={file}/>}
    </section>) : <pre className="diff-raw">{proposal.diff}</pre>}
  </div>;
}

/** Loads the native diff model once for the expanded proposal; a failure leaves the local comparison in place. */
function useDiffModel({workspace, state}: Props, proposal: Proposal | undefined) {
  useEffect(() => {
    if (proposal && !(proposal.id in state.proposalDiffs)) void workspace.proposalDiff(proposal).catch(() => {});
  }, [proposal?.id, proposal && proposal.id in state.proposalDiffs]);
}

export function ReviewPane({workspace, state}: Props) {
  const [opened, setOpened] = useState('');
  // A decided proposal stays open, so its outcome is visible rather than folding away.
  const last = useRef('');
  const selected = opened || state.reviewProposalId || state.proposals.find(item => item.status === 'pending')?.id || last.current;
  last.current = selected === '-' ? '' : selected;
  const proposal = state.proposals.find(item => item.id === selected);
  useDiffModel({workspace, state}, proposal);
  return <fieldset className="pane review-pane" disabled={state.busy}>
    <div className="pane-head">
      <h2>{t('修改审阅')}</h2>
      <span className="muted">{t('修改必须在此处人工审批后才会写入文件')}</span>
      <button id="refresh-proposals" className="quiet" onClick={action(workspace, () => workspace.refreshProposals())}><RefreshCw className="icon" aria-hidden="true"/>{t('刷新')}</button>
    </div>
    <div className="review-body">
      <div id="proposals" className="proposal-list">
        {!state.proposals.length && <p className="pane-note">{t('还没有修改提案。可在编辑器中将改动提交为提案，或让研究助手生成修改。')}</p>}
        {state.proposals.map(item => {
          const open = item.id === selected;
          return <details key={item.id} open={open} onToggle={event => { if (event.currentTarget.open && !open) setOpened(item.id); }}>
            <summary onClick={event => { event.preventDefault(); setOpened(open ? '-' : item.id); }}><code>{short(item.id, 8)}</code><span className="proposal-state" data-status={item.status}>{proposalStatus[item.status]}</span><span className="proposal-summary">{item.summary}</span></summary>
            <ProposalReview workspace={workspace} state={state} proposal={item}/>
          </details>;
        })}
      </div>
    </div>
  </fieldset>;
}
