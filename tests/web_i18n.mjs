// Every message the interface can show must exist in English with the same placeholders,
// and no Chinese may reach the screen except through t(). Python's workflow reasons count too.
import assert from 'node:assert/strict';
import { readdirSync, readFileSync } from 'node:fs';
import ts from 'typescript';

const source = readFileSync('frontend/locales/en.ts', 'utf8');
const dictionary = ts.createSourceFile('en.ts', source, ts.ScriptTarget.Latest, true);
const english = new Map();
(function collect(node) {
  if (ts.isPropertyAssignment(node) && ts.isStringLiteralLike(node.name) && ts.isStringLiteralLike(node.initializer)) {
    assert.ok(!english.has(node.name.text), `Duplicate key: ${node.name.text}`);
    english.set(node.name.text, node.initializer.text);
  }
  node.forEachChild(collect);
})(dictionary);

const cjk = /[一-鿿]/;
const files = ['frontend', 'frontend/ui', 'frontend/models'].flatMap(directory => readdirSync(directory).filter(name => /\.tsx?$/.test(name)).map(name => `${directory}/${name}`));
const used = new Set(), stray = [];
for (const file of files) {
  const sf = ts.createSourceFile(file, readFileSync(file, 'utf8'), ts.ScriptTarget.Latest, true, file.endsWith('x') ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
  (function walk(node) {
    if (ts.isVariableDeclaration(node) && node.name.getText(sf) === 'phrases') return;
    if (ts.isCallExpression(node) && node.expression.getText(sf) === 't') {
      const [key] = node.arguments;
      assert.ok(key && ts.isStringLiteralLike(key), `${file}: t() needs a literal key: ${node.getText(sf).slice(0, 60)}`);
      used.add(key.text);
      node.arguments.slice(1).forEach(walk);
      return;
    }
    if ((ts.isStringLiteralLike(node) || ts.isJsxText(node)) && cjk.test(node.text) && !(ts.isPropertyAssignment(node.parent) && node.parent.name === node)) stray.push(`${file}: ${node.text.trim().slice(0, 50)}`);
    if (ts.isTemplateExpression(node) && (cjk.test(node.head.text) || node.templateSpans.some(span => cjk.test(span.literal.text)))) stray.push(`${file}: ${node.getText(sf).slice(0, 50)}`);
    node.forEachChild(walk);
  })(sf);
}
assert.deepEqual(stray, [], 'Chinese text that bypasses t()');

for (const line of readFileSync('src/lemfolio/workflow.py', 'utf8').split('\n')) {
  if (line.trimStart().startsWith('#')) continue;
  for (const [, text] of line.matchAll(/"([^"]*[一-鿿][^"]*)"/g)) used.add(text);
}
const missing = [...used].filter(key => !english.has(key));
assert.deepEqual(missing, [], 'Messages with no English');
const placeholders = text => (text.match(/\{\d+\}/g) || []).sort().join();
for (const key of used) assert.equal(placeholders(english.get(key)), placeholders(key), `Placeholders differ: ${key}`);
for (const [key, value] of english) assert.ok(!cjk.test(value) || key === '简体中文', `English still contains Chinese: ${value}`);
console.log(`PASS ${used.size} messages have English; no Chinese bypasses t()`);
