// Pure display models: Diff, evidence claims, LaTeX log, project structure.
import assert from 'node:assert/strict';
import { resolve } from 'node:path';
import { build } from 'esbuild';

const bundle = await build({stdin: {contents: `export * from './frontend/models/diff-model.ts'; export * from './frontend/models/evidence-model.ts'; export * from './frontend/models/latex-log.ts'; export * from './frontend/models/project-model.ts';`, resolveDir: resolve('.'), loader: 'ts'}, bundle: true, format: 'esm', write: false, platform: 'neutral'});
const models = await import('data:text/javascript;base64,' + Buffer.from(bundle.outputFiles[0].text).toString('base64'));
const {parseUnified, withInlineRanges, localInlineRanges, segments, splitRows, hunkLocation, buildEvidenceClaims, recordStatus, compileClaim, parseLatexLog, fileTree, readOutline, readProviders, linkSources} = models;

const tests = {
  'unified diff keeps file status, counts and real line numbers'() {
    const [file, created] = parseUnified('--- a/main.tex\n+++ b/main.tex\n@@ -3,2 +3,2 @@ intro\n same\n-old text\n+new text\n--- /dev/null\n+++ b/fig.tex\n@@ -0,0 +1 @@\n+x\n');
    assert.equal(file.path, 'main.tex'); assert.equal(file.status, 'modified'); assert.equal(file.adds, 1); assert.equal(file.dels, 1);
    assert.deepEqual(file.lines.map(line => [line.kind, line.old, line.new]), [['hunk', null, null], ['context', 3, 3], ['del', 4, null], ['add', null, 4]]);
    assert.equal(created.status, 'added'); assert.equal(created.path, 'fig.tex');
  },
  'unparseable diff text yields no files, so the raw text is shown instead of a fake table'() { assert.deepEqual(parseUnified('proposed changes'), []); },
  'rust ranges apply only when the rust lines match the digest-bound text'() {
    const parsed = parseUnified('--- a/x.tex\n+++ b/x.tex\n@@ -1 +1 @@\n-定义😀甲结论\n+定义😀乙结论\n');
    const range = {start: 10, end: 13, start_utf16: 4, end_utf16: 5};
    const matching = {status: 'available', files: [{path: 'x.tex', lines: [{kind: 'hunk', text: parsed[0].lines[0].text, changed: []}, {kind: 'del', text: '定义😀甲结论', changed: [range]}, {kind: 'add', text: '定义😀乙结论', changed: [range]}]}]};
    const line = withInlineRanges(parsed, matching)[0].lines[1];
    assert.deepEqual(segments(line).filter(part => part.changed).map(part => part.text), ['甲'], 'UTF-16 ranges survive astral characters');
    const mismatched = {status: 'available', files: [{path: 'x.tex', lines: [{kind: 'del', text: 'other', changed: [range]}]}]};
    assert.deepEqual(segments(withInlineRanges(parsed, mismatched)[0].lines[1]).filter(part => part.changed).map(part => part.text), ['甲'], 'falls back to the local comparison');
  },
  'local comparison marks only the differing middle'() {
    const lines = localInlineRanges([{kind: 'del', old: 1, new: null, text: 'hello brave world', changed: []}, {kind: 'add', old: null, new: 1, text: 'hello big world', changed: []}]);
    assert.deepEqual(segments(lines[0]).filter(part => part.changed).map(part => part.text), ['rave']);
    assert.deepEqual(segments(lines[1]).filter(part => part.changed).map(part => part.text), ['ig']);
  },
  'split rows pair replacements and leave gaps for pure insertions'() {
    const rows = splitRows(parseUnified('--- a/x\n+++ b/x\n@@ -1 +1,2 @@\n-a\n+b\n+c\n')[0].lines);
    assert.equal(rows[0].hunk, '@@ -1 +1,2 @@'); assert.equal(rows[1].left.text, 'a'); assert.equal(rows[1].right.text, 'b'); assert.equal(rows[2].left, null); assert.equal(rows[2].right.text, 'c');
  },
  'hunk location is stated in lines'() { assert.equal(hunkLocation('@@ -3,2 +3,3 @@ sec'), '原文 第 3–4 行 → 新文 第 3–5 行 · sec'); },
  'a successful compile is never a mathematical or human verification'() {
    const project = {id: 'p', state_revision: 's', main_file: 'main.tex'};
    const claims = Object.fromEntries(buildEvidenceClaims({project, proposals: [], build: {id: 'b1234567', status: 'success', engine: 'pdflatex', main_file: 'main.tex'}, buildHistorical: false, records: []}).map(claim => [claim.id, claim]));
    assert.equal(claims.compile.status, 'verified'); assert.match(claims.compile.limit, /不是数学证明/);
    assert.equal(claims.math.status, 'unchecked'); assert.equal(claims.literature.status, 'unchecked'); assert.equal(claims.human.status, 'human');
  },
  'math and literature statuses keep their distinct meanings'() {
    const project = {id: 'p', state_revision: 's', main_file: 'main.tex'};
    const record = (kind, status, state = 's') => ({id: kind + status, kind, state_revision: state, created_at: 1, base_revision: 'r', project_id: 'p', result: {status}});
    const claim = (records, id) => buildEvidenceClaims({project, proposals: [], buildHistorical: false, records}).find(item => item.id === id);
    assert.equal(claim([record('symbolic_math', 'symbolically_equivalent')], 'math').status, 'verified');
    assert.equal(claim([record('symbolic_math', 'not_equivalent')], 'math').status, 'failed');
    assert.equal(claim([record('symbolic_math', 'timeout')], 'math').status, 'uncertain');
    assert.equal(claim([record('symbolic_math', 'symbolically_equivalent', 'old')], 'math').status, 'unchecked', 'evidence from another state is not current');
    assert.equal(claim([record('reference_metadata', 'verified_metadata')], 'literature').status, 'verified');
    assert.equal(claim([record('reference_metadata', 'not_found')], 'literature').status, 'failed');
    assert.equal(claim([record('reference_metadata', 'unavailable')], 'literature').status, 'uncertain');
  },
  'historical and failed builds are not reported as a current success'() {
    assert.equal(compileClaim({id: 'b', status: 'success', engine: 'x', main_file: 'm'}, true).status, 'uncertain');
    assert.equal(compileClaim({id: 'b', status: 'failed', error: 'E', engine: 'x', main_file: 'm'}, false).status, 'failed');
  },
  'verification records never read as passed by themselves'() {
    const record = verification => ({kind: 'research_verification', result: {}, verification});
    assert.equal(recordStatus(record({status: 'human_review_required'})), 'human');
    assert.equal(recordStatus(record({status: 'failed'})), 'failed');
    assert.equal(recordStatus(record({status: 'partial'})), 'uncertain');
  },
  'latex log problems keep file and line'() {
    const problems = parseLatexLog('./sections/a.tex:12: Undefined control sequence.\nl.12 \\foo\nLaTeX Warning: Reference `x\' on page 1 undefined on input line 7.\n! Emergency stop.\nl.40 \n');
    assert.deepEqual(problems[0], {severity: 'error', file: 'sections/a.tex', line: 12, message: 'Undefined control sequence.'});
    assert.equal(problems.find(item => item.message === 'Emergency stop.').line, 40);
    assert.equal(problems.find(item => item.severity === 'warning').line, 7);
    assert.deepEqual(parseLatexLog(null), []);
  },
  'file tree nests folders before files'() {
    const tree = fileTree([{path: 'main.tex', text: true, size: 1}, {path: 'sections/b.tex', text: true, size: 1}, {path: 'sections/a.tex', text: true, size: 1}]);
    assert.deepEqual(tree.map(node => node.name), ['sections', 'main.tex']);
    assert.deepEqual(tree[0].children.map(node => node.name), ['a.tex', 'b.tex']);
  },
  'outline tolerates unknown shapes and reports unresolved citations'() {
    assert.deepEqual(readOutline(null).entries, []); assert.deepEqual(readOutline({index: 'bad'}).entries, []);
    const outline = readOutline({index: {nodes: [{id: 'a', type: 'section', level: 2, title: 'Intro', file: 'main.tex', line: 4}, {id: 'b', type: 'theorem', environment: 'theorem', title: 'theorem', file: 'main.tex', line: 9}], citations: [{key: 'known', file: 'main.tex', line: 5}, {key: 'missing', file: 'main.tex', line: 6}], bibliography: [{key: 'known', entry_type: 'article', file: 'r.bib', line: 1}, {key: 'unused', entry_type: 'book', file: 'r.bib', line: 9}]}});
    assert.deepEqual(outline.entries.map(entry => entry.title), ['Intro', '定理']);
    assert.deepEqual(outline.unresolved.map(item => item.key), ['missing']);
    assert.deepEqual(outline.bibliography.map(item => item.cited), [true, false]);
  },
  'providers report only real login state and never present API as signed in'() {
    const views = Object.fromEntries(readProviders({runtime: {providers: {codex: {installed: true, authenticated: true}, claude: {installed: true, authenticated: false, login_command: 'claude auth login'}, api: {installed: true, configured: false}}}}).map(view => [view.id, view]));
    assert.equal(views.codex.state, 'ready'); assert.equal(views.claude.state, 'login'); assert.match(views.claude.hint, /claude auth login/);
    assert.equal(views.api.selectable, false);
    assert.equal(readProviders(null)[0].state, 'off', 'unknown status is not presented as available');
  },
  'only real project locations become links'() {
    const parts = linkSources('See main.tex:12 and other.tex:3.', ['main.tex']);
    assert.deepEqual(parts.filter(part => part.path).map(part => [part.path, part.line]), [['main.tex', 12]]);
    assert.equal(parts.map(part => part.text).join(''), 'See main.tex:12 and other.tex:3.');
  },
};
for (const [name, test] of Object.entries(tests)) { test(); console.log('PASS', name); }
