import { t } from '../i18n.js';
import { RefreshCw, ShieldCheck } from './icons.js';
import { useMemo } from 'react';
import { action, Fields, StatusMark } from './common.js';
import type { Props } from './common.js';
import { buildEvidenceClaims, recordIsCurrent, recordKindLabel, recordStatus } from '../models/evidence-model.js';
import { short } from '../models/project-model.js';

const when = (seconds: number) => new Date(seconds * 1000).toLocaleString('zh-CN', {hour12: false});
const verificationSteps: Record<string, string> = {
  static_source_checks: t('静态源码检查'), isolated_compilation: t('隔离编译'), compilation: t('编译'), compilation_pending: t('编译未完成'), source_structure_or_citations: t('结构或引用'),
};
const stepLabel = (value: unknown) => { const text = String(value); return verificationSteps[text] || text; };

export function EvidencePane({workspace, state}: Props) {
  const claims = useMemo(() => buildEvidenceClaims({project: state.project, run: state.run, proposals: state.proposals, build: state.build, buildHistorical: state.buildHistorical, records: state.evidenceRecords, selectedProposalId: state.reviewProposalId}), [state.project, state.run, state.proposals, state.build, state.buildHistorical, state.evidenceRecords, state.reviewProposalId]);
  const current = state.evidenceRecords.filter(record => recordIsCurrent(record, state.project)).length;
  return <fieldset className="pane evidence-pane" disabled={state.busy}>
    <div className="pane-head">
      <h2>{t('科研证据')}</h2>
      <span className="muted">{t('每一项只证明它自己说明的内容，彼此不能互相替代')}</span>
      <button id="verify" className="quiet" onClick={action(workspace, () => workspace.verifyStructure())}><ShieldCheck className="icon" aria-hidden="true"/>{t('运行结构与引用检查')}</button>
      <button id="refresh-verifications" className="quiet" onClick={action(workspace, () => workspace.refreshVerifications())}><RefreshCw className="icon" aria-hidden="true"/>{t('刷新')}</button>
    </div>
    <table className="claims" aria-label={t('当前版本的证据状态')}>
      <thead><tr><th scope="col">{t('事项')}</th><th scope="col">{t('状态')}</th><th scope="col">{t('依据')}</th><th scope="col">{t('不代表')}</th></tr></thead>
      <tbody>{claims.map(claim => <tr key={claim.id} id={`claim-${claim.id}`} data-status={claim.status}><th scope="row">{claim.label}</th><td><StatusMark status={claim.status}/></td><td>{claim.detail}</td><td className="muted">{claim.limit}</td></tr>)}</tbody>
    </table>

    <h3>{t('独立验证')}</h3>
    <p className="muted section-note">{t('由系统在保存的快照上重新检查与编译，与模型结果分开记录。人工审阅始终是必需的。')}</p>
    <div id="verifications" className="record-list">
      {!state.verifications.length && <p className="pane-note">{t('当前项目还没有独立验证记录。')}</p>}
      {state.verifications.map(record => {
        const isCurrent = record.current_state_matches === true && record.main_file === state.project?.main_file && record.engine === state.form.engine;
        const verification = (record.verification || {}) as {status?: unknown; satisfied?: unknown[]; failed?: unknown[]; uncertain?: unknown[]; missing?: unknown[]; reason?: unknown};
        const groups: [string, unknown[] | undefined][] = [[t('已满足'), verification.satisfied], [t('失败'), verification.failed], [t('不确定'), verification.uncertain], [t('缺失'), verification.missing]];
        return <details key={record.id} className="record" data-current={isCurrent}>
          <summary><StatusMark status={recordStatus(record)}/><span>{isCurrent ? t('独立验证') : t('历史独立验证（不作为当前证据）')} <code>{record.id.slice(0, 8)}</code>{t('（模型完成不等于科研验收）')}</span><time className="muted">{when(record.created_at)}</time></summary>
          <div className="record-body">
            <button disabled={record.current_state_matches !== true || !(record.result.verification_status === 'failed' || record.verification?.status === 'failed')} onClick={action(workspace, () => workspace.retryVerification(record))}>{t('重试独立验证')}</button>
            {typeof verification.reason === 'string' && <p className="conflict">{verification.reason}</p>}
            <dl className="fields">{groups.filter(([, items]) => items?.length).map(([label, items]) => <div key={label}><dt>{label}</dt><dd>{items?.map(stepLabel).join('、')}</dd></div>)}
              <div><dt>{t('编译')}</dt><dd>{record.build ? `${short(String(record.build.id ?? ''), 8)} · ${String(record.build.status)}` : '—'}</dd></div>
              <div><dt>{t('绑定状态')}</dt><dd><code>{record.state_revision || t('旧格式未绑定')}</code></dd></div></dl>
            <details><summary>{t('完整记录')}</summary><Fields value={record}/></details>
          </div>
        </details>;
      })}
    </div>

    <h3>{t('全部记录')} <span className="muted">{current} {t('条属于当前版本，')}{state.evidenceRecords.length - current} {t('条为历史')}</span></h3>
    <div id="evidence-records" className="record-list">
      {state.evidenceRecords.map(record => <details key={record.id} className="record" data-current={recordIsCurrent(record, state.project)}>
        <summary><StatusMark status={recordStatus(record)}/><span>{recordKindLabel(record.kind)} <code>{record.id.slice(0, 8)}</code>{!recordIsCurrent(record, state.project) && <span className="tag">{t('历史')}</span>}</span><time className="muted">{when(record.created_at)}</time></summary>
        <div className="record-body"><Fields value={record.result}/></div>
      </details>)}
    </div>
  </fieldset>;
}
