import { t } from './i18n.js';
import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { createRoot } from 'react-dom/client';
import { locale, switchLocale } from './i18n.js';
import { Workspace } from './workspace.js';
import { action, Field, IconButton, Splitter, useDismiss, useMedia, usePreference } from './ui/common.js';
import { PanelLeft, ChevronDown, CodeXml, FileDiff, FilePen, FlaskConical, FolderOpen, FolderPlus, Import, Keyboard, Languages, LoaderCircle, LocateFixed, Play, Redo2, Save, SaveAll, Search, ShieldCheck, SunMoon, Trash2, Undo2, Upload, Workflow as WorkflowIcon, X } from './ui/icons.js';
import type { Props } from './ui/common.js';
import { SourceEditor } from './ui/source-editor.js';
import type { EditorApi } from './ui/source-editor.js';
import { PdfViewer } from './ui/pdf-viewer.js';
import { LeftRail } from './ui/left-rail.js';
import { ReviewPane } from './ui/diff-review.js';
import { Workflow } from './ui/workflow.js';
import { EvidencePane } from './ui/evidence.js';
import { Assistant } from './ui/assistant.js';
import { BuildDock, buildStates } from './ui/build-dock.js';
import { short } from './models/project-model.js';

type CenterView = 'source' | 'review' | 'workflow' | 'evidence';

function ProjectPanel({workspace, state, onClose}: Props & {onClose(): void}) {
  return <section id="project-panel" className={state.project ? 'widget glass project-popover' : 'project-home'} aria-label={t('项目')}>
    {!state.project && <header className="home-head"><img src="/favicon.svg" alt="" width="56" height="56"/><h1>Lemfolio</h1><p>{t('论文写作、编译与科研证据的本地工作台')}</p></header>}
    <fieldset disabled={state.busy || !state.ready}>
      {!state.project && state.projects.length > 0 && <ul className="home-projects" aria-label={t('最近项目')}>{state.projects.map(project => <li key={project.id}><button className="home-project" onClick={action(workspace, () => workspace.openProject(project.id))}><FolderOpen className="icon" aria-hidden="true"/><span>{project.name}</span><code>{short(project.revision, 8)}</code></button></li>)}</ul>}
      <div className="project-form">
        <label htmlFor="projects" hidden={!state.project}>{t('切换项目')}</label>
        <div className="input-row" hidden={!state.project && state.projects.length > 0 ? true : !state.projects.length}><select id="projects" value={state.form.projectId} onChange={event => workspace.form('projectId', event.currentTarget.value)}>{state.projects.map(project => <option key={project.id} value={project.id}>{project.name}</option>)}</select><button id="open-project" onClick={() => { void workspace.perform(() => workspace.openProject()); onClose(); }}><FolderOpen className="icon" aria-hidden="true"/>{t('打开')}</button></div>
        <label htmlFor="project-name">{t('新建项目')}</label>
        <div className="input-row"><Field workspace={workspace} state={state} name="projectName" id="project-name" placeholder={t('论文名称')}/><button id="create-project" disabled={!state.form.projectName.trim()} onClick={() => { void workspace.perform(() => workspace.createProject()); onClose(); }}><FolderPlus className="icon" aria-hidden="true"/>{t('新建')}</button></div>
        <label className="file-button"><Import className="icon" aria-hidden="true"/>{t('导入 ZIP')}<input id="import-zip" type="file" accept=".zip" onChange={event => { const file = event.currentTarget.files?.[0]; event.currentTarget.value = ''; if (file) { void workspace.perform(() => workspace.importProject(file)); onClose(); } }}/></label>
        <a id="export" className="tool-link" hidden={!state.project} href={state.project ? `/api/projects/${state.project.id}/export` : undefined}><Upload className="icon" aria-hidden="true"/>{t('导出 ZIP')}</a>
      </div>
    </fieldset>
  </section>;
}

function Topbar({workspace, state, projectMenu, setProjectMenu, assistant, setAssistant, compact, railOpen, setRailOpen}: Props & {projectMenu: boolean; setProjectMenu(next: boolean): void; assistant: boolean; setAssistant(next: boolean): void; compact: boolean; railOpen: boolean; setRailOpen(next: boolean): void}) {
  const {project, build} = state;
  const dirtyCount = state.draftPaths.length + (state.dirty ? 1 : 0);
  const saveLabel = state.saving ? t('保存中…') : dirtyCount > 1 ? t('{0} 个文件未保存', dirtyCount) : dirtyCount ? t('未保存') : t('已保存');
  const compileLabel = build ? `${state.buildHistorical ? t('历史 · ') : ''}${buildStates[build.status] || build.status}` : state.buildNotice ? t('需要重新编译') : t('尚未编译');
  const busyBuild = build?.status === 'queued' || build?.status === 'running';
  return <header className="topbar">
    <span className="brand"><img src="/favicon.svg" alt="" width="22" height="22"/><span className="wordmark">Lemfolio</span></span>
    {project && <>
      {compact && <IconButton id="toggle-rail" icon={PanelLeft} label={t('项目导航')} tip={t('项目导航（Ctrl+B）')} aria-expanded={railOpen} aria-controls="rail-nav" onPointerDown={event => event.stopPropagation()} onClick={() => setRailOpen(!railOpen)}/>}
      <button id="project-switch" className="project-name" aria-expanded={projectMenu} onClick={() => setProjectMenu(!projectMenu)}><FolderOpen className="icon" aria-hidden="true"/><span className="project-label">{project.name}</span><ChevronDown className="icon" aria-hidden="true"/></button>
      <span id="save-state" className="top-state" data-state={state.saving ? 'saving' : dirtyCount ? 'dirty' : 'saved'} role="status">{saveLabel}</span>
      <span id="compile-state" className="top-state" data-build={build?.status || 'none'} data-historical={state.buildHistorical || undefined}>{compileLabel}</span>
      <span id="revision" className="revision" title={`revision ${project.revision}\nstate ${project.state_revision}`}>{t('版本')} {project.revision.slice(0, 12)} {t('· 状态')} {project.state_revision.slice(0, 12)}</span>
      <span className="top-spacer"/>
      <fieldset className="compile-group" disabled={state.busy}>
        {state.draftPaths.length + (state.dirty ? 1 : 0) > 1 && <button id="save-all" onClick={action(workspace, () => workspace.saveAll())}><SaveAll className="icon" aria-hidden="true"/>{t('全部保存')}</button>}
        <select id="engine" aria-label={t('TeX 引擎')} value={state.form.engine} onChange={event => workspace.form('engine', event.currentTarget.value)}><option>pdflatex</option><option>xelatex</option><option>lualatex</option></select>
        <button id="compile" className="primary" disabled={busyBuild} title="Ctrl+Enter" onClick={action(workspace, () => workspace.compile())}>{busyBuild ? <LoaderCircle className="icon spin" aria-hidden="true"/> : <Play className="icon" aria-hidden="true"/>}<span className="button-label">{busyBuild ? t('编译中…') : t('编译')}</span></button>
      </fieldset>
      <button id="toggle-assistant" aria-label={t('研究助手')} aria-expanded={assistant} title="Ctrl+Shift+A" onClick={() => setAssistant(!assistant)}><FlaskConical className="icon" aria-hidden="true"/><span className="button-label">{t('研究助手')}</span></button>
    </>}
  </header>;
}

function nextTheme() {
  const root = document.documentElement, dark = root.dataset.theme ? root.dataset.theme === 'dark' : matchMedia('(prefers-color-scheme: dark)').matches;
  root.dataset.theme = dark ? 'light' : 'dark';
  try { localStorage.setItem('lemfolio.theme', root.dataset.theme); } catch { /* applies to this page only */ }
}
function StatusBar({state, cursorRef, onHelp}: {state: Props['state']; cursorRef: React.RefObject<HTMLSpanElement | null>; onHelp(): void}) {
  return <footer className="statusbar">
    <p id="message" role="status" aria-live="polite" data-error={/error|failed|refused|mismatch|incompatible|失败|错误|不匹配|不兼容/i.test(state.message) || undefined} title={state.message}>{state.message}</p>
    <span className="top-spacer"/>
    {state.project && <>
      <span ref={cursorRef} id="cursor-position"/>
      <span className="status-extra">{state.project.main_file}</span>
      <span className="status-extra">{state.form.engine}</span>
    </>}
    <button id="toggle-language" className="status-button" lang={locale === 'zh' ? 'en' : 'zh-CN'} disabled={state.dirty || state.draftPaths.length > 0} title={state.dirty || state.draftPaths.length > 0 ? t('请先保存修改再切换语言') : undefined} onClick={switchLocale}><Languages className="icon" aria-hidden="true"/><span className="button-label">{locale === 'zh' ? 'English' : t('简体中文')}</span></button>
    <button id="toggle-theme" className="status-button" onClick={nextTheme}><SunMoon className="icon" aria-hidden="true"/><span className="button-label">{t('深浅色')}</span></button>
    <button className="status-button" onClick={onHelp}><Keyboard className="icon" aria-hidden="true"/><span className="button-label">{t('快捷键')}</span></button>
  </footer>;
}

const shortcuts = [
  [t('Esc 后按 Tab'), t('从编辑器移开键盘焦点')], ['Ctrl+S', t('保存当前文件')], ['Ctrl+Enter', t('编译')], ['Ctrl+F', t('查找与替换')], ['Ctrl+Z / Ctrl+Shift+Z', t('撤销 / 重做')], ['Ctrl+Alt+J', t('从光标定位到 PDF')],
  [t('双击 PDF'), t('定位回源码')], ['Ctrl+J', t('显示或隐藏编译日志')], ['Ctrl+Shift+A', t('显示或隐藏研究助手')], ['Ctrl+B', t('窄窗口下显示或隐藏项目导航')], ['PageUp / PageDown', t('PDF 翻页（焦点在 PDF 时）')], [t('Ctrl+滚轮'), t('PDF 缩放')],
];
function HelpDialog({onClose}: {onClose(): void}) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => { const element = dialog.current; if (element && !element.open && element.showModal) element.showModal(); }, []);
  return <dialog ref={dialog} className="help glass" aria-label={t('快捷键')} onClose={onClose}><h2>{t('快捷键')}</h2><dl>{shortcuts.map(([key, label]) => <div key={key}><dt><kbd>{key}</kbd></dt><dd>{label}</dd></div>)}</dl><form method="dialog"><button autoFocus><X className="icon" aria-hidden="true"/>{t('关闭')}</button></form></dialog>;
}

function App() {
  const state = useSyncExternalStore(workspace.subscribe, workspace.getSnapshot);
  const [center, setCenter] = usePreference<CenterView>('center-view', 'source');
  const [assistant, setAssistant] = usePreference('assistant-open', false);
  const [dockOpen, setDockOpen] = useState(false);
  const dockAutoOpened = useRef(false);
  const [widths, setWidths] = usePreference('pane-widths', {rail: 232, viewer: 560, assistant: 392});
  const [projectMenu, setProjectMenu] = useState(false);
  const [help, setHelp] = useState(false);
  // Below this width the rail becomes a drawer. The same components stay mounted, so a
  // resize never loses the paper's state.
  const compact = useMedia('(max-width: 1180px)');
  const [railOpen, setRailOpen] = useState(false);
  const railRef = useDismiss<HTMLElement>(compact && railOpen, () => setRailOpen(false));
  const projectRef = useDismiss<HTMLDivElement>(Boolean(state.project) && projectMenu, () => setProjectMenu(false));
  const editor = useRef<EditorApi | null>(null);
  const cursorRef = useRef<HTMLSpanElement>(null);
  const stateRef = useRef(state);
  stateRef.current = state;

  useEffect(() => { void workspace.start(); return () => workspace.dispose(); }, []);
  useEffect(() => { const beforeUnload = (event: BeforeUnloadEvent) => { if (state.dirty || state.draftPaths.length) event.preventDefault(); }; window.addEventListener('beforeunload', beforeUnload); return () => window.removeEventListener('beforeunload', beforeUnload); }, [state.dirty, state.draftPaths.length]);
  useEffect(() => { document.title = state.project ? `${state.project.name} — Lemfolio` : 'Lemfolio'; }, [state.project?.name]);
  // A failure is what the user needs next, so the log opens by itself; it floats over the
  // source, so the next success puts it away again unless the user opened it themselves.
  useEffect(() => {
    const status = state.build?.status;
    if (!status || status === 'queued' || status === 'running') return;
    if (status !== 'success') { dockAutoOpened.current = true; setDockOpen(true); }
    else if (dockAutoOpened.current) { dockAutoOpened.current = false; setDockOpen(false); }
  }, [state.build?.id, state.build?.status]);

  // A jump to a source line has to show that line, whichever view is open.
  useEffect(() => { if (state.jump) setCenter('source'); }, [state.jump?.nonce]);
  useEffect(() => { if (!compact) setRailOpen(false); }, [compact]);
  // Opening a drawer moves focus into it; closing it gives focus back to the button that owns it.
  const opened = useRef({assistant, railOpen});
  useEffect(() => {
    const was = opened.current; opened.current = {assistant, railOpen};
    if (assistant && !was.assistant) document.getElementById('intent')?.focus({preventScroll: true});
    else if (!assistant && was.assistant && (document.activeElement === document.body || document.activeElement?.closest('.assistant-host'))) document.getElementById('toggle-assistant')?.focus();
    if (railOpen && !was.railOpen) document.querySelector<HTMLElement>('.rail [role="tab"][aria-selected="true"]')?.focus({preventScroll: true});
    else if (!railOpen && was.railOpen && (document.activeElement === document.body || document.activeElement?.closest('.rail'))) document.getElementById('toggle-rail')?.focus();
  }, [assistant, railOpen]);

  const forwardSync = useCallback(() => {
    const cursor = editor.current?.cursor();
    if (cursor) workspace.background(() => workspace.synctex(cursor.line, cursor.column));
  }, []);
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (!(event.ctrlKey || event.metaKey) || !stateRef.current.project) return;
      const key = event.key.toLowerCase();
      if (key === 's' && !event.shiftKey) { event.preventDefault(); if (stateRef.current.file) void workspace.perform(() => workspace.saveFile()); }
      else if (key === 'enter') { event.preventDefault(); void workspace.perform(() => workspace.compile()); }
      else if (key === 'j' && event.altKey) { event.preventDefault(); forwardSync(); }
      else if (key === 'j') { event.preventDefault(); dockAutoOpened.current = false; setDockOpen(open => !open); }
      else if (key === 'a' && event.shiftKey) { event.preventDefault(); setAssistant(open => !open); }
      else if (key === 'b' && !event.shiftKey && matchMedia('(max-width: 1180px)').matches) { event.preventDefault(); setRailOpen(open => !open); }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [forwardSync]);

  const setCursor = useCallback((line: number, column: number, selected: number) => {
    if (cursorRef.current) cursorRef.current.textContent = t('第 {0} 行，第 {1} 列{2}', line, column, selected ? t(' · 已选 {0} 字符', selected) : '');
  }, []);
  const onEdit = useCallback((source: string) => workspace.edit(source), []);
  const onSelect = useCallback((start: number, end: number) => workspace.select(start, end), []);
  const onInverse = useCallback((page: number, x: number, y: number) => { setCenter('source'); workspace.background(() => workspace.synctexInverse(page, x, y)); }, []);
  const resize = (pane: 'rail' | 'viewer' | 'assistant', delta: number) => setWidths(values => ({...values, [pane]: Math.min(pane === 'viewer' ? 1200 : pane === 'assistant' ? 720 : 560, Math.max(pane === 'viewer' ? 280 : pane === 'assistant' ? 320 : 180, values[pane] + delta))}));
  const onLookup = useCallback((key: string) => { workspace.form('mode', 'literature'); workspace.form('literatureQuery', key); setAssistant(true); }, []);
  const review = useCallback(() => setCenter('review'), []);
  const evidence = useCallback(() => setCenter('evidence'), []);

  const {build, file} = state;
  const pending = state.proposals.filter(proposal => proposal.status === 'pending').length;
  const pdf = build?.status === 'success' ? build.artifacts.find(artifact => artifact.name.endsWith('.pdf')) : undefined;
  const pdfUrl = pdf && build ? `/api/builds/${build.id}/artifacts/${encodeURIComponent(pdf.name)}` : undefined;
  const syncBlocked = !pdf ? t('需要一次成功的编译') : state.buildHistorical ? t('历史编译不能用于定位') : state.dirty || state.draftPaths.length ? t('请先保存并重新编译') : '';
  const columns = compact ? `minmax(0, 1fr) 6px minmax(280px, min(${widths.viewer}px, 46%))` : `${widths.rail}px 6px minmax(380px, 1fr) 6px minmax(280px, ${widths.viewer}px)`;
  const centerTabs: [CenterView, string, typeof CodeXml][] = [['source', t('源码'), CodeXml], ['review', pending ? t('审阅 · {0}', pending) : t('审阅'), FileDiff], ['workflow', t('流程'), WorkflowIcon], ['evidence', t('证据'), ShieldCheck]];

  return <div className="workbench" data-project={Boolean(state.project)}>
    <Topbar workspace={workspace} state={state} projectMenu={projectMenu} setProjectMenu={setProjectMenu} assistant={assistant} setAssistant={setAssistant} compact={compact} railOpen={railOpen} setRailOpen={setRailOpen}/>
    <div className="project-host" ref={projectRef} hidden={Boolean(state.project) && !projectMenu}><ProjectPanel workspace={workspace} state={state} onClose={() => setProjectMenu(false)}/></div>
    <main id="workspace" className="columns" hidden={!state.project} style={{gridTemplateColumns: columns}}>
      <LeftRail workspace={workspace} state={state} onLookup={onLookup} drawer={compact} open={railOpen} railRef={railRef} onClose={() => setRailOpen(false)}/>
      {!compact && <Splitter label={t('调整导航宽度')} value={widths.rail} onResize={delta => resize('rail', delta)}/>}
      <div className="center">
        <div className="tabs center-tabs" role="tablist" aria-label={t('主视图')}>{centerTabs.map(([id, label, Icon]) => <button key={id} id={`view-${id}`} role="tab" aria-selected={center === id} aria-controls={`pane-${id}`} data-attention={id === 'review' && pending > 0 || undefined} onClick={() => setCenter(id)}><Icon className="icon" aria-hidden="true"/>{label}</button>)}</div>
        <div className="center-panes">
          <section id="pane-source" className="source-pane" role="tabpanel" aria-labelledby="view-source" hidden={center !== 'source'}>
            <fieldset className="toolbar source-toolbar" disabled={state.busy}>
              <span id="file-path" className="breadcrumb" title={file?.path}>{file?.path || t('未打开文件')}</span>
              <span className="toolbar-gap"/>
              <IconButton id="save-file" icon={Save} label={t('保存')} tip={t('保存（Ctrl+S）')} disabled={!file} onClick={action(workspace, () => workspace.saveFile())}/>
              <IconButton id="undo" icon={Undo2} label={t('撤销')} tip={t('撤销（Ctrl+Z）')} disabled={!file} onClick={() => editor.current?.undo()}/>
              <IconButton id="redo" icon={Redo2} label={t('重做')} tip={t('重做（Ctrl+Shift+Z）')} disabled={!file} onClick={() => editor.current?.redo()}/>
              <IconButton id="find" icon={Search} label={t('查找与替换')} tip={t('查找与替换（Ctrl+F）')} disabled={!file} onClick={() => editor.current?.openSearch()}/>
              <IconButton id="synctex" icon={state.synctexBusy ? LoaderCircle : LocateFixed} label={t('定位到 PDF')} tip={syncBlocked || (state.synctexBusy ? t('定位中…') : t('定位到 PDF（Ctrl+Alt+J）'))} disabled={!file || Boolean(syncBlocked) || state.synctexBusy} onClick={forwardSync}/>
              <IconButton id="propose-edit" icon={FilePen} label={t('提交为提案')} tip={t('提交为提案，审阅后再应用')} disabled={!file} onClick={() => { void workspace.perform(async () => { await workspace.proposeEdit(); setCenter('review'); }); }}/>
              <IconButton id="delete-file" icon={Trash2} label={t('删除文件')} className="tip-end danger" disabled={!file} onClick={action(workspace, () => workspace.deleteFile())}/>
            </fieldset>
            <SourceEditor path={file?.path || null} source={state.source} jump={state.jump} label={file ? t('{0} 的源码', file.path) : t('未打开文件')} onEdit={onEdit} onSelect={onSelect} onCursor={setCursor} apiRef={editor}/>
          </section>
          <div id="pane-review" className="pane-host" role="tabpanel" aria-labelledby="view-review" hidden={center !== 'review'}><ReviewPane workspace={workspace} state={state}/></div>
          <div id="pane-workflow" className="pane-host" role="tabpanel" aria-labelledby="view-workflow" hidden={center !== 'workflow'}><Workflow workspace={workspace} state={state}/></div>
          <div id="pane-evidence" className="pane-host" role="tabpanel" aria-labelledby="view-evidence" hidden={center !== 'evidence'}><EvidencePane workspace={workspace} state={state}/></div>
        </div>
        <BuildDock workspace={workspace} state={state} open={dockOpen} onToggle={() => { dockAutoOpened.current = false; setDockOpen(!dockOpen); }}/>
      </div>
      <Splitter label={t('调整 PDF 宽度')} value={widths.viewer} onResize={delta => resize('viewer', -delta)}/>
      <section className="viewer" aria-label={t('PDF 预览')}>
        <section id="pdf" hidden={!pdf} data-src={pdfUrl} className="pdf-host">
          {pdfUrl && build && <PdfViewer url={pdfUrl} documentKey={build.id} target={state.pdfTarget?.buildId === build.id ? state.pdfTarget : null} syncHint={syncBlocked || t('双击页面定位回源码')} onInverse={onInverse} downloadName={pdf?.name || 'paper.pdf'}/>}
        </section>
        {pdf && <p id="pdf-identity" className="pdf-identity" data-historical={state.buildHistorical || undefined}>{state.buildHistorical ? t('历史编译 · ') : ''}{build?.id.slice(0, 8)} · {build?.engine} · {state.synctexNotice || (state.dirty || state.draftPaths.length ? t('编辑器有未保存修改，此 PDF 对应已保存版本') : syncBlocked || t('双击页面定位回源码'))}</p>}
        {!pdf && <div className="viewer-empty">
          {build && ['queued', 'running'].includes(build.status) ? <p>{t('正在编译…')}</p>
            : build ? <><p>{buildStates[build.status] || build.status}{t('，没有可显示的 PDF。')}</p><p className="muted">{t('旧的预览已清除。错误信息见编译日志。')}</p></>
            : <><p>{state.buildNotice || t('尚未编译')}</p><p className="muted">{t('按 Ctrl+Enter 编译当前已保存的版本。')}</p></>}
        </div>}
      </section>
      <div className="assistant-host glass" data-open={assistant} inert={!assistant} style={{'--drawer-width': `${widths.assistant}px`} as React.CSSProperties} onKeyDown={event => { if (event.key === 'Escape' && !event.defaultPrevented) setAssistant(false); }}>
        <Splitter label={t('调整研究助手宽度')} value={widths.assistant} onResize={delta => resize('assistant', -delta)}/>
        <Assistant workspace={workspace} state={state} onClose={() => setAssistant(false)} onReview={review} onEvidence={evidence}/>
      </div>
    </main>
    <StatusBar state={state} cursorRef={cursorRef} onHelp={() => setHelp(true)}/>
    {help && <HelpDialog onClose={() => setHelp(false)}/>}
  </div>;
}

// The theme follows the system unless the viewer has chosen one; storage may be unavailable.
try { const theme = localStorage.getItem('lemfolio.theme'); if (theme === 'light' || theme === 'dark') document.documentElement.dataset.theme = theme; } catch { /* follows the system */ }
document.documentElement.lang = locale === 'zh' ? 'zh-CN' : 'en';
const workspace = new Workspace();
const root = document.getElementById('root');
if (!root) throw new Error('WebUI root is missing');
createRoot(root).render(<App/>);
