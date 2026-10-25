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
    if (name.startsWith('@/')) return load(`frontend/src/${name.slice(2)}.tsx`);
    return frontendRequire(name);
  } });
  modules.set(file, exports);
  return exports;
}

const Button = load('frontend/src/components/ui/Button/Button.tsx').default;
const PageHeader = load('frontend/src/components/ui/PageHeader/PageHeader.tsx').default;

test('shared button variants preserve semantic styles and native disabled behavior', () => {
  for (const [variant, primitive] of [['primary', 'default'], ['secondary', 'outline'], ['ghost', 'ghost'], ['danger', 'destructive']]) {
    const html = renderToStaticMarkup(React.createElement(Button, { variant, disabled: true }, 'Action'));
    assert.match(html, new RegExp(`data-variant="${primitive}"`));
    assert.match(html, new RegExp(`\\b${variant}\\b`));
    assert.match(html, /disabled=""/);
    assert.match(html, /<button/);
  }
});

test('button links use one accessible anchor rather than nested interactive controls', () => {
  const html = renderToStaticMarkup(React.createElement(Button, { asChild: true, variant: 'secondary' },
    React.createElement('a', { href: '/endpoints', 'aria-label': 'Browse endpoints' }, 'Endpoints')));
  assert.match(html, /^<a /);
  assert.match(html, /href="\/endpoints"/);
  assert.match(html, /aria-label="Browse endpoints"/);
  assert.doesNotMatch(html, /<button/);
});

test('page header keeps one title with metadata and action controls', () => {
  const html = renderToStaticMarkup(React.createElement(PageHeader, {
    id: 'page-title', title: 'Endpoints',
    metadata: React.createElement('span', null, 'Running · PID 123'),
    actions: React.createElement(Button, { variant: 'secondary', type: 'button' }, 'Refresh'),
  }));
  assert.equal((html.match(/<h1\b/g) || []).length, 1);
  assert.match(html, /id="page-title"/);
  assert.match(html, /Running · PID 123/);
  assert.match(html, /Refresh/);
  assert.doesNotMatch(html, /<p/);
});
