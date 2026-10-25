const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const ts = require('../frontend/node_modules/typescript');

function load(file, globals = {}) {
  const exports = {};
  const code = ts.transpileModule(fs.readFileSync(file, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS },
  }).outputText;
  vm.runInNewContext(code, { exports, ...globals });
  return exports;
}

const { filterEndpoints, endpointKey, resolveTesterEndpoint, REQUEST_METHODS } = load('frontend/src/lib/endpoints.ts');
const routes = [
  { method: 'POST', path: '/books', handler: 'endpoints/books.py:create_book' },
  { method: 'GET', path: '/books', handler: 'endpoints/books.py:list_books' },
  { method: 'GET', path: '/books/{book_id}', handler: 'endpoints/books.py:get_book' },
  { method: 'DELETE', path: '/books/{book_id}' },
  { method: 'TRACE', path: '/debug' },
];

test('endpoint search matches method, path, and actual handler case-insensitively', () => {
  assert.equal(filterEndpoints(routes, '  GET books ').length, 2);
  assert.equal(filterEndpoints(routes, 'CREATE_BOOK')[0], routes[0]);
  assert.equal(filterEndpoints(routes, 'delete BOOK_ID')[0], routes[3]);
  assert.equal(filterEndpoints(routes, 'nonexistent').length, 0);
  assert.equal(filterEndpoints(routes, ' \t ').length, routes.length);
  assert.equal(filterEndpoints(routes, 'GET create_book').length, 0);
});

test('method filtering combines with search and never mutates the source routes', () => {
  const before = JSON.stringify(routes);
  assert.equal(filterEndpoints(routes, 'books', 'POST')[0], routes[0]);
  assert.equal(filterEndpoints(routes, 'debug', 'GET').length, 0);
  assert.equal(filterEndpoints(routes, '', 'TRACE')[0], routes[4]);
  assert.equal(JSON.stringify(routes), before);
  assert.equal(filterEndpoints([], '', 'all').length, 0);
});

test('route identity includes the method, not just the shared path', () => {
  assert.notEqual(endpointKey(routes[0]), endpointKey(routes[1]));
  assert.equal(endpointKey(routes[2]), 'GET /books/{book_id}');
});

test('tester deep links select the exact existing method and path without executing requests', () => {
  assert.equal(resolveTesterEndpoint(routes, 'POST', '/books'), routes[0]);
  assert.equal(resolveTesterEndpoint(routes, 'GET', '/books/{book_id}'), routes[2]);
  assert.equal(resolveTesterEndpoint(routes, 'DELETE', '/books'), undefined);
  assert.equal(resolveTesterEndpoint(routes, 'POST', '/removed'), undefined);
  assert.equal(resolveTesterEndpoint(routes, 'GET', '/books?other=1'), undefined);
  assert.equal(resolveTesterEndpoint(routes, 'GET', '/../other'), undefined);
});

test('unknown, incomplete, or unsupported tester selections never silently resolve to another route', () => {
  for (const [method, path] of [['TRACE', '/debug'], ['GET', undefined], [undefined, '/books'], ['', '']]) {
    assert.equal(resolveTesterEndpoint(routes, method, path), undefined);
  }
  assert.equal(REQUEST_METHODS.includes('TRACE'), false);
});

test('the default tester route prefers GET and skips unsupported methods', () => {
  assert.equal(resolveTesterEndpoint(routes), routes[1]);
  assert.equal(resolveTesterEndpoint([routes[4], routes[0]]), routes[0]);
  assert.equal(resolveTesterEndpoint([routes[4]]), undefined);
  assert.equal(resolveTesterEndpoint([]), undefined);
});

test('endpoint metadata reads are scoped, uncached and preserve the real manifest response', async () => {
  const projectId = 'a'.repeat(32);
  const project = { id: projectId, name: 'Library API', status: 'stopped', active_version: 'b'.repeat(32), endpoints: routes, error: null };
  const { default: handler } = load('frontend/src/pages/api/projects/[projectId]/[...path].ts', {
    URL, AbortSignal, process: { env: {} },
    fetch: async (url, init) => {
      assert.equal(url.href, `http://127.0.0.1:4000/api/projects/${projectId}/endpoints`);
      assert.equal(init.method, 'GET');
      assert.equal(init.body, undefined);
      assert.equal(init.redirect, 'error');
      return { ok: true, status: 200, json: async () => project };
    },
  });
  const result = { headers: {} };
  const response = {
    setHeader(name, value) { result.headers[name] = value; },
    status(code) { result.status = code; return this; },
    json(data) { result.body = data; return this; },
  };
  await handler({ method: 'GET', query: { projectId, path: ['endpoints'] }, headers: {} }, response);
  assert.equal(result.headers['Cache-Control'], 'no-store');
  assert.equal(result.status, 200);
  assert.equal(result.body, project);
});
