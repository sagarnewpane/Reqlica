const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const ts = require('../frontend/node_modules/typescript');
const source = ts.transpileModule(fs.readFileSync('frontend/src/pages/api/projects.ts', 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText;
async function call(method, body, upstream, headers = {}) {
  const exports = {};
  vm.runInNewContext(source, { exports, URL, AbortSignal, process: { env: {} }, fetch: upstream });
  const result = { headers: {} };
  const response = { setHeader(k,v) { result.headers[k] = v; }, status(code) { result.status = code; return this; }, json(data) { result.body = data; return this; } };
  await exports.default({ method, body, headers: { host: 'localhost:3000', 'content-type': 'application/json', ...headers } }, response);
  return result;
}
test('project listing forwards live response and disables caching', async () => {
  const result = await call('GET', null, async (url, options) => {
    assert.equal(url.href, 'http://127.0.0.1:4000/api/projects');
    assert.equal(options.method, 'GET');
    return { ok: true, status: 200, json: async () => ({ projects: [{ id: 'test' }] }) };
  });
  assert.equal(result.status, 200);
  assert.equal(result.headers['Cache-Control'], 'no-store');
  assert.equal(result.body.projects[0].id, 'test');
});
test('creation trims fields and preserves created response', async () => {
  const result = await call('POST', { name: ' Books ', directory: ' ~/books ' }, async (_, options) => {
    assert.deepEqual(JSON.parse(options.body), { name: 'Books', directory: '~/books' });
    return { ok: true, status: 201, json: async () => ({ id: 'new', status: 'idle' }) };
  });
  assert.equal(result.status, 201);
});
test('invalid paths and blank names never reach the runtime', async () => {
  for (const body of [{ name: 'Books', directory: 'relative' }, { name: ' ', directory: '/tmp/books' }]) {
    assert.equal((await call('POST', body, () => assert.fail('unexpected fetch'))).status, 400);
  }
});
test('cross-origin creation is rejected', async () => {
  assert.equal((await call('POST', {}, () => assert.fail('unexpected fetch'), { origin: 'https://other.example' })).status, 403);
});
test('runtime conflicts keep actionable errors', async () => {
  const result = await call('POST', { name: 'Books', directory: '/tmp/books' }, async () => ({ ok: false, status: 409, json: async () => ({ detail: 'Directory must be empty' }) }));
  assert.equal(result.status, 409);
  assert.equal(result.body.error, 'Directory must be empty');
});
test('unavailable runtime reports uncertainty on creation', async () => {
  const result = await call('POST', { name: 'Books', directory: '/tmp/books' }, async () => { throw new Error('timeout'); });
  assert.equal(result.status, 502);
  assert.match(result.body.error, /Refresh the project list before retrying/);
});
test('unsupported methods cannot mutate runtime state', async () => {
  assert.equal((await call('DELETE', null, () => assert.fail('unexpected fetch'))).status, 405);
});
