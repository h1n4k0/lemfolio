import { t } from '../i18n.js';
import { locale } from '../i18n.js';
import { useEffect, useRef } from 'react';
import type { MutableRefObject } from 'react';
import { defaultKeymap, history, historyKeymap, indentWithTab, redo, undo } from '@codemirror/commands';
import { StreamLanguage, bracketMatching, indentUnit, syntaxHighlighting } from '@codemirror/language';
import { stex } from '@codemirror/legacy-modes/mode/stex';
import { highlightSelectionMatches, openSearchPanel, search, searchKeymap } from '@codemirror/search';
import { EditorSelection, EditorState, StateEffect, StateField } from '@codemirror/state';
import { Decoration, EditorView, drawSelection, highlightActiveLine, highlightActiveLineGutter, keymap, lineNumbers } from '@codemirror/view';
import { classHighlighter } from '@lezer/highlight';
import { StyleModule } from 'style-mod';
import type { SourceJump } from '../workspace.js';

// style-mod injects a <style> element into the document, which `style-src 'self'`
// refuses. Constructed stylesheets are allowed, so CodeMirror's generated rules go
// there and the CSP stays as strict as it is.
if ('adoptedStyleSheets' in document) {
  const sheet = new CSSStyleSheet(), mounted = new Set<StyleModule>();
  document.adoptedStyleSheets = [...document.adoptedStyleSheets, sheet];
  StyleModule.mount = (_root, modules) => {
    for (const module of Array.isArray(modules) ? modules : [modules]) {
      if (mounted.has(module)) continue;
      mounted.add(module);
      for (const rule of module.getRules().split('\n')) if (rule) sheet.insertRule(rule, sheet.cssRules.length);
    }
  };
}

export interface EditorApi { undo(): void; redo(): void; openSearch(): void; focus(): void; cursor(): {line: number; column: number} | null }

const flashLine = StateEffect.define<number | null>();
const flashField = StateField.define({
  create: () => Decoration.none,
  update(value, transaction) {
    value = value.map(transaction.changes);
    for (const effect of transaction.effects) {
      if (effect.is(flashLine)) value = effect.value === null ? Decoration.none : Decoration.set([Decoration.line({class: 'cm-flash-line'}).range(effect.value)]);
    }
    return value;
  },
  provide: field => EditorView.decorations.from(field),
});
const phrases = locale === 'en' ? [] : EditorState.phrases.of({
  'Find': '查找', 'Replace': '替换', 'next': '下一个', 'previous': '上一个', 'all': '全部', 'match case': '区分大小写',
  'by word': '全字匹配', 'regexp': '正则', 'replace': '替换', 'replace all': '全部替换', 'close': '关闭',
  'current match': '当前匹配', 'on line': '位于第', 'replaced $ matches': '已替换 $ 处', 'replaced match on line $': '已替换第 $ 行的匹配', 'Go to line': '跳转到行', 'go': '跳转',
});

interface Props {
  path: string | null; source: string; jump: SourceJump | null; label: string;
  onEdit(source: string): void; onSelect(start: number, end: number): void; onCursor(line: number, column: number, selected: number): void; apiRef: MutableRefObject<EditorApi | null>;
}

/** CodeMirror owns the document while typing. React only pushes text that changed elsewhere, such as an opened file or an applied proposal. */
export function SourceEditor({path, source, jump, label, onEdit, onSelect, onCursor, apiRef}: Props) {
  const host = useRef<HTMLDivElement>(null);
  const view = useRef<EditorView | null>(null);
  const callbacks = useRef({onEdit, onSelect, onCursor});
  callbacks.current = {onEdit, onSelect, onCursor};
  // Per-file editor states keep undo history while the user moves between project files.
  const states = useRef(new Map<string, EditorState>());
  const currentPath = useRef<string | null>(null);
  const flashTimer = useRef<ReturnType<typeof setTimeout>>(undefined);

  const extensions = useRef([
    lineNumbers(), highlightActiveLineGutter(), history(), drawSelection(), highlightActiveLine(), bracketMatching(), highlightSelectionMatches(),
    EditorState.allowMultipleSelections.of(true), EditorView.lineWrapping, indentUnit.of('  '),
    StreamLanguage.define(stex), syntaxHighlighting(classHighlighter), search({top: true}), phrases, flashField,
    // Mod-Enter compiles at the window level; without this the default keymap would also insert a blank line.
    keymap.of([{key: 'Mod-Shift-z', run: redo, preventDefault: true}, {key: 'Mod-Enter', run: () => true}, ...searchKeymap, ...historyKeymap, indentWithTab, ...defaultKeymap]),
    // Chromium's own editing commands try to copy a style attribute onto the lines they
    // create for multi-line text (dictation, automation), which the CSP blocks. Taking that
    // one input path keeps the browser out of it; typing and composition stay native.
    EditorView.domEventHandlers({beforeinput(event, editor) {
      if (event.inputType !== 'insertText' || !event.data?.includes('\n') || editor.composing) return false;
      event.preventDefault();
      editor.dispatch(editor.state.replaceSelection(event.data), {userEvent: 'input.type', scrollIntoView: true});
      return true;
    }}),
    EditorView.contentAttributes.of({'aria-label': t('LaTeX 源码'), spellcheck: 'false', autocapitalize: 'off', autocorrect: 'off', tabindex: '0'}),
    EditorView.updateListener.of(update => {
      if (update.docChanged) callbacks.current.onEdit(update.state.doc.toString());
      if (update.docChanged || update.selectionSet) {
        const range = update.state.selection.main, line = update.state.doc.lineAt(range.head);
        callbacks.current.onCursor(line.number, range.head - line.from + 1, range.to - range.from);
        if (update.selectionSet) callbacks.current.onSelect(range.from, range.to);
      }
    }),
  ]).current;

  useEffect(() => {
    if (!host.current) return;
    const created = new EditorView({parent: host.current, state: EditorState.create({doc: source, extensions})});
    view.current = created;
    (host.current as HTMLDivElement & {editorView?: EditorView}).editorView = created;
    apiRef.current = {
      undo: () => { undo(created); created.focus(); }, redo: () => { redo(created); created.focus(); },
      openSearch: () => { openSearchPanel(created); }, focus: () => created.focus(),
      cursor: () => { const head = created.state.selection.main.head, line = created.state.doc.lineAt(head); return {line: line.number, column: head - line.from + 1}; },
    };
    currentPath.current = path;
    return () => { clearTimeout(flashTimer.current); created.destroy(); view.current = null; apiRef.current = null; };
  }, []);

  useEffect(() => {
    const editor = view.current;
    if (!editor) return;
    const previous = currentPath.current;
    if (previous !== path) {
      if (previous) states.current.set(previous, editor.state);
      const cached = path ? states.current.get(path) : undefined;
      editor.setState(cached && cached.doc.toString() === source ? cached : EditorState.create({doc: source, extensions}));
      currentPath.current = path;
    } else if (editor.state.doc.toString() !== source) {
      // The file changed outside this editor (reload, restore, applied proposal):
      // the old undo stack no longer describes the text on disk.
      editor.setState(EditorState.create({doc: source, extensions}));
    }
    const head = editor.state.selection.main.head, line = editor.state.doc.lineAt(head);
    callbacks.current.onCursor(line.number, head - line.from + 1, 0);
  }, [path, source]);

  useEffect(() => {
    const editor = view.current;
    if (!editor || !jump || jump.path !== path) return;
    const line = editor.state.doc.line(Math.min(Math.max(jump.line, 1), editor.state.doc.lines));
    editor.dispatch({selection: EditorSelection.cursor(line.from), effects: [EditorView.scrollIntoView(line.from, {y: 'center'}), flashLine.of(line.from)]});
    editor.focus();
    clearTimeout(flashTimer.current);
    flashTimer.current = setTimeout(() => { if (view.current === editor) editor.dispatch({effects: flashLine.of(null)}); }, 1800);
  }, [jump?.nonce]);

  return <div id="source" className="source-editor" ref={host} data-empty={path ? undefined : 'true'} role="group" aria-label={label}/>;
}
