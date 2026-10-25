const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { createRequire } = require('node:module');
const frontendRequire = createRequire(path.resolve('frontend/package.json'));
const ts = frontendRequire('typescript');
const React = frontendRequire('react');
const { renderToStaticMarkup } = frontendRequire('react-dom/server');
const modules = new Map();
function load(file) {
  if (modules.has(file)) return modules.get(file);
  const exports = {};
  const code = ts.transpileModule(fs.readFileSync(file, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  vm.runInNewContext(code, { exports, require(name) {
    if (name.endsWith('.module.css')) return { default: new Proxy({}, { get: (_, key) => String(key) }) };
    if (name === './use-resource') return { useResource: () => ({ data: { items: [] }, busy: false, refresh() {} }) };
    if (name === '@/components/endpoints/ContractViewer') return { sample: () => ({}) };
    if (name.startsWith('@/') || name.startsWith('./')) {
      const base = name.startsWith('@/') ? `frontend/src/${name.slice(2)}` : path.resolve(path.dirname(file), name);
      return load(fs.existsSync(`${base}.tsx`) ? `${base}.tsx` : `${base}.ts`);
    }
    return frontendRequire(name);
  } });
  modules.set(file, exports);
  return exports;
}
const RequestTester = load('frontend/src/components/requests/RequestTester.tsx').default;
const SavedItems = load('frontend/src/components/workspace/SavedItems.tsx').default;

test('tester renders consistent request/response/chain sections and an explicit empty response', () => {
  const html = renderToStaticMarkup(React.createElement(RequestTester, { projectId: 'a'.repeat(32) }));
  assert.match(html, /Send a request/);
  assert.match(html, /id="response-heading">Response/);
  assert.match(html, /Send a request to see its status, timing, body and headers here/);
  assert.match(html, /No steps yet/);
  assert.match(html, /may change its data/);
  assert.match(html, /role="switch"/);
  assert.match(html, /for="inline-headers"/);
  assert.doesNotMatch(html, /<select\b(?! aria-hidden="true")/);
  assert.match(html, /data-slot="select-trigger"/);
});

test('saved requests and chains use shared inputs/selects with distinct accessible labels', () => {
  const html = renderToStaticMarkup(React.createElement('div', null,
    ...['request', 'chain'].map(kind => React.createElement(SavedItems, { key: kind, kind, projectId: 'a'.repeat(32), data: {}, onLoad() {} }))));
  const ids = [...html.matchAll(/<label for="([^"]+)"/g)].map(match => match[1]);
  assert.equal(ids.length, 4);
  assert.equal(new Set(ids).size, 4);
  for (const id of ids) assert.ok(html.includes(`id="${id}"`));
  assert.equal((html.match(/data-slot="input"/g) || []).length, 2);
  assert.equal((html.match(/data-slot="select-trigger"/g) || []).length, 2);
  assert.doesNotMatch(html, /<select\b(?! aria-hidden="true")/);
});
