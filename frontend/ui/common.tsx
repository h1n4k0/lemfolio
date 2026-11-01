import { t } from '../i18n.js';
import { translated } from '../i18n.js';
import { useEffect, useRef, useState } from 'react';
import type { LucideIcon } from 'lucide-react';
import { statusIcons } from './icons.js';
import type { FormValues, Workspace, WorkspaceState } from '../workspace.js';
import type { EvidenceStatus } from '../models/evidence-model.js';
import { evidenceLabels } from '../models/evidence-model.js';
import { linkSources } from '../models/project-model.js';

export type Props = { workspace: Workspace; state: WorkspaceState };
export const action = (workspace: Workspace, task: () => Promise<unknown>) => () => { void workspace.perform(task); };

export function Field({workspace, state, name, id, ...props}: Props & {name: keyof FormValues; id: string} & React.InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} id={id} value={state.form[name]} onChange={event => workspace.form(name, event.currentTarget.value)}/>;
}
/** A button whose only visible content is its icon; the name stays available to assistive technology and as a tip. */
export function IconButton({icon: Icon, label, tip, className = '', ...props}: {icon: LucideIcon; label: string; tip?: string} & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return <button {...props} className={`icon-button ${className}`} aria-label={label} data-tip={tip || label}><Icon className="icon" aria-hidden="true"/></button>;
}
/** Closes a floating widget on Escape or on a pointer press outside it. */
export function useDismiss<T extends HTMLElement>(open: boolean, close: () => void) {
  const ref = useRef<T>(null);
  useEffect(() => {
    if (!open) return;
    const press = (event: PointerEvent) => { if (!ref.current?.contains(event.target as Node)) close(); };
    const key = (event: KeyboardEvent) => { if (event.key === 'Escape') { event.stopPropagation(); close(); } };
    document.addEventListener('pointerdown', press); document.addEventListener('keydown', key, true);
    return () => { document.removeEventListener('pointerdown', press); document.removeEventListener('keydown', key, true); };
  }, [open]);
  return ref;
}
export function StatusMark({status, label}: {status: EvidenceStatus; label?: string}) {
  const Icon = statusIcons[status];
  return <span className="status-mark" data-status={status}><Icon className="icon" aria-hidden="true"/>{label || evidenceLabels[status]}</span>;
}
/** Renders a stored record as readable fields instead of raw JSON; all values stay inspectable. */
export function Fields({value, depth = 0}: {value: unknown; depth?: number}) {
  if (value === null || value === undefined) return <span className="muted">—</span>;
  if (typeof value === 'boolean') return <span>{value ? t('是') : t('否')}</span>;
  if (typeof value !== 'object') {
    const text = translated(String(value));
    return text.includes('\n') || text.length > 160 ? <pre className="field-text">{text}</pre> : <span className="field-value">{text}</span>;
  }
  if (Array.isArray(value)) {
    if (!value.length) return <span className="muted">{t('无')}</span>;
    return <ol className="field-list">{value.map((item, index) => <li key={index}><Fields value={item} depth={depth + 1}/></li>)}</ol>;
  }
  const entries = Object.entries(value as Record<string, unknown>);
  if (!entries.length) return <span className="muted">{t('无')}</span>;
  return <dl className="fields">{entries.map(([key, item]) => {
    const nested = item !== null && typeof item === 'object' && (Array.isArray(item) ? item.length > 0 : Object.keys(item).length > 0);
    return <div key={key}><dt>{key}</dt><dd>{nested && depth >= 1 ? <details><summary>{Array.isArray(item) ? t('{0} 项', item.length) : t('展开')}</summary><Fields value={item} depth={depth + 1}/></details> : <Fields value={item} depth={depth + 1}/>}</dd></div>;
  })}</dl>;
}
/** Model prose; only locations of real project files become links. */
export function ModelText({text, workspace, state}: Props & {text: string}) {
  const paths = state.files.map(file => file.path);
  return <div className="model-text">{linkSources(text, paths).map((part, index) => 'path' in part
    ? <button key={index} className="link" onClick={() => void workspace.perform(() => workspace.openAt(part.path, part.line))}>{part.text}</button>
    : <span key={index}>{part.text}</span>)}</div>;
}
export function Splitter({label, onResize, value}: {label: string; onResize(delta: number): void; value: number}) {
  const last = useRef<number | null>(null);
  return <div className="splitter" role="separator" aria-orientation="vertical" aria-label={label} aria-valuenow={Math.round(value)} tabIndex={0}
    onPointerDown={event => { last.current = event.clientX; event.currentTarget.setPointerCapture(event.pointerId); }}
    onPointerMove={event => { if (last.current !== null) { onResize(event.clientX - last.current); last.current = event.clientX; } }}
    onPointerUp={() => { last.current = null; }} onPointerCancel={() => { last.current = null; }}
    onKeyDown={event => { if (event.key === 'ArrowLeft') { event.preventDefault(); onResize(-24); } else if (event.key === 'ArrowRight') { event.preventDefault(); onResize(24); } }}/>;
}
/** Follows a media query, so layout changes can also change what is inert or rendered. */
export function useMedia(query: string) {
  const [matches, setMatches] = useState(() => matchMedia(query).matches);
  useEffect(() => {
    const list = matchMedia(query), change = () => setMatches(list.matches);
    change(); list.addEventListener('change', change);
    return () => list.removeEventListener('change', change);
  }, [query]);
  return matches;
}
/** A per-viewer layout preference. Storage may be unavailable, so it only ever falls back to the default. */
export function usePreference<T>(key: string, initial: T): [T, (next: T | ((value: T) => T)) => void] {
  const [value, setValue] = useState<T>(() => {
    try { const stored = localStorage.getItem('lemfolio.' + key); return stored === null ? initial : JSON.parse(stored) as T; } catch { return initial; }
  });
  useEffect(() => { try { localStorage.setItem('lemfolio.' + key, JSON.stringify(value)); } catch { /* not persisted */ } }, [key, value]);
  return [value, setValue];
}
