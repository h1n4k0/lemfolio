// Real-browser acceptance of the React workspace against a running isolated service.
// Uses system Edge, real keyboard and CDP IME input, Docker TeX and (unless --no-model)
// one real subscription CLI call. Nothing here fakes a task, build or verification.
//   node scripts/accept_frontend.mjs --url http://127.0.0.1:8791 [--no-model] [--provider codex|claude]
import { mkdir, readFile, stat, writeFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { chromium } from 'playwright-core';

const argument = (name, fallback) => { const index = process.argv.indexOf(name); return index > 0 ? process.argv[index + 1] : fallback; };
const baseUrl = argument('--url', 'http://127.0.0.1:8791');
const useModel = !process.argv.includes('--no-model');
const preferredProvider = argument('--provider', '');
const outputDir = new URL('../reports/frontend/', import.meta.url);
await mkdir(outputDir, {recursive: true});
const axeSource = await readFile(createRequire(import.meta.url).resolve('axe-core/axe.min.js'), 'utf8');

const browser = await chromium.launch({channel: 'msedge', headless: true});
const context = await browser.newContext({viewport: {width: 1600, height: 960}, colorScheme: 'light', locale: 'zh-CN'});
const page = await context.newPage();
const cdp = await context.newCDPSession(page);
const consoleProblems = [], cspViolations = [], httpErrors = [], results = [];
page.on('console', message => { if (message.text().startsWith('CSPV ')) cspViolations.push(message.text().slice(5)); else if (message.type() === 'error') consoleProblems.push(message.text().slice(0, 300)); });
page.on('pageerror', error => consoleProblems.push('pageerror: ' + error.message));
page.on('response', response => { if (response.status() >= 500) httpErrors.push(`${response.status()} ${response.url()}`); });
let dialogs = 'accept';
page.on('dialog', dialog => dialogs === 'accept' ? dialog.accept(dialog.type() === 'prompt' ? dialog.defaultValue() : undefined) : dialog.dismiss());
await page.addInitScript(() => document.addEventListener('securitypolicyviolation', event => console.log('CSPV', event.violatedDirective, event.sourceFile, event.lineNumber, event.blockedURI, (event.target?.outerHTML || '').slice(0, 120))));

// Waits for drawer and widget transitions, so a picture shows the state rather than its way there.
const shot = async (name, options = {}) => (await page.waitForTimeout(350), page.screenshot({path: new URL(`${name}.jpg`, outputDir).pathname.replace(/^\/([A-Za-z]:)/, '$1'), type: 'jpeg', quality: 82, ...options}));
const assert = (condition, message) => { if (!condition) throw new Error(message); };
const source = () => page.evaluate(() => document.getElementById('source').editorView.state.doc.toString());
const cursorLine = () => page.evaluate(() => { const view = document.getElementById('source').editorView; return view.state.doc.lineAt(view.state.selection.main.head).number; });
const lineOf = (text, needle) => text.split('\n').findIndex(line => line.includes(needle)) + 1;
const message = () => page.textContent('#message');
const buildState = () => page.getAttribute('#compile-state', 'data-build');
const settled = () => page.waitForFunction(() => document.getElementById('message')?.textContent !== '处理中…');
const focusEditor = async () => { await page.click('#view-source'); await page.click('.cm-content'); };
const replaceAll = async text => { await focusEditor(); await page.keyboard.press('Control+A'); await page.keyboard.insertText(text); };
const moveToLine = async line => page.evaluate(number => { const view = document.getElementById('source').editorView, target = view.state.doc.line(number); view.dispatch({selection: {anchor: target.from}, scrollIntoView: true}); view.focus(); }, line);
const save = async () => { await focusEditor(); await page.keyboard.press('Control+S'); await page.waitForFunction(() => document.querySelector('.tree-file[data-active]') && !document.querySelector('.tree-file[data-active] .dirty-dot')); await settled(); };
const compile = async expected => {
  await page.click('#compile'); await settled();
  await page.waitForFunction(() => !['queued', 'running', 'none'].includes(document.getElementById('compile-state').dataset.build), null, {timeout: 180000});
  assert(await buildState() === expected, `build ended ${await buildState()}, expected ${expected}: ${await page.textContent('#build-context')}`);
};
const canvasInk = () => page.evaluate(() => { const canvas = document.querySelector('.pdf-page canvas'); if (!canvas) return 0; const data = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data; let dark = 0; for (let index = 0; index < data.length; index += 4) if (data[index] < 128) dark++; return dark; });
const waitForPdf = async () => { await page.waitForSelector('.pdf-page canvas', {timeout: 30000}); await page.waitForFunction(() => document.querySelector('.textLayer span')); };
const axe = async name => {
  await page.evaluate(axeSource);
  const result = await page.evaluate(async () => { const value = await window.axe.run(document, {runOnly: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']}); return value.violations.map(item => ({id: item.id, impact: item.impact, nodes: item.nodes.length, sample: item.nodes[0]?.target.join(' '), help: item.help})); });
  return {view: name, violations: result};
};
async function step(name, run) {
  const started = Date.now();
  try { const detail = await run(); results.push({name, status: detail?.status || 'passed', ms: Date.now() - started, detail: detail ?? null}); console.log('PASS', name); }
  catch (error) { results.push({name, status: 'failed', ms: Date.now() - started, error: String(error.message || error).slice(0, 600)}); console.log('FAIL', name, String(error.message || error).slice(0, 300)); await shot('failure-' + name.replace(/\W+/g, '-')).catch(() => {}); }
}

const paper = (extra = '') => String.raw`\documentclass{article}
\usepackage{amsmath,amssymb}
\newtheorem{theorem}{Theorem}
\title{Acceptance Paper}
\author{Lemfolio}
\begin{document}
\maketitle
\begin{abstract}
A short abstract about bounded sums.
\end{abstract}
\section{Introduction}\label{sec:intro}
The opening paragraph studies bounded sums and refers to Section~\ref{sec:method}.
${Array.from({length: 18}, (_, index) => `Filler sentence number ${index + 1} keeps the first page occupied with ordinary prose so that the document grows.`).join('\n')}
\input{sections/background}
\newpage
\section{Method}\label{sec:method}
Marker paragraph on the second page describes the method used here.
\begin{theorem}\label{thm:main}
For every integer $n \ge 1$ the sum satisfies
\begin{equation}\label{eq:sum}
\sum_{k=1}^{n} k = \frac{n(n+1)}{2}.
\end{equation}
\end{theorem}
${extra}
\bibliographystyle{plain}
\bibliography{references}
\end{document}
`;
const background = String.raw`\subsection{Background}
Background sentence from the included file, cited as \cite{knuth}.
`;
const bibliography = `@book{knuth,
  title = {The Art of Computer Programming},
  author = {Knuth, Donald E.},
  year = {1968}
}
`;

await step('boot-under-csp', async () => {
  const before = Date.now();
  await page.goto(baseUrl);
  await page.waitForFunction(() => document.getElementById('message')?.textContent === '本地会话已建立');
  const timing = await page.evaluate(() => { const nav = performance.getEntriesByType('navigation')[0]; return {domContentLoaded: Math.round(nav.domContentLoadedEventEnd), load: Math.round(nav.loadEventEnd)}; });
  const csp = (await (await page.request.get(baseUrl + '/')).headers())['content-security-policy'];
  assert(csp.includes("script-src 'self'") && csp.includes("style-src 'self'") && !csp.includes('unsafe'), 'CSP must stay strict: ' + csp);
  await shot('01-home');
  return {csp, timing, ready_ms: Date.now() - before};
});

await step('create-project', async () => {
  await page.fill('#project-name', `Acceptance ${new Date().toISOString().slice(0, 19)}`);
  await page.click('#create-project');
  await page.waitForFunction(() => document.getElementById('source').editorView?.state.doc.length > 0);
  await settled();
  assert(!(await page.isVisible('.assistant')), 'the assistant must not be the first thing shown');
});

await step('codemirror-typing-undo-redo-highlighting', async () => {
  await focusEditor();
  await page.keyboard.press('Control+End');
  await page.keyboard.type('% typed by real keys', {delay: 4});
  assert((await source()).endsWith('% typed by real keys'), 'typed text reaches the document');
  assert(await page.getAttribute('#save-state', 'data-state') === 'dirty', 'typing marks the file unsaved');
  await page.keyboard.press('Control+Z'); await page.waitForTimeout(50);
  assert(!(await source()).includes('typed by real keys'), 'Ctrl+Z undoes');
  await page.keyboard.press('Control+Shift+Z'); await page.waitForTimeout(50);
  assert((await source()).includes('typed by real keys'), 'Ctrl+Shift+Z redoes');
  await page.keyboard.press('Control+Z'); await page.waitForTimeout(50);
  const counts = await page.evaluate(() => ({lineNumbers: document.querySelectorAll('.cm-lineNumbers .cm-gutterElement').length, tokens: new Set([...document.querySelectorAll('.cm-line span[class*="tok-"]')].map(item => item.className)).size}));
  assert(counts.lineNumbers > 5, 'line numbers render'); assert(counts.tokens >= 2, 'LaTeX tokens are highlighted');
  return counts;
});

await step('codemirror-find-replace', async () => {
  await replaceAll('alpha beta alpha\ngamma alpha\n');
  await page.keyboard.press('Control+F');
  await page.waitForSelector('.cm-search input[name="search"]');
  await page.fill('.cm-search input[name="search"]', 'alpha');
  await page.fill('.cm-search input[name="replace"]', 'omega');
  await shot('02-find-replace');
  await page.click('.cm-search button[name="replaceAll"]');
  assert(await source() === 'omega beta omega\ngamma omega\n', 'replace all changed every match: ' + await source());
  await page.keyboard.press('Escape');
});

await step('chinese-ime-composition', async () => {
  await replaceAll('前缀\n');
  await page.keyboard.press('Control+End');
  await page.evaluate(() => { window.__ime = []; const content = document.querySelector('.cm-content'); for (const name of ['compositionstart', 'compositionupdate', 'compositionend']) content.addEventListener(name, event => window.__ime.push(name + ':' + event.data)); });
  // Input.imeSetComposition is the browser-level composition path a real IME uses.
  for (const text of ['l', 'lu', 'lun', 'lunw', 'lunwe', 'lunwen']) await cdp.send('Input.imeSetComposition', {text, selectionStart: text.length, selectionEnd: text.length});
  const during = await source();
  await cdp.send('Input.imeSetComposition', {text: '论文', selectionStart: 2, selectionEnd: 2});
  await cdp.send('Input.insertText', {text: '论文'});
  await page.waitForTimeout(100);
  const after = await source(), events = await page.evaluate(() => window.__ime);
  assert(after === '前缀\n论文', 'composition commits exactly once: ' + JSON.stringify(after));
  assert(events.some(item => item.startsWith('compositionstart')) && events.some(item => item.startsWith('compositionend')), 'real composition events fired: ' + events.join());
  await page.keyboard.press('Control+Z'); await page.waitForTimeout(60);
  return {during_composition: during, committed: after, events: events.length};
});

await step('multi-file-project-keeps-drafts', async () => {
  await replaceAll(paper());
  await save();
  for (const [path, content] of [['sections/background.tex', background]]) {
    await page.click('#rail-add'); await page.fill('#new-path', path); await page.click('#new-file'); await settled();
    await replaceAll(content); await save();
  }
  await page.click('.tree-file:has-text("main.tex")'); await settled();
  await focusEditor(); await page.keyboard.press('Control+End'); await page.keyboard.type('% unsaved main draft');
  await page.click('.tree-file:has-text("references.bib")'); await settled();
  await replaceAll(bibliography); await save();
  assert(await page.locator('.tree-file:has-text("main.tex") .dirty-dot').count() === 1, 'the draft stays marked');
  await page.click('.tree-file:has-text("main.tex")'); await settled();
  assert((await source()).includes('% unsaved main draft'), 'switching back restores the draft');
  await page.keyboard.press('Control+Z'); await page.waitForTimeout(80);
  await page.evaluate(() => document.getElementById('source').editorView.focus());
  // Undo history survives the switch, which also brings the text back to the saved version.
  while ((await source()).includes('unsaved main')) { await page.keyboard.press('Control+Z'); await page.waitForTimeout(30); }
  assert(await page.getAttribute('#save-state', 'data-state') === 'saved', 'undoing to the saved text is clean');
});

await step('outline-and-references-navigation', async () => {
  await page.click('#rail-tab-outline');
  await page.waitForSelector('.outline-row:has-text("Method")');
  const titles = await page.locator('.outline-row .tree-name').allTextContents();
  assert(titles.includes('Introduction') && titles.includes('Method') && titles.includes('定理'), 'outline lists sections and claims: ' + titles.join());
  await page.click('.outline-row:has-text("Method")'); await settled(); await page.waitForTimeout(60);
  const text = await source();
  assert(await cursorLine() === lineOf(text, '\\section{Method}'), 'outline click moves the cursor to the section');
  await shot('03-outline');
  await page.click('#rail-tab-refs');
  await page.waitForSelector('.bib-entry:has-text("knuth")');
  await page.click('#rail-tab-files');
  return {outline: titles};
});

let pdfRenderMs = null;
await step('compile-and-pdfjs-render', async () => {
  await save();
  const started = Date.now();
  await page.keyboard.press('Control+Enter'); await settled();
  await page.waitForFunction(() => !['queued', 'running', 'none'].includes(document.getElementById('compile-state').dataset.build), null, {timeout: 180000});
  assert(await buildState() === 'success', 'build: ' + await page.textContent('#build-context'));
  await waitForPdf();
  pdfRenderMs = Date.now() - started;
  const ink = await canvasInk();
  assert(ink > 500, 'the canvas contains rendered text pixels, got ' + ink);
  const pages = Number((await page.textContent('#pdf-pages')).replace(/\D/g, ''));
  assert(pages >= 2, 'multi-page paper, got ' + pages);
  const text = await page.locator('.pdf-page[data-page="1"] .textLayer').textContent();
  assert(text.includes('opening paragraph'), 'text layer carries selectable text');
  await shot('04-compiled');
  return {pages, dark_pixels: ink, build_and_first_page_ms: pdfRenderMs};
});

await step('pdf-page-navigation-and-zoom', async () => {
  const pageOne = await page.locator('.pdf-page[data-page="1"]').boundingBox();
  await page.click('#pdf-next'); await page.waitForTimeout(250);
  assert(await page.inputValue('#pdf-page') === '2', 'next page button');
  await page.waitForFunction(() => document.querySelector('.pdf-page[data-page="2"] canvas'));
  await page.fill('#pdf-page', '1'); await page.keyboard.press('Enter'); await page.waitForTimeout(250);
  assert(await page.inputValue('#pdf-page') === '1', 'page input');
  await page.click('#pdf-zoom-in'); await page.waitForTimeout(350);
  const larger = await page.locator('.pdf-page[data-page="1"]').boundingBox();
  assert(larger.width > pageOne.width, 'zoom in enlarges the page');
  await page.selectOption('#pdf-zoom', '1'); await page.waitForTimeout(350);
  const actual = await page.locator('.pdf-page[data-page="1"]').boundingBox();
  // TeX Live's default paper in this image is A4 (595.28 pt wide).
  assert(Math.abs(actual.width - 595.28) < 2, '100% is one CSS pixel per point, got ' + actual.width);
  await page.selectOption('#pdf-zoom', 'width'); await page.waitForTimeout(350);
  await page.focus('#pdf-scroll'); await page.keyboard.press('PageDown'); await page.waitForTimeout(250);
  assert(await page.inputValue('#pdf-page') === '2', 'PageDown turns the page');
  await page.keyboard.press('PageUp');
  return {fit_width: pageOne.width, zoomed: larger.width, actual: actual.width};
});

await step('pane-resize-refits-pdf', async () => {
  const before = await page.locator('.pdf-page[data-page="1"]').boundingBox();
  const handle = await page.locator('.splitter[aria-label="调整 PDF 宽度"]').boundingBox();
  await page.mouse.move(handle.x + 2, 400); await page.mouse.down(); await page.mouse.move(handle.x - 120, 400, {steps: 6}); await page.mouse.up();
  await page.waitForTimeout(400);
  const after = await page.locator('.pdf-page[data-page="1"]').boundingBox();
  assert(after.width > before.width + 60, `wider pane refits the page ${before.width} → ${after.width}`);
  await page.focus('.splitter[aria-label="调整 PDF 宽度"]');
  for (let index = 0; index < 5; index++) await page.keyboard.press('ArrowRight');
  await page.waitForTimeout(200);
});

let forwardBox = null;
await step('synctex-forward-source-to-pdf', async () => {
  await focusEditor();
  const text = await source(), line = lineOf(text, 'Marker paragraph on the second page');
  await moveToLine(line);
  await page.click('#synctex');
  await page.waitForSelector('.pdf-target', {timeout: 30000});
  const onPage = await page.locator('.pdf-target').first().evaluate(element => element.closest('.pdf-page').dataset.page);
  assert(onPage === '2', 'the marker is on PDF page 2, got ' + onPage);
  forwardBox = await page.locator('.pdf-target').first().boundingBox();
  const scroller = await page.locator('#pdf-scroll').boundingBox();
  assert(forwardBox.y > scroller.y && forwardBox.y < scroller.y + scroller.height, 'the target is scrolled into view');
  assert(/PDF 第 2 页/.test(await page.textContent('#pdf-identity')), 'result is reported');
  await shot('05-synctex-forward');
  return {line, page: onPage};
});

await step('synctex-inverse-pdf-to-source', async () => {
  await moveToLine(1);
  const text = await source(), expected = lineOf(text, 'Marker paragraph on the second page');
  await page.mouse.dblclick(forwardBox.x + 20, forwardBox.y + forwardBox.height / 2);
  await page.waitForFunction(() => /→ main\.tex:\d+/.test(document.getElementById('pdf-identity').textContent), null, {timeout: 30000});
  const line = await cursorLine();
  assert(Math.abs(line - expected) <= 1, `double-click returns to the source line: ${line}, expected ${expected}`);
  await shot('06-synctex-inverse');
  // Multi-file: a point produced by the included file opens that file.
  await page.fill('#pdf-page', '1'); await page.keyboard.press('Enter'); await page.waitForTimeout(250);
  await page.click('.tree-file:has-text("background")').catch(() => {}); await settled();
  await focusEditor(); await moveToLine(2);
  await page.click('#synctex');
  await page.waitForFunction(() => /PDF 第 \d+ 页/.test(document.getElementById('pdf-identity').textContent) && document.querySelector('.pdf-target'), null, {timeout: 30000});
  const box = await page.locator('.pdf-target').first().boundingBox();
  await page.click('.tree-file:has-text("main.tex")'); await settled();
  await page.mouse.dblclick(box.x + 20, box.y + box.height / 2);
  await page.waitForFunction(() => /→ sections\/background\.tex:\d+/.test(document.getElementById('pdf-identity').textContent), null, {timeout: 30000});
  assert((await page.textContent('#file-path')) === 'sections/background.tex', 'inverse search opens the included file');
  await page.click('.tree-file:has-text("main.tex")'); await settled();
  return {main_line: line, included_file_opened: true};
});

await step('failed-build-clears-preview-and-locates-error', async () => {
  const good = await source();
  const broken = good.replace('Marker paragraph', '\\undefinedcommandxyz Marker paragraph');
  await replaceAll(broken); await save();
  assert(await page.locator('#pdf').isHidden(), 'saving a new state invalidates the old preview at once');
  await compile('failed');
  assert(await page.locator('#pdf').isHidden(), 'failed build shows no PDF');
  assert(await page.getAttribute('#pdf', 'data-src') === null, 'no PDF address remains');
  assert(await page.locator('.pdf-page').count() === 0, 'the old pages are gone');
  await page.waitForSelector('.problems [data-severity="error"]');
  await shot('07-failed-build');
  await page.click('.problems [data-severity="error"] .link'); await settled(); await page.waitForTimeout(60);
  assert(await cursorLine() === lineOf(broken, '\\undefinedcommandxyz'), 'the problem link jumps to the error line');
  await replaceAll(good); await save(); await compile('success'); await waitForPdf();
});

const proposalAccept = () => page.locator('#proposals details[open] button:has-text("接受此摘要绑定的修改")');
await step('diff-review-stale-conflict-and-reject', async () => {
  const good = await source();
  await replaceAll(good.replace('bounded sums', 'bounded partial sums'));
  await page.click('#propose-edit'); await settled();
  await page.waitForSelector('#proposals details[open] .diff-file');
  assert(await page.locator('#proposals details[open] mark').count() >= 1, 'intra-line changes are marked');
  await page.waitForFunction(() => document.querySelector('.diff-engine')?.textContent.includes('Keel Rust'), null, {timeout: 8000});
  const meta = await page.locator('#proposals details[open] .proposal-meta').textContent();
  assert(meta.includes('main.tex') && meta.includes('基础版本') && meta.includes('修改依据'), 'file, base version and rationale are shown');
  assert((await page.locator('#proposals details[open] thead').first().textContent()).includes('原始内容'), 'original and new columns');
  await shot('08-diff-review');
  await page.click('.segmented button:has-text("合并")');
  assert(await page.locator('#proposals details[open] .diff-unified').count() === 1, 'unified layout');
  await page.click('.segmented button:has-text("并排")');
  // Saving other text moves the project on: the open proposal must show a conflict.
  await page.click('#view-source');
  await replaceAll(good.replace('Filler sentence number 1 ', 'Filler sentence number one ')); await save();
  await page.click('#view-review');
  await page.waitForSelector('#proposals details[open] [data-stale]');
  assert(await proposalAccept().isDisabled(), 'stale proposal cannot be accepted');
  assert((await page.locator('#proposals details[open] .conflict').textContent()).includes('不能应用'), 'conflict is explicit');
  await page.waitForSelector('#proposals details[open] .conflict-drift');
  await shot('09-stale-conflict');
  await page.click('#proposals details[open] button:has-text("拒绝修改")'); await settled();
  await page.click('#view-source');
  assert((await source()).includes('number one'), 'rejecting leaves the file untouched');
});

await step('manual-proposal-apply-is-separate-from-verification', async () => {
  const text = await source();
  await replaceAll(text.replace('Filler sentence number one ', 'Filler sentence number 1 '));
  await page.click('#propose-edit'); await settled();
  await page.waitForSelector('#proposals details[open] .diff-file');
  await page.locator('#proposals details[open] button:has-text("接受此摘要绑定的修改")').click();
  await page.waitForFunction(() => /修改已应用/.test(document.getElementById('message').textContent), null, {timeout: 30000});
  assert(/人工审阅|验收记录|独立验证/.test(await message()), 'apply and verification are reported separately: ' + await message());
  await page.click('#view-source');
  assert((await source()).includes('Filler sentence number 1 '), 'accepted text is in the editor');
  return {message: await message()};
});

let runProvider = null, chosenModel = null;
await step('real-model-loop-to-independent-verification', async () => {
  const status = await page.evaluate(async () => (await fetch('/api/status')).json());
  const providers = status.runtime.providers;
  runProvider = preferredProvider || ['codex', 'claude'].find(name => providers[name]?.authenticated) || null;
  if (!useModel || !runProvider || !providers[runProvider]?.authenticated) return {status: 'not_run', reason: useModel ? 'No logged-in official CLI' : '--no-model', providers: Object.fromEntries(Object.entries(providers).map(([name, item]) => [name, {installed: item.installed, authenticated: item.authenticated}]))};
  await page.click('#toggle-assistant');
  await page.waitForSelector('.assistant');
  await page.waitForFunction(() => document.querySelector('.assistant-host[data-open="true"]'));
  await page.click('#model-picker');
  await page.waitForSelector('#model-widget:not([hidden])');
  await page.check(`input[name="provider"][value="${runProvider}"]`);
  await page.waitForSelector('#model-widget .model-row[data-model]', {timeout: 45000});
  const offered = await page.locator('#model-widget .model-row[data-model]').evaluateAll(rows => rows.map(row => row.dataset.model));
  const catalog = await page.evaluate(async () => (await fetch('/api/runtime/models')).json());
  const reported = catalog.providers.find(item => item.provider === runProvider).models.map(item => item.id);
  assert(JSON.stringify(offered) === JSON.stringify(reported), 'the picker offers exactly what the CLI reported: ' + offered);
  chosenModel = offered[0];
  await shot('10a-model-picker');
  await page.locator(`#model-widget .model-row[data-model="${chosenModel}"]`).click();
  assert(!(await page.isVisible('#model-widget')), 'choosing a model closes the widget');
  await page.click('#mode-edit');
  await page.fill('#intent', 'In the Method section, change the single word "Marker" to "Tagged". Make only that one guarded edit in main.tex and nothing else.');
  await shot('10-assistant-edit');
  await page.click('#submit-run'); await settled();
  await page.waitForFunction(() => document.querySelector('.run-state') && !['排队中', '运行中'].includes(document.querySelector('.run-state').textContent), null, {timeout: 480000});
  const state = await page.textContent('.run-state');
  assert(state === '模型已返回', 'run ended: ' + state + ' ' + await page.textContent('#run-output'));
  assert((await page.textContent('.run-line')).includes(chosenModel), 'the run records the model chosen in the picker');
  await page.waitForSelector('#run-proposal:not([disabled])', {timeout: 15000});
  await shot('11-model-result');
  await page.click('#run-proposal'); await settled();
  await page.waitForSelector('#proposals details[open] .diff-file', {timeout: 15000});
  const rationale = await page.locator('#proposals details[open] .proposal-meta').textContent();
  assert(rationale.includes('科研任务'), 'the proposal names the run it came from');
  await shot('12-model-diff');
  assert(await page.getAttribute('#save-state', 'data-state') === 'saved', 'nothing is written before approval');
  await page.click('#view-source');
  assert((await source()).includes('Marker paragraph'), 'the file is unchanged before approval');
  await page.click('#view-review');
  await page.locator('#proposals details[open] button:has-text("接受此摘要绑定的修改")').click();
  await page.waitForFunction(() => /修改已应用/.test(document.getElementById('message').textContent), null, {timeout: 30000});
  await page.click('#view-source');
  assert((await source()).includes('Tagged paragraph'), 'the approved edit is applied');
  // The independent verification queued by approval compiles the approved snapshot.
  await page.click('#view-evidence');
  await page.waitForFunction(() => document.querySelector('#verifications .record'), null, {timeout: 30000});
  await page.click('#refresh-verifications'); await settled();
  await page.click('#view-source');
  await compile('success'); await waitForPdf();
  const pdfText = await page.locator('.pdf-page[data-page="2"]').evaluate(async element => { element.scrollIntoView(); return ''; }).catch(() => '');
  await page.waitForFunction(() => document.querySelector('.pdf-page[data-page="2"] .textLayer span'), null, {timeout: 20000}).catch(() => {});
  const secondPage = await page.locator('.pdf-page[data-page="2"] .textLayer').textContent();
  assert(secondPage.includes('Tagged'), 'the new PDF contains the approved edit');
  await page.click('#view-evidence');
  await page.click('#refresh-verifications'); await settled();
  await page.waitForTimeout(400);
  await shot('13-evidence-after-loop');
  const claims = await page.locator('.claims tbody tr').evaluateAll(rows => rows.map(row => ({id: row.id, status: row.dataset.status, label: row.querySelector('th').textContent})));
  const claim = id => claims.find(item => item.id === 'claim-' + id)?.status;
  assert(claim('model') === 'verified' && claim('applied') === 'verified' && claim('compile') === 'verified', 'completed stages: ' + JSON.stringify(claims));
  assert(claim('human') === 'human', 'human review stays required');
  assert(claim('math') !== 'verified' || pdfText === '', 'no math evidence is implied');
  await page.click('#view-workflow');
  await page.waitForSelector('#workflow-graph');
  await shot('14-workflow');
  await page.click('#view-source');
  return {provider: runProvider, claims, verifications: await page.locator('#verifications .record').count()};
});

await step('five-assistant-modes-differ', async () => {
  if (!(await page.isVisible('.assistant'))) await page.click('#toggle-assistant');
  const seen = {};
  for (const mode of ['chat', 'edit', 'math', 'literature', 'review']) {
    await page.click(`#mode-${mode}`);
    seen[mode] = {hint: await page.textContent('.mode-hint'), math_fields: await page.locator('#math-lhs').count(), literature_field: await page.locator('#literature-query').count()};
  }
  assert(new Set(Object.values(seen).map(item => item.hint)).size === 5, 'each mode explains itself');
  assert(seen.math.math_fields === 1 && seen.chat.math_fields === 0, 'math inputs only in math mode');
  assert(seen.literature.literature_field === 1 && seen.review.literature_field === 0, 'literature inputs only in literature mode');
  return seen;
});

await step('symbolic-check-is-not-a-proof', async () => {
  await page.click('#mode-math');
  await page.fill('#math-lhs', '(x+1)**2'); await page.fill('#math-rhs', 'x**2+2*x+1');
  await page.click('#math'); await settled();
  await page.waitForSelector('#research-output .status-mark');
  const text = await page.textContent('#research-output');
  assert(text.includes('有限符号检查：等价') && text.includes('不是数学证明'), text.slice(0, 200));
  await page.fill('#math-rhs', 'x**2+1');
  await page.click('#math'); await settled();
  await page.waitForFunction(() => document.querySelector('#research-output .status-mark')?.dataset.status === 'failed');
  await shot('15-math-tool');
  await page.click('#view-evidence');
  const math = await page.getAttribute('#claim-math', 'data-status');
  assert(math === 'failed', 'a failed check dominates: ' + math);
  await page.click('#view-source');
  return {claim_math: math};
});

await step('literature-tool-reports-real-status', async () => {
  await page.click('#mode-literature');
  await page.fill('#literature-query', 'not-an-identifier');
  await page.click('#lookup'); await settled();
  await page.waitForSelector('#research-output .status-mark');
  const invalid = await page.textContent('#research-output .status-mark');
  assert(invalid.includes('标识符无效'), invalid);
  await page.fill('#literature-query', '10.1038/nature14539');
  await page.click('#lookup'); await settled();
  await page.waitForTimeout(300);
  const lookup = await page.locator('#research-output .status-mark').getAttribute('data-status');
  const label = await page.textContent('#research-output .status-mark');
  await shot('16-literature-tool');
  return {invalid, doi_status: label.trim(), doi_evidence: lookup, note: 'Depends on live Crossref availability; the UI reports whichever status the service returned.'};
});

await step('snapshot-history-and-restore', async () => {
  await page.click('#rail-tab-history');
  await page.fill('#snapshot-message', 'Acceptance snapshot');
  await page.click('#snapshot'); await settled();
  await page.click('#history'); await settled();
  await page.waitForSelector('#history-output .history-row');
  const count = await page.locator('#history-output .history-row').count();
  assert(count >= 2, 'history lists snapshots');
  await page.click('#rail-tab-files');
  return {entries: count};
});

const accessibility = [];
await step('accessibility-axe-wcag-aa', async () => {
  await page.click('#view-source');
  accessibility.push(await axe('source+pdf+assistant'));
  for (const [view, name] of [['#view-review', 'review'], ['#view-workflow', 'workflow'], ['#view-evidence', 'evidence']]) { await page.click(view); accessibility.push(await axe(name)); }
  await page.click('#view-source');
  await page.evaluate(() => { document.documentElement.dataset.theme = 'dark'; });
  await page.waitForTimeout(150);
  accessibility.push(await axe('dark-theme'));
  await page.evaluate(() => { delete document.documentElement.dataset.theme; });
  const serious = accessibility.flatMap(item => item.violations.filter(violation => ['serious', 'critical'].includes(violation.impact)).map(violation => `${item.view}: ${violation.id} (${violation.sample})`));
  assert(!serious.length, serious.join('\n'));
  return {views: accessibility};
});

await step('keyboard-only-reachability', async () => {
  // Controls are disabled while a build runs, so reach them with the verification build finished.
  await page.waitForFunction(() => !['queued', 'running'].includes(document.getElementById('compile-state').dataset.build), null, {timeout: 120000});
  await page.focus('#project-switch');
  const reached = new Set();
  // Tab indents inside the editor, so Esc first releases focus as the shortcut sheet describes.
  for (let index = 0; index < 70; index++) {
    if (await page.evaluate(() => document.activeElement?.classList.contains('cm-content'))) await page.keyboard.press('Escape');
    await page.keyboard.press('Tab');
    reached.add(await page.evaluate(() => { const element = document.activeElement; return element?.id || element?.getAttribute('aria-label') || element?.className || element?.tagName; }));
  }
  const required = ['compile', 'view-review', 'toggle-assistant', 'rail-tab-outline', 'pdf-scroll', 'message'].filter(id => id !== 'message');
  for (const id of required) assert(reached.has(id), `Tab reaches ${id}; reached ${[...reached].join(', ')}`);
  return {distinct_stops: reached.size};
});

await step('typing-latency-on-large-document', async () => {
  const paragraph = 'The quick brown fox jumps over the lazy dog, repeatedly, so that the document is large enough to measure. ';
  const large = Array.from({length: 2000}, (_, index) => `% line ${index} ${paragraph}`).join('\n');
  await page.click('#view-source');
  await page.click('#rail-add'); await page.fill('#new-path', 'perf.tex'); await page.click('#new-file'); await settled();
  await focusEditor(); await page.keyboard.insertText(large);
  await page.waitForTimeout(300);
  await page.keyboard.press('Control+End');
  const measured = await page.evaluate(() => { window.__latency = []; const observer = new PerformanceObserver(list => { for (const entry of list.getEntries()) if (entry.name === 'keydown' || entry.name === 'beforeinput' || entry.name === 'input') window.__latency.push(entry.duration); }); observer.observe({type: 'event', durationThreshold: 16, buffered: false}); window.__latencyObserver = observer; return true; });
  const stamps = [];
  for (let index = 0; index < 40; index++) { const before = Date.now(); await page.keyboard.press('a'); await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => setTimeout(resolve)))); stamps.push(Date.now() - before); }
  stamps.sort((a, b) => a - b);
  const events = await page.evaluate(() => window.__latency);
  const result = {document_bytes: large.length, keystroke_to_paint_ms: {median: stamps[20], p95: stamps[37], max: stamps[39]}, event_timing_entries_over_16ms: events.length, slowest_event_ms: events.length ? Math.max(...events) : 0, measured};
  assert(stamps[37] < 100, 'p95 keystroke to paint under 100 ms: ' + JSON.stringify(result));
  // Discard this unsaved scratch buffer through the real delete control.
  await page.click('#delete-file'); await settled();
  await page.click('.tree-file:has-text("main.tex")'); await settled();
  return result;
});

await step('screenshots-themes-and-sizes', async () => {
  await page.click('#view-source');
  await waitForPdf();
  await shot('20-workbench-light');
  await page.click('#toggle-theme'); await page.waitForTimeout(200);
  await shot('21-workbench-dark');
  await page.click('#toggle-theme');
  await page.click('.assistant [aria-label="关闭研究助手"]').catch(() => {});
  await page.setViewportSize({width: 1280, height: 800}); await page.waitForTimeout(300);
  await shot('22-workbench-1280');
  const overflow1280 = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth);
  // Narrow windows keep navigation in a drawer.
  const navigationReachable = async () => {
    await page.click('#toggle-rail');
    await page.waitForFunction(() => document.querySelector('.rail').dataset.open === 'true');
    await page.waitForTimeout(300);
    await page.locator('.rail [role="tab"]').first().click();
    assert(await page.locator('.rail-panel').getByText('main.tex', {exact: true}).first().isVisible(), 'the file list is usable');
    await page.keyboard.press('Escape');
    await page.waitForFunction(() => document.querySelector('.rail').dataset.open === 'false');
  };
  await page.setViewportSize({width: 1024, height: 768}); await page.waitForTimeout(300);
  await navigationReachable();
  await shot('23-workbench-1024');
  await page.setViewportSize({width: 1600, height: 960});
  assert(!overflow1280, 'no horizontal page scroll');
});

// The drawer's state is remembered across reloads, so tests state what they want rather than toggle.
const setAssistant = async open => {
  if (await page.getAttribute('.assistant-host', 'data-open') !== String(open)) await page.click('#toggle-assistant');
  await page.waitForFunction(state => document.querySelector('.assistant-host').dataset.open === state, String(open));
  await page.waitForTimeout(350);
};

await step('english-interface-is-complete-and-survives-reload', async () => {
  await page.click('#toggle-language');
  await page.waitForLoadState('load');
  await page.waitForFunction(() => document.getElementById('source')?.editorView?.state.doc.length > 0);
  assert(await page.getAttribute('html', 'lang') === 'en', 'the document language follows the interface');
  const chinese = /[\u4e00-\u9fff]/;
  const views = ['source', 'review', 'workflow', 'evidence'];
  const leaks = {};
  await setAssistant(true);
  await page.click('#model-picker');
  for (const view of views) {
    await page.click('#view-' + view); await page.waitForTimeout(200);
    // User content and model answers are the author's own language, so only interface text counts.
    const lines = await page.evaluate(() => {
      const skip = '#source, .diff-table, .proposal-summary, .model-text, .run-result, #run-output, #events, #runs, #status, .field-text, .fields, #project-switch, .textLayer, #proposals dd, .record, #workflow-detail, .identity-line, #toggle-language, #message, #research-output';
      const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
      const found = [];
      while (walker.nextNode()) {
        const element = walker.currentNode.parentElement;
        if (element.closest(skip) || !element.checkVisibility()) continue;
        if (walker.currentNode.textContent.trim()) found.push(walker.currentNode.textContent.trim());
      }
      return found;
    });
    const leaked = lines.filter(line => chinese.test(line));
    if (leaked.length) leaks[view] = leaked;
  }
  assert(!Object.keys(leaks).length, 'Chinese left in the English interface: ' + JSON.stringify(leaks));
  await page.keyboard.press('Escape');
  await page.click('#view-source'); await page.waitForTimeout(250);
  await shot('25-workbench-english');
  await page.keyboard.press('Escape');
  await page.reload();
  await page.waitForFunction(() => document.getElementById('source')?.editorView?.state.doc.length > 0);
  assert((await page.textContent('#view-source')).includes('Source'), 'the choice survives a reload');
  await page.click('#toggle-language');
  await page.waitForLoadState('load');
  await page.waitForFunction(() => document.getElementById('source')?.editorView?.state.doc.length > 0);
  assert((await page.textContent('#view-source')).includes('源码'), 'switching back restores Chinese');
  await setAssistant(false);
});

await step('drawers-and-widgets-float-as-frosted-glass', async () => {
  const blurred = selector => page.locator(selector).first().evaluate(element => getComputedStyle(element).backdropFilter);
  await setAssistant(true);
  const drawer = await page.locator('.assistant-host').boundingBox();
  const viewer = await page.locator('.viewer').boundingBox();
  assert(drawer.x >= viewer.x && drawer.x + drawer.width <= 1600, 'the assistant is a drawer over the PDF, not a column');
  assert(/blur/.test(await blurred('.assistant-host')), 'the drawer is frosted');
  const before = await page.locator('.viewer').boundingBox();
  assert(before.width === viewer.width, 'opening the drawer does not resize the panes');
  await page.click('#model-picker');
  assert(/blur/.test(await blurred('#model-widget')), 'the model widget is frosted');
  await page.keyboard.press('Escape');
  assert(!(await page.isVisible('#model-widget')), 'Escape closes the widget first');
  assert(await page.isVisible('.assistant'), 'and leaves the drawer open');
  await page.keyboard.press('Control+Shift+A');
  await page.waitForFunction(() => document.querySelector('.assistant-host[data-open="false"]'));
  await page.keyboard.press('Control+Shift+A');
  await page.waitForFunction(() => document.querySelector('.assistant-host[data-open="true"]'));
  await page.focus('#intent'); await page.keyboard.press('Escape');
  await page.waitForFunction(() => document.querySelector('.assistant-host[data-open="false"]'));
  await page.keyboard.press('Control+j');
  await page.waitForFunction(() => document.querySelector('.drawer-log[data-open="true"]'));
  assert(/blur/.test(await blurred('.drawer-log')), 'the log drawer is frosted');
  await page.keyboard.press('Control+j');
  await page.waitForFunction(() => document.querySelector('.drawer-log[data-open="false"]'));
  await page.click('#project-switch');
  assert(/blur/.test(await blurred('.project-popover')), 'the project widget is frosted');
  await shot('26-project-widget');
  await page.mouse.click(800, 500);
  assert(!(await page.isVisible('.project-popover')), 'pressing outside closes the widget');
  const picker = await page.evaluate(() => CSS.supports('appearance', 'base-select'));
  return {styled_select_menus: picker};
});

await step('glass-keeps-text-readable-over-a-white-page', async () => {
  // Worst case: dark theme, drawer over the white PDF. Measure the real composited pixels.
  await page.click('#toggle-theme'); await setAssistant(true);
  const box = await page.locator('.assistant-host').boundingBox();
  const png = await page.screenshot({clip: {x: box.x + 3, y: box.y + box.height - 40, width: box.width - 6, height: 30}});
  const probe = await context.newPage();
  const brightest = await probe.evaluate(async base64 => {
    const blob = await (await fetch('data:image/png;base64,' + base64)).blob();
    const bitmap = await createImageBitmap(blob), canvas = new OffscreenCanvas(bitmap.width, bitmap.height), draw = canvas.getContext('2d');
    draw.drawImage(bitmap, 0, 0);
    const {data} = draw.getImageData(0, 0, bitmap.width, bitmap.height);
    let most = 0;
    for (let i = 0; i < data.length; i += 4) most = Math.max(most, data[i] * .2126 + data[i + 1] * .7152 + data[i + 2] * .0722);
    return most;
  }, png.toString('base64'));
  await probe.close();
  const muted = await page.locator('.assistant-host .selection-note').evaluate(element => getComputedStyle(element).color);
  const channel = value => { const c = value / 255; return c <= .03928 ? c / 12.92 : ((c + .055) / 1.055) ** 2.4; };
  const luminance = ([r, g, b]) => .2126 * channel(r) + .7152 * channel(g) + .0722 * channel(b);
  const text = luminance(muted.match(/[\d.]+/g).slice(0, 3).map(Number));
  const ground = luminance([brightest, brightest, brightest]);
  const contrast = (text + .05) / (ground + .05);
  await shot('27-dark-drawer-over-pdf');
  await setAssistant(false); await page.click('#toggle-theme');
  assert(contrast >= 4.5, `muted text over the frosted drawer: ${contrast.toFixed(2)}:1`);
  return {muted_text_contrast: Number(contrast.toFixed(2))};
});

await step('native-deep-link-opens-the-selected-project', async () => {
  // This is the exact URL form the GPUI window hands to the system browser.
  const id = await page.evaluate(async () => (await (await fetch('/api/projects')).json())[0].id);
  const name = await page.evaluate(async () => (await (await fetch('/api/projects')).json())[0].name);
  await page.goto(`${baseUrl}/#project=${id}`);
  await page.reload();
  await page.waitForFunction(() => document.getElementById('source')?.editorView?.state.doc.length > 0);
  assert((await page.textContent('#project-switch')).trim() === name, 'the deep link opens that project');
});

await step('no-csp-violations-or-page-errors', async () => {
  assert(!cspViolations.length, 'CSP violations: ' + cspViolations.join(' | '));
  // A failed or conflicting request logs a console error by design; uncaught errors never are.
  const unexpected = consoleProblems.filter(item => !/Failed to load resource|Applying inline style violates/.test(item));
  assert(!unexpected.length, unexpected.join(' | '));
  assert(!httpErrors.length, 'server errors: ' + httpErrors.join(' | '));
  return {console_resource_errors: consoleProblems.length, csp_violations: cspViolations.length};
});

const sizes = {};
for (const file of ['web/app.js', 'web/workbench.css', 'web/pdf.worker.js']) sizes[file] = (await stat(new URL('../' + file, import.meta.url))).size;
await browser.close();
const summary = {passed: results.filter(item => item.status === 'passed').length, failed: results.filter(item => item.status === 'failed').length, not_run: results.filter(item => item.status === 'not_run').length};
await writeFile(new URL('../reports/frontend-acceptance.json', import.meta.url), JSON.stringify({
  evidence_class: 'real_browser_acceptance', browser: 'system Microsoft Edge via playwright-core (headless)', base_url: baseUrl, at: new Date().toISOString(),
  summary, results, sizes, csp_violations: cspViolations, provider: runProvider,
}, null, 2) + '\n', 'utf8');
console.log(JSON.stringify(summary));
process.exit(summary.failed ? 1 : 0);
