import { t } from '../i18n.js';
import { useMemo } from 'react';
import { action, Field, IconButton } from './common.js';
import { ChevronRight, ScrollText, Search, Square, X } from './icons.js';
import type { Props } from './common.js';
import { parseLatexLog } from '../models/latex-log.js';
import { short } from '../models/project-model.js';

export const buildStates: Record<string, string> = {queued: t('排队中'), running: t('编译中'), success: t('编译成功'), failed: t('编译失败'), cancelled: t('已取消'), interrupted: t('已中断'), timeout: t('超时'), output_limit: t('输出超限'), unavailable: t('编译器不可用')};

/** Compile log, located problems and build history. Everything here describes the selected receipt only. */
export function BuildDock({workspace, state, open, onToggle}: Props & {open: boolean; onToggle(): void}) {
  const build = state.build;
  const problems = useMemo(() => parseLatexLog(build?.log), [build?.log]);
  const errors = problems.filter(item => item.severity === 'error').length;
  const url = (name: string) => `/api/builds/${build?.id}/artifacts/${encodeURIComponent(name)}`;
  return <section className="dock" data-open={open} aria-label={t('编译日志')}>
    <div className="drawer-log glass" data-open={open} inert={!open}>
      <div className="drawer-head"><ScrollText className="icon" aria-hidden="true"/><h2>{t('编译日志')}</h2><IconButton icon={X} label={t('收起编译日志')} tip={t('收起（Ctrl+J）')} className="tip-end" onClick={onToggle}/></div>
    <fieldset className="dock-body" disabled={state.busy}>
      <div className="dock-controls">
        <label htmlFor="main-file">{t('入口')}</label>
        <Field workspace={workspace} state={state} name="mainFile" id="main-file" list="entry-files" spellCheck={false}/>
        <datalist id="entry-files">{state.files.filter(file => /\.(tex|ltx)$/i.test(file.path)).map(file => <option key={file.path} value={file.path}/>)}</datalist>
        <button id="set-main" disabled={state.form.mainFile === state.project?.main_file} onClick={action(workspace, () => workspace.setMain())}>{t('设为入口')}</button>
        <label htmlFor="builds">{t('编译历史')}</label>
        <select id="builds" value={state.form.buildId} onChange={event => workspace.form('buildId', event.currentTarget.value)}>{state.builds.map(item => <option key={item.id} value={item.id}>{item.id.slice(0, 8)} {buildStates[item.status] || item.status} {item.main_file}</option>)}</select>
        <button id="inspect-build" disabled={!state.form.buildId} onClick={action(workspace, () => workspace.inspectBuild())}><Search className="icon" aria-hidden="true"/>{t('查看')}</button>
        <button id="cancel-build" disabled={!build || !['queued', 'running'].includes(build.status)} onClick={action(workspace, () => workspace.cancelBuild())}><Square className="icon" aria-hidden="true"/>{t('取消编译')}</button>
      </div>
      <div id="build-output" className="build-output">
        {!build && <p className="pane-note">{state.buildNotice || t('当前版本还没有编译记录。')}</p>}
        {build && <>
          <p className="build-identity"><strong>{buildStates[build.status] || build.status}</strong> <code>{build.id}</code> <span className="muted">{t('快照')} <code>{short(build.snapshot_id)}</code></span></p>
          {build.error && <p className="conflict" role="alert">{build.error}</p>}
          {problems.length > 0 && <ul className="problems" aria-label={t('编译问题')}>{problems.map((problem, index) => <li key={index} data-severity={problem.severity}>
            <span className="problem-kind">{problem.severity === 'error' ? t('错误') : t('警告')}</span>
            {problem.file && problem.line ? <button className="link" onClick={() => void workspace.perform(() => workspace.openAt(problem.file || '', problem.line || 1))}>{problem.file}:{problem.line}</button> : <span className="muted">{problem.line ? t('输入第 {0} 行', problem.line) : t('位置未知')}</span>}
            <span className="problem-message">{problem.message}</span>
          </li>)}</ul>}
          <div id="artifacts" className="artifacts">{build.artifacts.map(artifact => <a key={artifact.name} href={url(artifact.name)} target="_blank" rel="noopener" title={`SHA-256 ${artifact.sha256}`}>{artifact.name} </a>)}</div>
          {build.log && <details><summary>{t('完整日志')}</summary><pre className="raw-log">{build.log}</pre></details>}
        </>}
      </div>
    </fieldset>
    </div>
    <div className="dock-bar">
      <button id="toggle-dock" className="dock-toggle" aria-expanded={open} onClick={onToggle}><ChevronRight className="icon caret" data-open={open} aria-hidden="true"/><ScrollText className="icon" aria-hidden="true"/>{t('编译日志')}</button>
      <span className="dock-summary muted">{problems.length ? t('{0} 个错误 · {1} 个警告', errors, problems.length - errors) : build ? t('无定位问题') : ''}</span>
      <span id="build-context" className="build-context muted">{build ? t('{0} · {1} · {2} · 状态 {3} · {4}', state.buildHistorical ? t('历史编译（不作为当前证据）') : t('当前编译'), build.main_file, build.engine, build.state_revision?.slice(0, 12) || t('旧格式未绑定'), buildStates[build.status] || build.status) : state.buildNotice}</span>
    </div>
  </section>;
}
