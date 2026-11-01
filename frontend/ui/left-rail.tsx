import { t } from '../i18n.js';
import { useMemo, useState } from 'react';
import { action, Field, IconButton, useDismiss, usePreference } from './common.js';
import { X, Camera, ChevronRight, FilePlus2, Files as FilesIcon, fileIcon, Folder, FolderOpen, Heading, History as HistoryIcon, Library, ListTree, RefreshCw, Search, Sigma, Upload } from './icons.js';
import type { Props } from './common.js';
import { fileTree, readOutline, short } from '../models/project-model.js';
import type { TreeNode } from '../models/project-model.js';

const sizeLabel = (size: number) => size < 1024 ? `${size} B` : size < 1048576 ? `${(size / 1024).toFixed(1)} KB` : `${(size / 1048576).toFixed(1)} MB`;

function FileNode({node, depth, collapsed, toggle, workspace, state}: Props & {node: TreeNode; depth: number; collapsed: string[]; toggle(path: string): void}) {
  const pad = {paddingInlineStart: 8 + depth * 13};
  if (!node.file) {
    const closed = collapsed.includes(node.path);
    return <li>
      <button className="tree-row tree-dir" style={pad} aria-expanded={!closed} onClick={() => toggle(node.path)}><ChevronRight className="icon caret" data-open={!closed} aria-hidden="true"/>{closed ? <Folder className="icon" aria-hidden="true"/> : <FolderOpen className="icon" aria-hidden="true"/>}<span className="tree-name">{node.name}</span></button>
      {!closed && <ul>{node.children.map(child => <FileNode key={child.path} {...{node: child, depth: depth + 1, collapsed, toggle, workspace, state}}/>)}</ul>}
    </li>;
  }
  const active = state.file?.path === node.path, draft = state.draftPaths.includes(node.path) || (active && state.dirty);
  const entry = state.project?.main_file === node.path;
  const Icon = fileIcon(node.path);
  return <li>
    <button className="tree-row tree-file" style={pad} data-active={active || undefined} data-asset={!node.file.text || undefined} aria-current={active ? 'true' : undefined} title={`${node.path} · ${sizeLabel(node.file.size)}`} onClick={() => void workspace.perform(() => workspace.openFile(node.path))}>
      <Icon className="icon" aria-hidden="true"/><span className="tree-name">{node.name}</span>
      {draft && <span className="dirty-dot" role="img" aria-label={t('有未保存修改')}/>}
      {entry && <span className="tag">{t('入口')}</span>}
      {!node.file.text && <span className="tag">{t('资源')}</span>}
    </button>
  </li>;
}

function Files({workspace, state}: Props) {
  const [collapsed, setCollapsed] = usePreference<string[]>('collapsed-dirs', []);
  const tree = useMemo(() => fileTree(state.files), [state.files]);
  const [adding, setAdding] = useState(false);
  const addRef = useDismiss<HTMLDivElement>(adding, () => setAdding(false));
  const toggle = (path: string) => setCollapsed(values => values.includes(path) ? values.filter(item => item !== path) : [...values, path]);
  return <fieldset className="rail-body" disabled={state.busy}>
    <div className="rail-head"><span>{t('项目文件')}</span>
      <div className="rail-add" ref={addRef}>
        <IconButton id="rail-add" icon={FilePlus2} label={t('新建文件')} aria-expanded={adding} onClick={() => setAdding(!adding)}/>
        <div className="widget glass add-widget" role="dialog" aria-label={t('新建文件')} hidden={!adding}>
          <label htmlFor="new-path">{t('文件路径')}</label>
          <div className="input-row"><Field workspace={workspace} state={state} name="newPath" id="new-path" placeholder="sections/method.tex" spellCheck={false}/><button id="new-file" className="primary" disabled={!state.form.newPath} onClick={() => { setAdding(false); void workspace.perform(() => workspace.newFile()); }}>{t('新建')}</button></div>
        </div>
      </div>
      <label className="file-button icon-button" data-tip={t('上传资源')} aria-label={t('上传资源')}><Upload className="icon" aria-hidden="true"/><input id="upload-asset" type="file" onChange={event => { const file = event.currentTarget.files?.[0]; event.currentTarget.value = ''; if (file) void workspace.perform(() => workspace.uploadAsset(file)); }}/></label>
    </div>
    <ul className="tree" aria-label={t('项目文件')}>{tree.map(node => <FileNode key={node.path} {...{node, depth: 0, collapsed, toggle, workspace, state}}/>)}
      {state.file && !state.file.sha256 && <li><span className="tree-row tree-file" data-active="true"><span className="tree-name">{state.file.path}</span><span className="tag">{t('未保存')}</span></span></li>}
    </ul>
  </fieldset>;
}

function Outline({workspace, state}: Props) {
  const outline = useMemo(() => readOutline(state.index), [state.index]);
  return <div className="rail-body">
    {state.indexNotice && <p className="pane-note" role="alert">{state.indexNotice}</p>}
    {!state.index && !state.indexNotice && <p className="pane-note">{t('正在读取结构…')}</p>}
    {state.index != null && !outline.entries.length && <p className="pane-note">{t('未找到章节或定理环境。')}</p>}
    <ol className="outline" aria-label={t('论文结构')}>{outline.entries.map(entry => <li key={entry.id}>
      <button className="tree-row outline-row" data-kind={entry.kind === 'section' ? 'section' : 'claim'} data-depth={entry.depth} style={{paddingInlineStart: 8 + entry.depth * 13}} title={`${entry.file}:${entry.line}`} onClick={() => void workspace.perform(() => workspace.openAt(entry.file, entry.line))}>{entry.kind === 'section' ? <Heading className="icon" aria-hidden="true"/> : <Sigma className="icon" aria-hidden="true"/>}<span className="tree-name">{entry.title}</span><span className="line-ref">{entry.line}</span></button>
    </li>)}</ol>
    {outline.diagnostics.length > 0 && <div className="rail-section"><h3>{t('结构提示')}</h3><ul className="plain">{outline.diagnostics.slice(0, 20).map((item, index) => <li key={index}><button className="link" onClick={() => void workspace.perform(() => workspace.openAt(item.file, item.line))}>{item.file}:{item.line}</button> {item.message}</li>)}</ul></div>}
    {state.index != null && <p className="rail-foot">{t('大纲来自已保存版本的静态索引，不展开宏定义。')}</p>}
  </div>;
}

function References({workspace, state, onLookup}: Props & {onLookup(key: string): void}) {
  const outline = useMemo(() => readOutline(state.index), [state.index]);
  return <div className="rail-body">
    {state.index != null && !outline.bibliography.length && !outline.unresolved.length && <p className="pane-note">{t('项目中没有参考文献条目。')}</p>}
    {outline.unresolved.length > 0 && <div className="rail-section"><h3>{t('未解析的引用')}</h3><ul className="plain">{outline.unresolved.map((item, index) => <li key={index}><button className="link" onClick={() => void workspace.perform(() => workspace.openAt(item.file, item.line))}>{item.key}</button> <span className="muted">{item.file}:{item.line}</span></li>)}</ul></div>}
    {outline.bibliography.length > 0 && <ul className="bib-list" aria-label={t('参考文献条目')}>{outline.bibliography.map(entry => <li key={`${entry.file}-${entry.key}`}>
      <button className="bib-entry" onClick={() => void workspace.perform(() => workspace.openAt(entry.file, entry.line))}><span className="bib-key">{entry.key}</span>{!entry.cited && <span className="tag">{t('未引用')}</span>}{entry.title && <span className="bib-title">{entry.title}</span>}</button>
      <IconButton icon={Search} label={t('检索 {0}', entry.key)} tip={t('检索此条目')} className="tip-end" onClick={() => onLookup(entry.key)}/>
    </li>)}</ul>}
  </div>;
}

function History({workspace, state}: Props) {
  return <fieldset className="rail-body" disabled={state.busy}>
    <div className="rail-form">
      <label htmlFor="snapshot-message">{t('保存快照')}</label>
      <div className="input-row"><Field workspace={workspace} state={state} name="snapshotMessage" id="snapshot-message" placeholder={t('说明此版本')}/><button id="snapshot" onClick={action(workspace, () => workspace.snapshot())}><Camera className="icon" aria-hidden="true"/>{t('保存')}</button></div>
      <button id="history" onClick={action(workspace, () => workspace.history())}><HistoryIcon className="icon" aria-hidden="true"/>{t('读取版本历史')}</button>
    </div>
    <div id="history-output" className="history-list">
      {state.historyOutput != null && <p className="pane-note">{t('快照已保存。')}</p>}
      {state.history.map(item => <p key={item.commit} className="history-row"><span className="history-text"><span>{item.message}</span><code>{short(item.commit, 10)}</code></span><button onClick={action(workspace, () => workspace.restore(item))}><RefreshCw className="icon" aria-hidden="true"/>{t('恢复')}</button></p>)}
    </div>
  </fieldset>;
}

const tabs = [['files', t('文件'), FilesIcon], ['outline', t('大纲'), ListTree], ['refs', t('文献'), Library], ['history', t('版本'), HistoryIcon]] as const;
export function LeftRail({workspace, state, onLookup, drawer = false, open = true, railRef, onClose}: Props & {onLookup(key: string): void; drawer?: boolean; open?: boolean; railRef?: React.Ref<HTMLElement>; onClose?(): void}) {
  const [tab, setTab] = usePreference<(typeof tabs)[number][0]>('rail-tab', 'files');
  // As a drawer, opening a place in the paper is the end of the errand, so it steps aside.
  const picked = (event: React.MouseEvent) => { if (drawer && (event.target as Element).closest('.tree-file, .outline-row, .bib-entry')) onClose?.(); };
  return <nav ref={railRef} className={drawer ? 'rail glass' : 'rail'} data-drawer={drawer || undefined} data-open={open} inert={drawer && !open} aria-label={t('项目导航')} onClickCapture={picked}>
    <div className="tabs" role="tablist" aria-label={t('导航类别')}>{tabs.map(([id, label, Icon]) => <button key={id} id={`rail-tab-${id}`} role="tab" aria-selected={tab === id} aria-controls={`rail-${id}`} aria-label={label} data-tip={tab === id ? undefined : label} onClick={() => setTab(id)}><Icon className="icon" aria-hidden="true"/><span className="tab-label">{label}</span></button>)}{drawer && <IconButton id="close-rail" icon={X} label={t('关闭项目导航')} tip={t('关闭（Esc）')} className="tip-end" onClick={onClose}/>}</div>
    <div id="rail-files" role="tabpanel" aria-labelledby="rail-tab-files" className="rail-panel" hidden={tab !== 'files'}><Files workspace={workspace} state={state}/></div>
    <div id="rail-outline" role="tabpanel" aria-labelledby="rail-tab-outline" className="rail-panel" hidden={tab !== 'outline'}><Outline workspace={workspace} state={state}/></div>
    <div id="rail-refs" role="tabpanel" aria-labelledby="rail-tab-refs" className="rail-panel" hidden={tab !== 'refs'}><References workspace={workspace} state={state} onLookup={onLookup}/></div>
    <div id="rail-history" role="tabpanel" aria-labelledby="rail-tab-history" className="rail-panel" hidden={tab !== 'history'}><History workspace={workspace} state={state}/></div>
  </nav>;
}
