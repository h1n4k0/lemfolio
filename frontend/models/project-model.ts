import { t } from '../i18n.js';
// Read-only views over the project file list and the Python structure index.
export interface TreeNode { name: string; path: string; file?: {path: string; text: boolean; size: number}; children: TreeNode[] }

export function fileTree(files: {path: string; text: boolean; size: number}[]): TreeNode[] {
  const root: TreeNode = {name: '', path: '', children: []};
  for (const file of files) {
    let node = root;
    const parts = file.path.split('/');
    parts.forEach((name, index) => {
      const path = parts.slice(0, index + 1).join('/');
      let child = node.children.find(item => item.name === name);
      if (!child) { child = {name, path, children: []}; node.children.push(child); }
      if (index === parts.length - 1) child.file = file;
      node = child;
    });
  }
  const order = (nodes: TreeNode[]): TreeNode[] => nodes
    .sort((a, b) => Number(Boolean(a.file)) - Number(Boolean(b.file)) || a.name.localeCompare(b.name))
    .map(node => ({...node, children: order(node.children)}));
  return order(root.children);
}

export interface OutlineEntry { id: string; kind: string; title: string; file: string; line: number; depth: number }
export interface BibEntry { key: string; kind: string; title: string; file: string; line: number; cited: boolean }
export interface CitationIssue { key: string; file: string; line: number }
export interface ProjectOutline { entries: OutlineEntry[]; bibliography: BibEntry[]; unresolved: CitationIssue[]; diagnostics: {message: string; file: string; line: number; severity: string}[]; limitations: string[] }

const list = (value: unknown): Record<string, unknown>[] => Array.isArray(value) ? value.filter(item => item && typeof item === 'object') as Record<string, unknown>[] : [];
const text = (value: unknown, fallback = '') => typeof value === 'string' ? value : fallback;
const number = (value: unknown) => typeof value === 'number' ? value : 1;
const claimNames: Record<string, string> = {theorem: t('定理'), lemma: t('引理'), definition: t('定义'), proposition: t('命题'), corollary: t('推论'), proof: t('证明'), remark: t('注记'), example: t('例'), axiom: t('公理'), conjecture: t('猜想'), assumption: t('假设'), equation: t('公式')};

/** Reads the untyped /index response defensively; unknown shapes become an empty outline. */
export function readOutline(value: unknown): ProjectOutline {
  const index = (value && typeof value === 'object' ? (value as {index?: unknown}).index : null) as Record<string, unknown> | null;
  if (!index || typeof index !== 'object') return {entries: [], bibliography: [], unresolved: [], diagnostics: [], limitations: []};
  const entries = list(index.nodes).map(node => {
    const kind = text(node.type, 'node'), section = kind === 'section';
    const fallback = claimNames[kind] || text(node.environment, kind);
    const title = text(node.title);
    return {
      id: text(node.id), kind, file: text(node.file), line: number(node.line),
      // Sections show their heading; a claim shows its type, plus its optional title if it has one.
      title: section ? title || t('（无标题）') : title && title !== text(node.environment) && title !== 'display equation' ? `${fallback} · ${title}` : fallback,
      depth: section ? Math.max(0, number(node.level) - 2) : 3,
    };
  }).sort((a, b) => a.file.localeCompare(b.file) || a.line - b.line);
  const citedKeys = new Set(list(index.citations).map(item => text(item.key)));
  const bibKeys = new Set(list(index.bibliography).map(item => text(item.key)));
  const bibliography = list(index.bibliography).map(item => {
    const fields = (item.fields && typeof item.fields === 'object' ? item.fields : {}) as Record<string, unknown>;
    return {key: text(item.key), kind: text(item.entry_type, 'entry'), title: text(item.title) || text(fields.title), file: text(item.file), line: number(item.line), cited: citedKeys.has(text(item.key)) || citedKeys.has('*')};
  });
  const unresolved = list(index.citations).filter(item => text(item.key) !== '*' && !bibKeys.has(text(item.key))).map(item => ({key: text(item.key), file: text(item.file), line: number(item.line)}));
  return {
    entries, bibliography, unresolved,
    diagnostics: list(index.diagnostics).map(item => ({message: text(item.message), file: text(item.file), line: number(item.line), severity: text(item.severity, 'warning')})),
    limitations: Array.isArray(index.limitations) ? index.limitations.filter(item => typeof item === 'string') : [],
  };
}

export interface ProviderView { id: 'codex' | 'claude' | 'api'; name: string; state: 'ready' | 'login' | 'missing' | 'off'; summary: string; hint: string; selectable: boolean }

/** Only reported login state may read as signed in; the API key is never implied or auto-selected. */
export function readProviders(status: unknown): ProviderView[] {
  const providers = (status as {runtime?: {providers?: Record<string, Record<string, unknown>>}} | null)?.runtime?.providers;
  const cli = (id: 'codex' | 'claude', name: string, plan: string): ProviderView => {
    const item = providers?.[id];
    if (!item) return {id, name, state: 'off', summary: t('状态未知'), hint: '', selectable: true};
    if (!item.installed) return {id, name, state: 'missing', summary: t('未安装官方 CLI'), hint: '', selectable: true};
    if (item.authenticated === true) return {id, name, state: 'ready', summary: t('已登录（{0}）', plan), hint: '', selectable: true};
    return {id, name, state: 'login', summary: t('未通过订阅登录'), hint: t('在终端运行 {0}', text(item.login_command)), selectable: true};
  };
  const api = providers?.api;
  return [
    cli('codex', 'Codex CLI', t('ChatGPT 订阅')), cli('claude', 'Claude Code CLI', t('Claude 订阅')),
    {id: 'api', name: t('API Key（备用）'), state: api?.configured ? 'off' : 'missing', summary: api?.configured ? t('已配置，需手动选择才会使用') : t('未配置'), hint: api?.configured ? '' : t('设置 LEMFOLIO_API_KEY 和 LEMFOLIO_API_MODEL 后可用'), selectable: Boolean(api?.configured)},
  ];
}

export const short = (value: string | null | undefined, size = 12) => value ? value.slice(0, size) : '—';

/** Splits model text so that real project locations such as main.tex:12 can link to the source. */
export function linkSources(answer: string, paths: string[]): ({text: string} | {path: string; line: number; text: string})[] {
  const known = new Set(paths);
  const parts: ({text: string} | {path: string; line: number; text: string})[] = [];
  const pattern = /([\w./-]+\.(?:tex|ltx|bib|sty|cls))(?::|\s*第\s*|\s+line\s+)(\d+)/g;
  let position = 0;
  for (const match of answer.matchAll(pattern)) {
    if (!known.has(match[1]) || !Number(match[2])) continue;
    if (match.index > position) parts.push({text: answer.slice(position, match.index)});
    parts.push({path: match[1], line: Number(match[2]), text: match[0]});
    position = match.index + match[0].length;
  }
  if (position < answer.length) parts.push({text: answer.slice(position)});
  return parts;
}
