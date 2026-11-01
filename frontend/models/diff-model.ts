import { t } from '../i18n.js';
// Display model for a proposal's digest-bound unified diff. Line kinds and numbers
// always come from that text; the Keel Rust model only adds exact intra-line ranges.
export interface ChangedRange { start: number; end: number }
export interface DiffLine { kind: 'hunk' | 'context' | 'add' | 'del'; old: number | null; new: number | null; text: string; changed: ChangedRange[] }
export interface DiffFile { path: string; status: string; adds: number; dels: number; lines: DiffLine[] }
export interface SplitRow { left: DiffLine | null; right: DiffLine | null; hunk?: string }
export interface Segment { text: string; changed: boolean }

const stripPrefix = (name: string) => name.replace(/^"|"$/g, '').replace(/^[ab]\//, '');

export function parseUnified(unified: string): DiffFile[] {
  const files: DiffFile[] = [];
  let file: DiffFile | null = null, oldLine = 0, newLine = 0, inHunk = false;
  const lines = unified.split('\n');
  for (let index = 0; index < lines.length; index++) {
    const line = lines[index];
    if (line.startsWith('--- ') && lines[index + 1]?.startsWith('+++ ')) {
      const before = line.slice(4).split('\t')[0], after = lines[++index].slice(4).split('\t')[0];
      const created = before === '/dev/null', removed = after === '/dev/null';
      file = {path: stripPrefix(removed ? before : after), status: created ? 'added' : removed ? 'deleted' : 'modified', adds: 0, dels: 0, lines: []};
      files.push(file); inHunk = false;
      continue;
    }
    const header = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/.exec(line);
    if (header && file) {
      oldLine = Number(header[1]); newLine = Number(header[2]); inHunk = true;
      file.lines.push({kind: 'hunk', old: null, new: null, text: line, changed: []});
    } else if (file && inHunk && line.startsWith('+')) {
      file.adds++; file.lines.push({kind: 'add', old: null, new: newLine++, text: line.slice(1), changed: []});
    } else if (file && inHunk && line.startsWith('-')) {
      file.dels++; file.lines.push({kind: 'del', old: oldLine++, new: null, text: line.slice(1), changed: []});
    } else if (file && inHunk && line.startsWith(' ')) {
      file.lines.push({kind: 'context', old: oldLine++, new: newLine++, text: line.slice(1), changed: []});
    }
  }
  return files;
}

interface RustRange { start_utf16?: unknown; end_utf16?: unknown }
interface RustLine { kind?: unknown; text?: unknown; changed?: RustRange[] }
interface RustFile { path?: unknown; lines?: RustLine[] }

/** Overlays Rust intra-line ranges when its lines match the parsed text; otherwise falls back locally. */
export function withInlineRanges(files: DiffFile[], model: unknown): DiffFile[] {
  const rust = (model as {status?: unknown; files?: RustFile[]} | null);
  const available = rust?.status === 'available' && Array.isArray(rust.files) ? rust.files : [];
  return files.map((file, fileIndex) => {
    const other = available[fileIndex];
    const lines = other?.path === file.path && Array.isArray(other.lines) ? other.lines : null;
    const matches = lines?.length === file.lines.length && lines.every((line, index) => line.text === file.lines[index].text && line.kind === file.lines[index].kind);
    if (matches && lines) {
      return {...file, lines: file.lines.map((line, index) => ({...line, changed: (lines[index].changed || []).flatMap(range => typeof range.start_utf16 === 'number' && typeof range.end_utf16 === 'number' ? [{start: range.start_utf16, end: range.end_utf16}] : [])}))};
    }
    return {...file, lines: localInlineRanges(file.lines)};
  });
}

/** Marks the differing middle of a replaced line when no native model is available. */
export function localInlineRanges(lines: DiffLine[]): DiffLine[] {
  const output = lines.map(line => ({...line}));
  for (let index = 0; index < output.length;) {
    if (output[index].kind !== 'del') { index++; continue; }
    let deletes = index;
    while (output[deletes]?.kind === 'del') deletes++;
    let adds = deletes;
    while (output[adds]?.kind === 'add') adds++;
    const pairs = Math.min(deletes - index, adds - deletes);
    for (let pair = 0; pair < pairs; pair++) {
      const before = output[index + pair], after = output[deletes + pair];
      let prefix = 0;
      while (prefix < before.text.length && prefix < after.text.length && before.text[prefix] === after.text[prefix]) prefix++;
      let suffix = 0;
      while (suffix < before.text.length - prefix && suffix < after.text.length - prefix && before.text.at(-1 - suffix) === after.text.at(-1 - suffix)) suffix++;
      if (before.text.length - suffix > prefix) before.changed = [{start: prefix, end: before.text.length - suffix}];
      if (after.text.length - suffix > prefix) after.changed = [{start: prefix, end: after.text.length - suffix}];
    }
    index = adds;
  }
  return output;
}

export function segments(line: DiffLine): Segment[] {
  const output: Segment[] = [];
  let position = 0;
  for (const {start, end} of [...line.changed].sort((a, b) => a.start - b.start)) {
    if (start < position || end > line.text.length || end <= start) continue;
    if (start > position) output.push({text: line.text.slice(position, start), changed: false});
    output.push({text: line.text.slice(start, end), changed: true});
    position = end;
  }
  if (position < line.text.length || !output.length) output.push({text: line.text.slice(position), changed: false});
  return output;
}

/** Pairs each deleted run with the following added run for original/new columns. */
export function splitRows(lines: DiffLine[]): SplitRow[] {
  const rows: SplitRow[] = [];
  for (let index = 0; index < lines.length;) {
    const line = lines[index];
    if (line.kind === 'hunk') { rows.push({left: null, right: null, hunk: line.text}); index++; continue; }
    if (line.kind === 'context') { rows.push({left: line, right: line}); index++; continue; }
    let deletes = index;
    while (lines[deletes]?.kind === 'del') deletes++;
    let adds = deletes;
    while (lines[adds]?.kind === 'add') adds++;
    for (let offset = 0; offset < Math.max(deletes - index, adds - deletes); offset++) {
      rows.push({left: index + offset < deletes ? lines[index + offset] : null, right: deletes + offset < adds ? lines[deletes + offset] : null});
    }
    index = adds;
  }
  return rows;
}

/** A human-readable location for a hunk, derived only from line numbers. */
export function hunkLocation(header: string): string {
  const match = /^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@\s*(.*)$/.exec(header);
  if (!match) return header;
  const span = (start: string, count?: string) => { const total = count === undefined ? 1 : Number(count), first = Number(start); return total <= 1 ? t('第 {0} 行', first) : t('第 {0}–{1} 行', first, first + total - 1); };
  return t('原文 {0} → 新文 {1}{2}', span(match[1], match[2]), span(match[3], match[4]), match[5] ? ` · ${match[5]}` : '');
}
