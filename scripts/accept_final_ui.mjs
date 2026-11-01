// Final visual and layout acceptance against a running isolated service, in system Edge.
// Complements accept_frontend.mjs, which covers the functional loop. No model is called here;
// the review states come from real proposals and real Docker builds.
//   node scripts/accept_final_ui.mjs --url http://127.0.0.1:8791
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { chromium } from 'playwright-core';

const argument = (name, fallback) => { const index = process.argv.indexOf(name); return index > 0 ? process.argv[index + 1] : fallback; };
const baseUrl = argument('--url', 'http://127.0.0.1:8791');
const outputDir = new URL('../reports/final-ui/', import.meta.url);
await mkdir(outputDir, {recursive: true});
const axeSource = await readFile(createRequire(import.meta.url).resolve('axe-core/axe.min.js'), 'utf8');

const browser = await chromium.launch({channel: 'msedge', headless: true});
const context = await browser.newContext({viewport: {width: 1600, height: 960}, colorScheme: 'light', locale: 'zh-CN'});
const page = await context.newPage();
const cdp = await context.newCDPSession(page);
const results = [], csp = [], pageErrors = [], screenshots = [];
page.on('console', message => { if (message.text().startsWith('CSPV ')) csp.push(message.text().slice(5)); });
page.on('pageerror', error => pageErrors.push(error.message));
page.on('dialog', dialog => dialog.accept());
await page.addInitScript(() => document.addEventListener('securitypolicyviolation', event => console.log('CSPV', event.violatedDirective, event.sourceFile, event.lineNumber, (event.target?.outerHTML || '').slice(0, 100))));

const shot = async name => { await page.waitForTimeout(380); screenshots.push(`${name}.jpg`); await page.screenshot({path: new URL(`${name}.jpg`, outputDir).pathname.replace(/^\/([A-Za-z]:)/, '$1'), type: 'jpeg', quality: 84}); };
const assert = (condition, message) => { if (!condition) throw new Error(message); };
const settled = () => page.waitForFunction(() => document.getElementById('message')?.textContent !== '处理中…');
const size = async (width, height) => { await page.setViewportSize({width, height}); await page.waitForTimeout(350); };
const box = selector => page.locator(selector).first().boundingBox();
const overflow = () => page.evaluate(() => document.documentElement.scrollWidth > innerWidth);
const theme = async value => { await page.evaluate(next => { document.documentElement.dataset.theme = next; }, value); await page.waitForTimeout(120); };
const assistantState = async open => {
  if (await page.getAttribute('.assistant-host', 'data-open') !== String(open)) await page.click('#toggle-assistant');
  await page.waitForFunction(state => document.querySelector('.assistant-host').dataset.open === state, String(open));
  await page.waitForTimeout(340);
};
const railState = async open => {
  if (await page.getAttribute('.rail', 'data-open') !== String(open)) await page.click('#toggle-rail');
  await page.waitForFunction(state => document.querySelector('.rail').dataset.open === state, String(open));
  await page.waitForTimeout(300);
};
const view = async name => { await page.click('#view-' + name); await page.waitForTimeout(120); };
const axe = async () => {
  await page.evaluate(axeSource);
  return page.evaluate(async () => (await window.axe.run(document, {runOnly: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']})).violations.map(item => ({id: item.id, impact: item.impact, nodes: item.nodes.length, sample: item.nodes[0]?.target.join(' ')})));
};
async function step(name, run) {
  try { const detail = await run(); results.push({name, status: 'PASS', detail: detail ?? null}); console.log('PASS', name); }
  catch (error) { results.push({name, status: 'FAIL', error: String(error.message || error).slice(0, 500)}); console.log('FAIL', name, String(error.message || error).slice(0, 260)); await page.screenshot({path: new URL(`failure-${name.replace(/\W+/g, '-')}.jpg`, outputDir).pathname.replace(/^\/([A-Za-z]:)/, '$1'), type: 'jpeg', quality: 70}).catch(() => {}); }
}

const main = String.raw`\documentclass{article}
\usepackage{amsmath,amssymb}
\newtheorem{theorem}{Theorem}
\title{Bounded Sums and Their Tails}
\author{Lemfolio acceptance}
\begin{document}
\maketitle
\begin{abstract}
A short note on bounded partial sums, used only for interface acceptance.
\end{abstract}
\section{Introduction}\label{sec:intro}
We study partial sums and refer to Theorem~\ref{thm:main} in Section~\ref{sec:method}.
${Array.from({length: 26}, (_, index) => `Sentence ${index + 1} is ordinary prose that gives the page enough text to sit behind a frosted drawer.`).join('\n')}
\input{sections/background}
\section{Method}\label{sec:method}
Marker paragraph describes the method used here.
\begin{theorem}\label{thm:main}
For every integer $n \ge 1$,
\begin{equation}\label{eq:sum}
\sum_{k=1}^{n} k = \frac{n(n+1)}{2}.
\end{equation}
\end{theorem}
\bibliographystyle{plain}
\bibliography{references}
\end{document}
`;
const background = String.raw`\subsection{Background}
Background sentence from the included file, cited as \cite{knuth}.
`;
const bibliography = `@book{knuth,\n  title = {The Art of Computer Programming},\n  author = {Knuth, Donald E.},\n  year = {1968}\n}\n`;
const api = (path, method = 'GET', body) => page.evaluate(async ([path, method, body]) => {
  const session = await (await fetch('/api/session')).json();
  const response = await fetch(path, {method, headers: {'content-type': 'application/json', 'x-csrf-token': session.csrf}, body: body === undefined ? undefined : JSON.stringify(body)});
  const value = await response.json();
  if (!response.ok) throw new Error(`${method} ${path}: ${response.status} ${JSON.stringify(value)}`);
  return value;
}, [path, method, body]);
let projectId = '';

await step('prepare-real-project-and-build', async () => {
  await page.goto(baseUrl);
  await page.waitForFunction(() => document.getElementById('message')?.textContent === '本地会话已建立');
  const project = await api('/api/projects', 'POST', {name: `最终验收 ${new Date().toISOString().slice(0, 16)}`});
  projectId = project.id;
  const route = `/api/projects/${project.id}`;
  const existing = await api(route + '/file?path=main.tex');
  await api(route + '/file', 'PUT', {path: 'main.tex', content: main, expected_hash: existing.sha256});
  await api(route + '/file', 'PUT', {path: 'sections/background.tex', content: background});
  const bib = await api(route + '/files');
  const bibFile = bib.find(item => item.path === 'references.bib');
  await api(route + '/file', 'PUT', {path: 'references.bib', content: bibliography, ...(bibFile ? {expected_hash: bibFile.sha256} : {})});
  await page.goto(`${baseUrl}/#project=${project.id}`); await page.reload();
  await page.waitForFunction(() => document.getElementById('source')?.editorView?.state.doc.length > 0);
  await settled();
  await page.click('#compile');
  await page.waitForFunction(() => !['queued', 'running', 'none'].includes(document.getElementById('compile-state').dataset.build), null, {timeout: 180000});
  assert(await page.getAttribute('#compile-state', 'data-build') === 'success', 'the paper compiles');
  await page.waitForSelector('.pdf-page canvas', {timeout: 30000});
  return {project: project.id};
});

const widths = [[1600, 960], [1280, 800], [1100, 800], [1024, 768]];
for (const [width, height] of widths) await step(`layout-${width}`, async () => {
  await size(width, height);
  const source = await box('.center'), viewer = await box('.viewer');
  assert(!(await overflow()), 'no horizontal page scroll');
  assert(source.width >= 380, `source keeps a working width: ${source.width}`);
  assert(viewer.width >= 280, `PDF keeps a working width: ${viewer.width}`);
  assert(Math.abs(source.y - viewer.y) < 2, 'source and PDF sit side by side');
  assert(source.height > height - 110, `source keeps the height: ${source.height}`);
  const drawer = width <= 1180;
  assert((await page.locator('.rail[data-drawer]').count() === 1) === drawer, 'the rail is a drawer only in narrow windows');
  await shot(`layout-${width}-light`);
  if (drawer) {
    await railState(true);
    assert(/blur/.test(await page.locator('.rail').evaluate(element => getComputedStyle(element).backdropFilter)), 'the rail drawer is frosted');
    assert((await box('.center')).width === source.width, 'opening navigation does not resize the panes');
    await shot(`layout-${width}-navigation`);
    await page.click('#rail-tab-outline');
    await page.waitForSelector('.outline-row:has-text("Method")');
    await page.click('.outline-row:has-text("Method")'); await settled();
    await page.waitForFunction(() => document.querySelector('.rail').dataset.open === 'false');
    await page.click('#toggle-rail');
    await page.click('#rail-tab-files');
    await page.keyboard.press('Escape');
    await page.waitForFunction(() => document.querySelector('.rail').dataset.open === 'false');
  }
  return {source_width: Math.round(source.width), pdf_width: Math.round(viewer.width), source_height: Math.round(source.height)};
});

await step('resizing-keeps-the-paper-state', async () => {
  await size(1600, 960);
  await page.click('.cm-content'); await page.keyboard.press('Control+End'); await page.keyboard.type('% draft across widths');
  const read = () => page.evaluate(() => ({doc: document.getElementById('source').editorView.state.doc.length, head: document.getElementById('source').editorView.state.selection.main.head, pages: document.querySelectorAll('.pdf-page').length, build: document.getElementById('pdf').dataset.src}));
  const before = await read();
  await page.evaluate(() => { window.__sameDocument = true; });
  for (const [width, height] of [[1280, 800], [1100, 800], [1024, 768], [1280, 800], [1600, 960]]) {
    await size(width, height);
    const now = await read();
    assert(now.doc === before.doc && now.head === before.head, `editor state at ${width}: ${JSON.stringify(now)}`);
    assert(now.build === before.build && now.pages === before.pages, `PDF state at ${width}: ${JSON.stringify(now)}`);
  }
  assert(await page.evaluate(() => window.__sameDocument === true), 'no reload happened');
  assert((await page.evaluate(() => document.getElementById('source').editorView.state.doc.toString())).endsWith('% draft across widths'), 'the draft survives');
  await page.keyboard.press('Control+Z'); await page.waitForTimeout(80);
  while (await page.getAttribute('#save-state', 'data-state') === 'dirty') { await page.keyboard.press('Control+Z'); await page.waitForTimeout(40); }
  return before;
});

let drawerShots = false;
for (const name of ['light', 'dark']) await step(`glass-over-real-content-${name}`, async () => {
  await size(1600, 960); await theme(name);
  await assistantState(true);
  await shot(`glass-assistant-over-pdf-${name}`);
  const drawer = await box('.assistant-host');
  const filter = await page.locator('.assistant-host').evaluate(element => { const style = getComputedStyle(element); return {filter: style.backdropFilter, background: style.backgroundColor}; });
  assert(/blur/.test(filter.filter), 'drawer is frosted');
  await page.click('#mode-math');
  await page.fill('#math-lhs', '(x+1)**2'); await page.fill('#math-rhs', 'x**2+2*x+1'); await page.click('#math'); await settled();
  await page.waitForSelector('#research-output .status-mark');
  await shot(`glass-assistant-tool-result-${name}`);
  await page.click('#mode-edit');
  await page.click('#model-picker'); await page.waitForSelector('#model-widget:not([hidden])');
  await shot(`glass-model-picker-${name}`);
  await page.keyboard.press('Escape');
  await assistantState(false);
  await page.click('#toggle-dock');
  await page.waitForFunction(() => document.querySelector('.drawer-log[data-open="true"]'));
  await shot(`glass-compile-log-over-source-${name}`);
  await page.click('#toggle-dock');
  await page.click('.cm-content'); await page.keyboard.press('Control+F'); await page.waitForSelector('.cm-search');
  await page.fill('.cm-search input[name="search"]', 'prose');
  await shot(`glass-search-over-source-${name}`);
  await page.keyboard.press('Escape');
  await page.click('#project-switch'); await shot(`glass-project-widget-${name}`); await page.mouse.click(800, 600);
  // Main panes stay opaque, and only floating surfaces carry the blur.
  const panes = await page.evaluate(() => ['.center', '.viewer', '.pdf-page', '#source .cm-editor'].map(selector => getComputedStyle(document.querySelector(selector)).backdropFilter));
  assert(panes.every(value => value === 'none'), 'panes never blur: ' + panes);
  drawerShots = true;
  return {drawer_width: Math.round(drawer.width), ...filter};
});

await step('muted-text-contrast-on-composited-glass', async () => {
  const channel = value => { const c = value / 255; return c <= .03928 ? c / 12.92 : ((c + .055) / 1.055) ** 2.4; };
  const luminance = ([r, g, b]) => .2126 * channel(r) + .7152 * channel(g) + .0722 * channel(b);
  const measured = {};
  for (const name of ['light', 'dark']) {
    await theme(name); await assistantState(true);
    const area = await box('.assistant-host');
    const png = await page.screenshot({clip: {x: area.x + 3, y: area.y + area.height - 34, width: area.width - 6, height: 26}});
    const probe = await context.newPage();
    const pixels = await probe.evaluate(async base64 => {
      const bitmap = await createImageBitmap(await (await fetch('data:image/png;base64,' + base64)).blob()), canvas = new OffscreenCanvas(bitmap.width, bitmap.height), draw = canvas.getContext('2d');
      draw.drawImage(bitmap, 0, 0);
      const {data} = draw.getImageData(0, 0, bitmap.width, bitmap.height);
      let most = 0, least = 255;
      for (let i = 0; i < data.length; i += 4) { const value = data[i] * .2126 + data[i + 1] * .7152 + data[i + 2] * .0722; most = Math.max(most, value); least = Math.min(least, value); }
      return {most, least};
    }, png.toString('base64'));
    await probe.close();
    const text = await page.locator('.assistant-host .selection-note').evaluate(element => getComputedStyle(element).color);
    const ink = luminance(text.match(/[\d.]+/g).slice(0, 3).map(Number));
    const ground = name === 'dark' ? luminance([pixels.most, pixels.most, pixels.most]) : luminance([pixels.least, pixels.least, pixels.least]);
    const contrast = (Math.max(ink, ground) + .05) / (Math.min(ink, ground) + .05);
    measured[name] = Number(contrast.toFixed(2));
    assert(contrast >= 4.5, `${name}: ${contrast.toFixed(2)}:1`);
  }
  await assistantState(false); await theme('light');
  return measured;
});

await step('diff-conflict-and-decisions-come-from-the-backend', async () => {
  await size(1600, 960); await theme('light');
  await view('source');
  const route = `/api/projects/${projectId}`;
  const project = await api(route);
  const file = await api(route + '/file?path=main.tex'), bg = await api(route + '/file?path=sections/background.tex');
  const proposal = await api(route + '/proposals', 'POST', {base_revision: project.revision, base_state_revision: project.state_revision, summary: 'Rename the marker in two files', changes: [{path: 'main.tex', content: file.content.replace('Marker paragraph', 'Tagged paragraph')}, {path: 'sections/background.tex', content: bg.content.replace('Background sentence', 'Context sentence')}]});
  await page.click('#view-review'); await page.click('#refresh-proposals'); await settled();
  await page.waitForSelector('#proposals details[open] .diff-file');
  await page.waitForFunction(() => document.querySelector('.diff-engine')?.textContent.includes('Keel Rust'), null, {timeout: 8000});
  const chips = await page.locator('#proposals details[open] .diff-nav button').count();
  assert(chips === 2, 'two files in the navigation, got ' + chips);
  assert(await page.locator('#proposals details[open] mark').count() >= 2, 'intra-line marks');
  await shot('diff-review-light'); await theme('dark'); await shot('diff-review-dark'); await theme('light');
  await page.locator('#proposals details[open] .diff-nav button').nth(1).click();
  await page.waitForTimeout(150);
  const target = await box('#proposals details[open] [data-file="sections/background.tex"]'), pane = await box('#pane-review');
  assert(target.y < pane.y + pane.height, 'the file button brings that file into view');
  // Change the source for real after the proposal exists.
  await api(route + '/file', 'PUT', {path: 'main.tex', content: file.content.replace('Sentence 1 is', 'Sentence one is'), expected_hash: file.sha256});
  await page.reload();
  await page.waitForFunction(() => document.getElementById('source')?.editorView?.state.doc.length > 0); await settled();
  await page.click('#view-review');
  await page.waitForSelector('#proposals details[open] .conflict-drift .diff-file', {timeout: 10000});
  const accept = page.locator('#proposals details[open] button:has-text("接受此摘要绑定的修改")');
  assert(await accept.isDisabled(), 'stale acceptance is disabled');
  const conflict = await api(route + `/proposals/${proposal.id}/conflict`);
  assert(conflict.stale && conflict.files.find(item => item.path === 'main.tex').changed && !conflict.files.find(item => item.path === 'sections/background.tex').changed, 'the backend names the file that moved');
  const shown = await page.locator('.conflict-drift').textContent();
  assert(shown.includes('Sentence one') && !shown.includes('Tagged'), 'the panel shows the real drift, not the proposal');
  await shot('diff-stale-conflict-light'); await theme('dark'); await shot('diff-stale-conflict-dark'); await theme('light');
  // API-level check, separate from the disabled button.
  const refused = await page.evaluate(async ([route, id, digest]) => { const session = await (await fetch('/api/session')).json(); return (await fetch(`${route}/proposals/${id}/decide`, {method: 'POST', headers: {'content-type': 'application/json', 'x-csrf-token': session.csrf}, body: JSON.stringify({decision: 'accept', digest, engine: 'pdflatex'})})).status; }, [route, proposal.id, proposal.digest]);
  assert(refused === 409, 'the service refuses it too: ' + refused);
  await page.click('#proposals details[open] button:has-text("拒绝修改")'); await settled();
  await page.waitForFunction(() => document.querySelector('#proposals details[open] .proposal-outcome'));
  await shot('diff-rejected-outcome');
  const after = await api(route + '/file?path=main.tex');
  assert(after.content.includes('Sentence one is') && !after.content.includes('Tagged'), 'the file holds the later edit only');
  return {api_status_for_stale_accept: refused, changed: conflict.files.map(item => [item.path, item.changed])};
});

await step('accepted-outcome-separates-apply-and-verification', async () => {
  const route = `/api/projects/${projectId}`;
  const project = await api(route), file = await api(route + '/file?path=main.tex');
  await api(route + '/proposals', 'POST', {base_revision: project.revision, base_state_revision: project.state_revision, summary: 'Restore the numeral', changes: [{path: 'main.tex', content: file.content.replace('Sentence one is', 'Sentence 1 is')}]});
  await page.click('#refresh-proposals'); await settled();
  await page.waitForSelector('#proposals details[open] button.primary:not([disabled])');
  await page.click('#proposals details[open] button.primary');
  await page.waitForFunction(() => /修改已应用/.test(document.getElementById('message').textContent), null, {timeout: 30000});
  await page.waitForSelector('#proposals details[open] .proposal-outcome');
  const text = await page.locator('#proposals details[open] .proposal-outcome').textContent();
  assert(text.includes('已接受并应用') && text.includes('独立验证'), 'both results are named: ' + text);
  await shot('diff-accepted-outcome');
  return {outcome: text};
});

for (const name of ['workflow', 'evidence']) await step(`pane-${name}`, async () => {
  await view(name); await page.waitForTimeout(300);
  await shot(`${name}-light`); await theme('dark'); await shot(`${name}-dark`); await theme('light');
  await size(1024, 768); await shot(`${name}-1024`); assert(!(await overflow()), 'no overflow at 1024');
  await size(1600, 960);
});

await step('failed-build-opens-log-and-clears-preview', async () => {
  await view('source');
  const route = `/api/projects/${projectId}`;
  await page.waitForFunction(() => !['queued', 'running'].includes(document.getElementById('compile-state').dataset.build), null, {timeout: 120000});
  const file = await api(route + '/file?path=sections/background.tex');
  await api(route + '/file', 'PUT', {path: 'sections/background.tex', content: file.content + '\\undefinedcommandxyz\n', expected_hash: file.sha256});
  await page.reload(); await page.waitForFunction(() => document.getElementById('source')?.editorView?.state.doc.length > 0); await settled();
  await page.click('#compile');
  await page.waitForFunction(() => !['queued', 'running', 'none'].includes(document.getElementById('compile-state').dataset.build), null, {timeout: 180000});
  assert(await page.getAttribute('#compile-state', 'data-build') === 'failed', 'the build fails');
  assert(await page.locator('.pdf-page').count() === 0, 'no old pages stay');
  await page.waitForSelector('.drawer-log[data-open="true"] .problems [data-severity="error"]');
  await shot('compile-failed-log-open');
  const repaired = await api(route + '/file?path=sections/background.tex');
  await api(route + '/file', 'PUT', {path: 'sections/background.tex', content: file.content, expected_hash: repaired.sha256});
  await page.reload(); await page.waitForFunction(() => document.getElementById('source')?.editorView?.state.doc.length > 0); await settled();
  await page.click('#compile');
  await page.waitForFunction(() => document.getElementById('compile-state').dataset.build === 'success', null, {timeout: 180000});
  await page.waitForSelector('.pdf-page canvas', {timeout: 30000});
});

await step('reduced-transparency-is-opaque', async () => {
  await cdp.send('Emulation.setEmulatedMedia', {features: [{name: 'prefers-reduced-transparency', value: 'reduce'}]});
  await assistantState(true);
  const style = await page.locator('.assistant-host').evaluate(element => { const value = getComputedStyle(element); return {filter: value.backdropFilter, background: value.backgroundColor}; });
  assert(style.filter === 'none', 'no blur: ' + style.filter);
  assert(!/rgba\(.*,\s*0?\.\d+\)/.test(style.background), 'an opaque ground: ' + style.background);
  await shot('reduced-transparency-light');
  await assistantState(false);
  await cdp.send('Emulation.setEmulatedMedia', {features: []});
  return style;
});

await step('reduced-motion-removes-transitions', async () => {
  await page.emulateMedia({reducedMotion: 'reduce'});
  const value = await page.locator('.assistant-host').evaluate(element => getComputedStyle(element).transitionProperty + '|' + getComputedStyle(element).transitionDuration);
  await page.emulateMedia({reducedMotion: 'no-preference'});
  assert(/none|0s|1e-05s/.test(value), value);
  return value;
});

await step('focus-returns-from-drawers', async () => {
  await page.focus('#toggle-assistant');
  await page.keyboard.press('Enter'); await page.waitForFunction(() => document.querySelector('.assistant-host').dataset.open === 'true'); await page.waitForTimeout(340);
  assert(await page.evaluate(() => document.activeElement?.id === 'intent'), 'opening moves focus into the drawer');
  await page.keyboard.press('Escape'); await page.waitForFunction(() => document.querySelector('.assistant-host').dataset.open === 'false');
  assert(await page.evaluate(() => document.activeElement?.id === 'toggle-assistant'), 'closing returns focus to its button');
  await size(1024, 768);
  await page.focus('#toggle-rail');
  await page.keyboard.press('Enter'); await page.waitForFunction(() => document.querySelector('.rail').dataset.open === 'true'); await page.waitForTimeout(280);
  assert(await page.evaluate(() => document.activeElement?.closest('.rail') !== null), 'opening moves focus into the navigation');
  await page.keyboard.press('Escape'); await page.waitForFunction(() => document.querySelector('.rail').dataset.open === 'false');
  assert(await page.evaluate(() => document.activeElement?.id === 'toggle-rail'), 'closing returns focus to its button');
  await page.keyboard.press('Control+b'); await page.waitForFunction(() => document.querySelector('.rail').dataset.open === 'true');
  await page.keyboard.press('Control+b'); await page.waitForFunction(() => document.querySelector('.rail').dataset.open === 'false');
  await size(1600, 960);
});

await step('ime-composition-at-medium-width', async () => {
  await size(1024, 768); await view('source'); await page.click('.cm-content'); await page.keyboard.press('Control+End');
  const before = await page.evaluate(() => document.getElementById('source').editorView.state.doc.length);
  for (const text of ['y', 'yi', 'yin', 'yinl', 'yinli']) await cdp.send('Input.imeSetComposition', {text, selectionStart: text.length, selectionEnd: text.length});
  await cdp.send('Input.imeSetComposition', {text: '引理', selectionStart: 2, selectionEnd: 2});
  await cdp.send('Input.insertText', {text: '引理'}); await page.waitForTimeout(100);
  const doc = await page.evaluate(() => document.getElementById('source').editorView.state.doc.toString());
  assert(doc.endsWith('引理') && doc.length === before + 2, 'composition commits once');
  await page.keyboard.press('Control+Z'); await page.waitForTimeout(80);
  await size(1600, 960);
  return {method: 'CDP Input.imeSetComposition', physical_ime: 'NOT_RUN'};
});

await step('multi-line-input-causes-no-csp-violation', async () => {
  const before = csp.length;
  await page.click('.cm-content'); await page.keyboard.press('Control+End');
  await page.keyboard.insertText('% one\n% two\n% three\n');
  await page.waitForTimeout(120);
  assert((await page.evaluate(() => document.getElementById('source').editorView.state.doc.toString())).endsWith('% one\n% two\n% three\n'), 'the text arrives');
  assert(csp.length === before, 'blocked: ' + csp.slice(before).join(' | '));
  while (await page.getAttribute('#save-state', 'data-state') === 'dirty') { await page.keyboard.press('Control+Z'); await page.waitForTimeout(40); }
});

await step('axe-all-views-widths-themes', async () => {
  const all = [], problems = [];
  for (const [width, height] of [[1600, 960], [1024, 768]]) for (const name of ['light', 'dark']) {
    await size(width, height); await theme(name);
    for (const tab of ['source', 'review', 'workflow', 'evidence']) {
      await view(tab);
      const violations = await axe();
      all.push({width, theme: name, view: tab, violations});
      for (const item of violations) if (['serious', 'critical'].includes(item.impact)) problems.push(`${width} ${name} ${tab}: ${item.id} ${item.sample}`);
    }
  }
  await assistantState(true); all.push({width: 1024, theme: 'dark', view: 'assistant', violations: await axe()}); await assistantState(false);
  await size(1600, 960); await theme('light'); await view('source');
  assert(!problems.length, problems.join('\n'));
  return {checked: all.length, total_violations: all.reduce((sum, item) => sum + item.violations.length, 0), minor: all.flatMap(item => item.violations.map(v => `${item.width} ${item.view}: ${v.id}`)).slice(0, 12)};
});

await step('accessibility-tree-semantics', async () => {
  await assistantState(true);
  const {nodes} = await cdp.send('Accessibility.getFullAXTree');
  const roles = {};
  for (const node of nodes) if (!node.ignored && node.role?.value) (roles[node.role.value] ??= []).push(node.name?.value || '');
  await assistantState(false);
  for (const role of ['navigation', 'main', 'complementary', 'tablist', 'tab', 'tabpanel', 'status', 'radiogroup']) assert(roles[role]?.length, 'missing role ' + role);
  const unnamed = nodes.filter(node => !node.ignored && ['button', 'textbox', 'combobox', 'radio', 'tab'].includes(node.role?.value) && !node.name?.value);
  assert(!unnamed.length, 'unnamed controls: ' + unnamed.map(node => node.role.value + ' ' + node.backendDOMNodeId).join());
  return {method: 'Chromium accessibility tree; not a screen reader', roles: Object.fromEntries(Object.entries(roles).filter(([role]) => ['navigation', 'main', 'complementary', 'tablist', 'tabpanel', 'status', 'radiogroup'].includes(role)).map(([role, names]) => [role, names.slice(0, 5)]))};
});

await step('no-csp-violations-or-page-errors', async () => {
  assert(!csp.length, 'CSP violations: ' + csp.slice(0, 5).join(' | '));
  assert(!pageErrors.length, pageErrors.join(' | '));
  const header = (await (await page.request.get(baseUrl + '/')).headers())['content-security-policy'];
  assert(header.includes("script-src 'self'") && header.includes("style-src 'self'") && !header.includes('unsafe'), header);
  return {csp: header, violations: 0};
});

await browser.close();
const notRun = [
  {name: 'physical-chinese-ime', status: 'NOT_RUN', reason: 'Composition was driven through CDP Input.imeSetComposition. No physical Windows IME session was run.'},
  {name: 'screen-reader', status: 'NOT_RUN', reason: 'No NVDA, Narrator or VoiceOver session. axe and the Chromium accessibility tree do not replace one.'},
  {name: 'other-browsers', status: 'NOT_RUN', reason: 'Firefox and Safari were not run; the no-backdrop-filter fallback was reviewed in CSS, not seen in a browser that lacks the feature.'},
  {name: 'os-reduced-transparency', status: 'NOT_RUN', reason: 'prefers-reduced-transparency was emulated through CDP, not set in Windows.'},
];
const summary = {passed: results.filter(item => item.status === 'PASS').length, failed: results.filter(item => item.status === 'FAIL').length, not_run: notRun.length};
await writeFile(new URL('../reports/final-ui-acceptance.json', import.meta.url), JSON.stringify({
  evidence_class: 'real_browser_acceptance', browser: 'system Microsoft Edge via playwright-core (headless)', base_url: baseUrl, at: new Date().toISOString(),
  commit: argument('--commit', null), summary, results, not_run: notRun, csp_violations: csp, screenshots: screenshots.map(name => `reports/final-ui/${name}`),
  functional_regression: 'See reports/frontend-acceptance.json for the functional loop run against the same build.',
}, null, 2) + '\n', 'utf8');
console.log(JSON.stringify(summary));
process.exit(summary.failed ? 1 : 0);
