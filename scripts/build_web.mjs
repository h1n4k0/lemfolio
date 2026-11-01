// Bundle the React workspace for the strict same-origin CSP: every script, worker
// and font is served from web/, and PDF.js loads as its own chunk on first use.
import { copyFile, mkdir, readFile, readdir, rm, writeFile } from 'node:fs/promises';
import { build } from 'esbuild';

const root = new URL('../', import.meta.url);
const path = relative => new URL(relative, root);

await rm(path('web/chunks'), {recursive: true, force: true});
const bundle = await build({
  absWorkingDir: decodeURIComponent(root.pathname.replace(/^\/([A-Za-z]:)/, '$1')),
  entryPoints: {app: 'frontend/app.tsx'}, outdir: 'web', chunkNames: 'chunks/[name]-[hash]',
  bundle: true, splitting: true, format: 'esm', target: 'es2022', minify: true, legalComments: 'none', metafile: true, logLevel: 'warning',
});

// Served as .js: Python maps .mjs to text/plain on Windows and nosniff then refuses it.
await copyFile(path('node_modules/pdfjs-dist/build/pdf.worker.min.mjs'), path('web/pdf.worker.js'));

await mkdir(path('web/fonts'), {recursive: true});
const keelFonts = ['KeelSans-Regular', 'KeelSans-Medium', 'KeelSans-SemiBold', 'KeelDisplay-Regular', 'KeelMono-Regular'];
// The interface uses only Keel; film/ still typesets its annotations with these two.
const fonts = {
  'ibm-plex-mono': ['latin-500-normal'],
  'source-serif-4': ['latin-600-normal'],
};
const wanted = new Set();
for (const face of keelFonts) {
  const name = `${face}.woff2`;
  wanted.add(name);
  await copyFile(path(`assets/fonts/keel/woff2/${name}`), path(`web/fonts/${name}`));
}
for (const [family, faces] of Object.entries(fonts)) {
  for (const face of faces) {
    const name = `${family}-${face}.woff2`;
    wanted.add(name);
    await copyFile(path(`node_modules/@fontsource/${family}/files/${name}`), path(`web/fonts/${name}`));
  }
}
for (const name of await readdir(path('web/fonts'))) if (name.endsWith('.woff2') && !wanted.has(name)) await rm(path(`web/fonts/${name}`));

// The bundle drops legal comments, so the license of every package shipped in web/ is collected here.
const shipped = new Set(['pdfjs-dist', ...Object.keys(fonts).map(family => `@fontsource/${family}`)]);
for (const input of Object.keys(bundle.metafile.inputs)) {
  const name = [...input.matchAll(/node_modules\/((?:@[^/]+\/)?[^/]+)/g)].at(-1)?.[1];
  if (name) shipped.add(name);
}
const notices = [];
for (const name of [...shipped].sort()) {
  const directory = `node_modules/${name}`;
  const {version, license} = JSON.parse(await readFile(path(`${directory}/package.json`), 'utf8'));
  const files = (await readdir(path(directory))).filter(file => /^(licen[cs]e|notice|copying)/i.test(file)).sort();
  if (!files.length) throw new Error(`${name} has no license file to ship`);
  const texts = await Promise.all(files.map(file => readFile(path(`${directory}/${file}`), 'utf8')));
  const text = texts.join('\n').replaceAll('\r\n', '\n').trim();
  notices.push(`${name} ${version} (${license})\n\n${text}\n`);
}
const divider = `\n${'='.repeat(72)}\n\n`;
await writeFile(path('web/THIRD_PARTY_NOTICES.txt'), `Third-party code and fonts shipped in web/.\n\n${notices.join(divider)}`);
console.log('Built web/app.js, PDF.js worker, self-hosted fonts and third-party notices');
