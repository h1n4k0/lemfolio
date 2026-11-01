// Separates what each piece of evidence actually establishes. These claims are
// never merged: a successful compile says nothing about mathematics or conclusions.
import { t } from '../i18n.js';
import type { Build, Project, Proposal, Run, VerificationRecord } from '../client.js';

export type EvidenceStatus = 'verified' | 'failed' | 'unchecked' | 'uncertain' | 'human';
export const evidenceLabels: Record<EvidenceStatus, string> = {verified: t('已验证'), failed: t('验证失败'), unchecked: t('尚未检查'), uncertain: t('结果不确定'), human: t('需要人工审阅')};
export interface EvidenceClaim { id: string; label: string; status: EvidenceStatus; detail: string; limit: string }

export interface EvidenceInput {
  project?: Project; run?: Run; proposals: Proposal[]; build?: Build; buildHistorical: boolean;
  records: VerificationRecord[]; selectedProposalId?: string | null;
}

const result = (record: VerificationRecord) => record.result as Record<string, unknown>;
const toolEvidence = (run?: Run): {tool?: unknown; result?: {status?: unknown}}[] => {
  const domain = (run?.task.context as {domain?: {tool_evidence?: unknown}} | undefined)?.domain;
  return Array.isArray(domain?.tool_evidence) ? domain.tool_evidence : [];
};
const fold = (statuses: EvidenceStatus[]): EvidenceStatus => statuses.includes('failed') ? 'failed' : statuses.includes('uncertain') ? 'uncertain' : statuses.length ? 'verified' : 'unchecked';

export const mathStatus = (value: unknown): EvidenceStatus => value === 'symbolically_equivalent' ? 'verified' : value === 'not_equivalent' ? 'failed' : 'uncertain';
export const referenceStatus = (value: unknown): EvidenceStatus => value === 'verified_metadata' ? 'verified' : ['not_found', 'identifier_mismatch', 'invalid_identifier'].includes(String(value)) ? 'failed' : 'uncertain';
const toolStatusLabels: Record<string, string> = {
  symbolically_equivalent: t('有限符号检查：等价'), not_equivalent: t('有限符号检查：不等价'), inconclusive: t('结果不确定'), unsupported: t('表达式不受支持'), timeout: t('检查超时'), unavailable: t('工具不可用'), error: t('工具出错'),
  verified_metadata: t('元数据已匹配'), not_found: t('未找到该条目'), identifier_mismatch: t('标识符与条目不符'), invalid_identifier: t('标识符无效'),
  passed: t('通过'), failed: t('未通过'),
};
// An unknown status stays visible as sent, so a new server value is never hidden behind a guess.
export const toolStatusLabel = (value: unknown): string => toolStatusLabels[String(value)] ?? String(value);
export const structureStatus = (value: unknown): EvidenceStatus => value === 'passed' ? 'verified' : value === 'failed' ? 'failed' : 'uncertain';

export function modelClaim(run?: Run): EvidenceClaim {
  const limit = t('只表示模型结束生成，不代表内容正确');
  if (!run) return {id: 'model', label: t('模型完成任务'), status: 'unchecked', detail: t('尚未选择任务'), limit};
  if (run.status === 'completed') return {id: 'model', label: t('模型完成任务'), status: 'verified', detail: t('{0} 已返回结果', run.task.provider), limit};
  if (run.status === 'failed' || run.status === 'interrupted') return {id: 'model', label: t('模型完成任务'), status: 'failed', detail: run.status === 'failed' ? run.error || t('任务失败') : t('任务已中断'), limit};
  return {id: 'model', label: t('模型完成任务'), status: 'unchecked', detail: t('任务运行中'), limit};
}

export function appliedClaim(proposals: Proposal[], selectedId?: string | null): EvidenceClaim {
  const limit = t('只表示文件已写入，不代表修改正确');
  const proposal = proposals.find(item => item.id === selectedId) || proposals[0];
  if (!proposal) return {id: 'applied', label: t('文件修改已应用'), status: 'unchecked', detail: t('没有修改提案'), limit};
  if (proposal.status === 'accepted') return {id: 'applied', label: t('文件修改已应用'), status: 'verified', detail: t('提案 {0} 已由人工接受并写入', proposal.id.slice(0, 8)), limit};
  if (proposal.status === 'rejected') return {id: 'applied', label: t('文件修改已应用'), status: 'unchecked', detail: t('提案 {0} 已拒绝，文件未改动', proposal.id.slice(0, 8)), limit};
  return {id: 'applied', label: t('文件修改已应用'), status: 'human', detail: t('提案 {0} 等待人工审批，文件未改动', proposal.id.slice(0, 8)), limit};
}

export function compileClaim(build: Build | undefined, historical: boolean): EvidenceClaim {
  const limit = t('只表示 LaTeX 能生成 PDF，不是数学证明，也不代表结论成立');
  if (!build) return {id: 'compile', label: t('LaTeX 编译成功'), status: 'unchecked', detail: t('当前版本尚无编译记录'), limit};
  if (historical) return {id: 'compile', label: t('LaTeX 编译成功'), status: 'uncertain', detail: t('正在查看历史编译 {0}，不作为当前证据', build.id.slice(0, 8)), limit};
  if (build.status === 'success') return {id: 'compile', label: t('LaTeX 编译成功'), status: 'verified', detail: t('编译 {0} · {1} · {2}', build.id.slice(0, 8), build.engine, build.main_file), limit};
  if (build.status === 'queued' || build.status === 'running') return {id: 'compile', label: t('LaTeX 编译成功'), status: 'unchecked', detail: t('编译进行中'), limit};
  return {id: 'compile', label: t('LaTeX 编译成功'), status: 'failed', detail: build.error || t('编译结束状态：{0}', build.status), limit};
}

export function buildEvidenceClaims(input: EvidenceInput): EvidenceClaim[] {
  const current = input.records.filter(record => record.state_revision && record.state_revision === input.project?.state_revision);
  const tools = toolEvidence(input.run);
  const structure = current.find(record => record.kind === 'structure_and_citations' || record.kind === 'post_edit_structure');
  const math = [
    ...current.filter(record => record.kind === 'symbolic_math').map(record => mathStatus(result(record).status)),
    ...tools.filter(item => item.tool === 'math_check').map(item => mathStatus(item.result?.status)),
  ];
  const references = [
    ...current.filter(record => record.kind === 'reference_metadata').map(record => referenceStatus(result(record).status)),
    ...tools.filter(item => item.tool === 'lookup_reference').map(item => referenceStatus(item.result?.status)),
  ];
  const count = (statuses: EvidenceStatus[], what: string) => statuses.length ? t('{0} 条{1}，{2} 条通过', statuses.length, what, statuses.filter(item => item === 'verified').length) : t('当前版本没有{0}', what);
  return [
    modelClaim(input.run),
    appliedClaim(input.proposals, input.selectedProposalId),
    compileClaim(input.build, input.buildHistorical),
    {id: 'structure', label: t('结构与引用静态检查'), status: structure ? structureStatus(result(structure).status) : 'unchecked', detail: structure ? t('检查结果：{0}', toolStatusLabel(result(structure).status)) : t('当前版本尚未检查'), limit: t('静态检查不展开宏，也不核对内容')},
    {id: 'math', label: t('数学工具检查通过'), status: fold(math), detail: count(math, t('有限符号检查')), limit: t('有限符号等价不等于证明，需人工判断假设与适用范围')},
    {id: 'literature', label: t('文献元数据匹配'), status: fold(references), detail: count(references, t('元数据核验')), limit: t('元数据存在不代表文献内容支持论文中的结论')},
    {id: 'human', label: t('科研结论经人工审阅'), status: 'human', detail: t('系统不记录也不代替结论验收，请由作者判断'), limit: t('任何自动检查都不能把它标记为通过')},
  ];
}

const recordKinds: Record<string, string> = {
  research_verification: t('独立验证'), post_edit_structure: t('修改后结构检查'), structure_and_citations: t('结构与引用检查'),
  symbolic_math: t('有限符号检查'), reference_metadata: t('文献元数据核验'), domain_tool_evidence: t('任务工具记录'),
  context_snapshot: t('冻结上下文'), proposal_created: t('提案创建'), submission_rejected: t('任务被拒绝'),
};
export const recordKindLabel = (kind: string) => recordKinds[kind] || kind;

/** The status a stored record supports. Records that verify nothing by themselves stay unchecked. */
export function recordStatus(record: VerificationRecord): EvidenceStatus {
  const value = result(record), status = value.status;
  switch (record.kind) {
    case 'symbolic_math': return mathStatus(status);
    case 'reference_metadata': return referenceStatus(status);
    case 'structure_and_citations': case 'post_edit_structure': return structureStatus(status);
    case 'submission_rejected': return 'failed';
    case 'research_verification': {
      const verification = (record.verification || {}) as Record<string, unknown>;
      if (value.verification_status === 'failed' || verification.status === 'failed') return 'failed';
      if (verification.status === 'human_review_required') return 'human';
      return verification.status === 'partial' ? 'uncertain' : 'unchecked';
    }
    default: return 'unchecked';
  }
}
export const recordIsCurrent = (record: VerificationRecord, project?: Project) => Boolean(record.state_revision && record.state_revision === project?.state_revision && (!record.main_file || record.main_file === project?.main_file));
