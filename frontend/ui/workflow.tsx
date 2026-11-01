import { t, translated } from '../i18n.js';
import { CircleDot, RefreshCw, stageIcons } from './icons.js';
import { action, Fields } from './common.js';
import type { Props } from './common.js';
import type { WorkflowAction, WorkflowNode } from '../client.js';
import { ProposalReview } from './diff-review.js';

const statusLabels: Record<WorkflowNode['status'], string> = {not_run: t('未运行'), running: t('运行中'), pending_review: t('待人工审批'), passed: t('已通过'), failed: t('失败'), interrupted: t('已中断'), blocked: t('已阻止')};
const actionLabels: Record<WorkflowAction['kind'], string> = {
  inspect_source: t('查看当前项目源码'), inspect_context: t('查看上下文证据'), inspect_tools: t('查看工具证据'), submit_run: t('提交科研任务'), inspect_run: t('查看任务'),
  create_proposal: t('生成待审提案'), review_proposal: t('展开提案与人工审批'), compile: t('编译当前源码'), inspect_build: t('查看编译记录'), verify_run: t('独立验证任务'), inspect_verification: t('查看验收证据'), retry_verification: t('重试独立验证'),
};

/** Operational view of persisted lineage. Selecting a stage never starts work; each action reuses its guarded workspace method. */
export function Workflow({workspace, state}: Props) {
  const workflow = state.workflow;
  const selected = workflow?.nodes.find(node => node.id === state.workflowNodeId);
  const targetProposal = selected?.actions.find(item => item.kind === 'review_proposal')?.target_id || selected?.evidence_refs.find(item => item.kind === 'proposal')?.id;
  const proposal = ['approval', 'proposal'].includes(selected?.id || '') ? state.proposals.find(item => item.id === targetProposal) : undefined;
  return <fieldset disabled={state.busy} id="workflow" className="pane workflow-pane">
    <div className="pane-head">
      <h2>{t('科研流程')}</h2>
      <span className="muted">{t('阶段状态来自已保存的任务与证据；模型完成不等于科研验收')}</span>
      <button id="refresh-workflow" className="quiet" onClick={action(workspace, () => workspace.refreshWorkflow())}><RefreshCw className="icon" aria-hidden="true"/>{t('刷新')}</button>
    </div>
    <span id="workflow-notice" className="pane-note" role="status">{state.workflowNotice}</span>
    {workflow && <>
      <p id="workflow-context" className="identity-line">{t('项目')} <code>{workflow.project_id.slice(0, 8)}</code> {t('· 任务')} <code>{workflow.run_id?.slice(0, 8) || t('尚未提交')}</code></p>
      <ol id="workflow-graph" className="stages" aria-label={t('科研流程阶段与证据状态')}>
        {workflow.nodes.map(node => <li key={node.id}>
          <button id={`workflow-node-${node.id}`} className="stage" data-status={node.status} data-historical={node.historical} aria-pressed={node.id === selected?.id} aria-label={`${translated(node.label)}：${node.historical ? t('历史 ') : ''}${statusLabels[node.status]}`} onClick={() => workspace.selectWorkflowNode(node.id)}>
            <span className="stage-name">{(() => { const Icon = stageIcons[node.id] || CircleDot; return <Icon className="icon" aria-hidden="true"/>; })()}{translated(node.label)}</span>
            <span className="stage-status"><i aria-hidden="true"/>{node.historical ? t('历史 · ') : ''}{statusLabels[node.status]}</span>
          </button>
        </li>)}
      </ol>
      <dl id="workflow-identity" className="identity">
        <div><dt>{t('输入状态')}</dt><dd><code>{workflow.input_state_revision || '—'}</code></dd></div>
        <div><dt>{t('审批产出状态')}</dt><dd><code>{workflow.output_state_revision || '—'}</code></dd></div>
        <div><dt>{t('当前状态')}</dt><dd><code>{workflow.current_state_revision}</code></dd></div>
      </dl>
      {selected && <section id="workflow-detail" aria-label={t('阶段详情')}>
        <h3>{translated(selected.label)} · {selected.historical ? t('历史 · ') : ''}{statusLabels[selected.status]}</h3>
        <div id="workflow-actions" className="action-list">{selected.actions.map(item => <p key={`${item.kind}-${item.target_id}`}><button data-action={item.kind} disabled={!item.enabled} title={item.reason ? translated(item.reason) : undefined} onClick={action(workspace, () => workspace.workflowAction(selected.id, item))}>{actionLabels[item.kind]}</button>{!item.enabled && item.reason && <span className="muted"> {item.reason}</span>}</p>)}</div>
        <div id="workflow-node-details" className="detail-block"><Fields value={{state_revision: selected.state_revision, ...selected.details}}/></div>
        <ul id="workflow-evidence-refs" className="plain ref-list">{selected.evidence_refs.map(reference => <li key={`${reference.kind}-${reference.id}`}>{reference.kind === 'source_snapshot' ? <>{t('绑定快照')} <code>{reference.id}</code></> : <button className="quiet" data-evidence-kind={reference.kind} onClick={action(workspace, () => workspace.inspectWorkflowEvidence(reference))}>{t('查看')} {reference.kind} {reference.id.slice(0, 8)}</button>}</li>)}</ul>
        {proposal && <section id="workflow-approval"><p className="muted">{t('人工审批点：查看完整 Diff 后，明确接受或拒绝此摘要绑定的修改。')}</p><ProposalReview workspace={workspace} state={state} proposal={proposal} decisionsEnabled={selected.actions.some(item => item.kind === 'review_proposal' && item.target_id === proposal.id && item.enabled)}/></section>}
        <div id="workflow-evidence-output" className="detail-block">{state.workflowEvidence != null && <Fields value={state.workflowEvidence}/>}</div>
      </section>}
    </>}
  </fieldset>;
}
