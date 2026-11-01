// Derive every application icon from the two brand drawings in assets/brand, and copy
// the line icons GPUI embeds. Run after changing either: node scripts/build_icons.mjs
import { copyFile, mkdir, readFile, writeFile } from 'node:fs/promises';
import { chromium } from 'playwright-core';

const root = new URL('../', import.meta.url);
const path = relative => new URL(relative, root);
const large = await readFile(path('assets/brand/lemfolio.svg'), 'utf8');
const small = await readFile(path('assets/brand/lemfolio-small.svg'), 'utf8');

const browser = await chromium.launch({channel: 'msedge', headless: true});
const page = await browser.newPage({viewport: {width: 256, height: 256}});
const render = async (svg, size) => {
  await page.setViewportSize({width: size, height: size});
  await page.setContent(`<!doctype html><html style="margin:0;background:transparent"><body style="margin:0;background:transparent;width:${size}px;height:${size}px">${svg.replace('<svg ', `<svg width="${size}" height="${size}" style="display:block" `)}</body></html>`);
  return page.screenshot({omitBackground: true, type: 'png'});
};

// Small sizes use the simplified drawing, because the fold and serif turn to noise below 32 px.
const frames = [];
for (const size of [16, 20, 24]) frames.push([size, await render(small, size)]);
for (const size of [32, 40, 48, 64, 128, 256]) frames.push([size, await render(large, size)]);
await browser.close();

const head = Buffer.alloc(6 + frames.length * 16);
head.writeUInt16LE(1, 2); head.writeUInt16LE(frames.length, 4);
let offset = head.length;
frames.forEach(([size, png], index) => {
  const at = 6 + index * 16;
  head[at] = size === 256 ? 0 : size; head[at + 1] = size === 256 ? 0 : size;
  head.writeUInt16LE(1, at + 4); head.writeUInt16LE(32, at + 6);
  head.writeUInt32LE(png.length, at + 8); head.writeUInt32LE(offset, at + 12);
  offset += png.length;
});
await mkdir(path('native/assets/brand'), {recursive: true});
await mkdir(path('native/assets/icons'), {recursive: true});
await mkdir(path('reports/frontend'), {recursive: true});
await writeFile(path('native/assets/brand/lemfolio.ico'), Buffer.concat([head, ...frames.map(([, png]) => png)]));
await writeFile(path('native/assets/brand/lemfolio-64.png'), frames.find(([size]) => size === 64)[1]);
await writeFile(path('web/favicon.svg'), large);
await writeFile(path('reports/frontend/brand-icon-256.png'), frames.find(([size]) => size === 256)[1]);
await writeFile(path('reports/frontend/brand-icon-16.png'), frames.find(([size]) => size === 16)[1]);

for (const name of ['folder-open', 'plus', 'refresh-cw', 'log-out', 'file-code-2', 'server', 'terminal', 'box', 'files',
  'arrow-left', 'arrow-up', 'book-open-text', 'check', 'chevron-down', 'chevron-right', 'chevrons-up-down', 'clipboard-check', 'code-xml', 'file', 'file-diff', 'folder', 'folder-input', 'messages-square', 'pen-line', 'play', 'save', 'scroll-text', 'shield-check', 'sigma', 'sparkles', 'square', 'workflow', 'x', 'zoom-in', 'zoom-out']) {
  await copyFile(path(`node_modules/lucide-static/icons/${name}.svg`), path(`native/assets/icons/${name}.svg`));
}
console.log(`Built ${frames.length} icon frames and the GPUI line icons`);
