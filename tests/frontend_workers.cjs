const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const ts = require('../frontend/node_modules/typescript');
const projectId = 'a'.repeat(32);
function load(file, globals = {}) {
  const exports = {};
  const code = ts.transpileModule(fs.readFileSync(file, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText;
  vm.runInNewContext(code, { exports, URL, AbortSignal, process: { env: {} }, ...globals });
  return exports;
}
async function call(action, { method = 'POST', body = { confirm: true }, headers = {}, id = projectId, fetch = () => assert.fail('Unexpected lifecycle request'), globals = {} } = {}) {
  const { default: handler } = load('frontend/src/pages/api/projects/[projectId]/[...path].ts', { fetch, ...globals });
  const result = { headers: {} };
  const res = { setHeader(name, value) { result.headers[name] = value; }, status(code) { result.status = code; return this; }, json(value) { result.body = value; return this; } };
  await handler({ method, body, query: { projectId: id, path: [action] }, headers: { host: 'localhost:3000', 'content-type': 'application/json', ...headers } }, res);
  return result;
}
test('worker actions target only the project runtime, with 30-second timeout and no credential forwarding', async () => {
  for (const action of ['start', 'stop']) {
    let timeout;
    const response = await call(action, { body: { confirm: true, api_key: 'secret', delete_files: true }, headers: { 'x-reqlica-groq-key': 'secret' }, globals: { AbortSignal: { timeout(value) { timeout = value; return undefined; } } }, fetch: async (url, init) => {
      assert.equal(url.href, `http://127.0.0.1:4000/api/projects/${projectId}/${action}`);
      assert.equal(init.method, 'POST'); assert.equal(init.redirect, 'error');
      assert.deepEqual(JSON.parse(init.body), {}); assert.equal(init.headers['X-Reqlica-Groq-Key'], undefined);
      return { ok: true, status: 200, json: async () => ({ id: projectId, status: action === 'start' ? 'running' : 'stopped', endpoints: [] }) };
    } });
    assert.equal(response.status, 200); assert.equal(response.headers['Cache-Control'], 'no-store'); assert.equal(timeout, 30000);
  }
});
test('worker lifecycle changes require literal confirmation and reject unsupported methods and origins', async () => {
  for (const action of ['start', 'stop']) {
    for (const body of [null, {}, { confirm: false }, { confirm: 'true' }, { confirm: 1 }]) assert.equal((await call(action, { body })).status, 400);
    for (const method of ['GET', 'PUT', 'DELETE', 'PATCH']) assert.equal((await call(action, { method })).status, 405);
    assert.equal((await call(action, { headers: { origin: 'https://other.example' } })).status, 403);
    assert.equal((await call(action, { headers: { 'sec-fetch-site': 'cross-site' } })).status, 403);
    assert.equal((await call(action, { headers: { 'content-type': 'text/plain' } })).status, 415);
    assert.equal((await call(action, { id: '../other' })).status, 400);
  }
});
test('worker conflicts and unconfirmed responses retain diagnostics and are never retried', async () => {
  for (const action of ['start', 'stop']) {
    const conflict = await call(action, { fetch: async () => ({ ok: false, status: 503, json: async () => ({ detail: 'Project is activating' }) }) });
    assert.equal(conflict.status, 503); assert.equal(conflict.body.error, 'Project is activating');
    let requests = 0;
    const lost = await call(action, { fetch: async () => { requests++; throw Error('lost'); } });
    assert.equal(requests, 1); assert.equal(lost.status, 502); assert.match(lost.body.error, /Refresh status before another action/);
  }
});
const { workerCapabilities, workerStatusDescription, isWorkerProject } = load('frontend/src/lib/workers.ts');
test('worker state gates distinguish idle lazy-start from explicitly stopped', () => {
  assert.equal(workerCapabilities('idle').canStart, true); assert.equal(workerCapabilities('idle').canStop, true);
  assert.equal(workerCapabilities('running').canStart, false); assert.equal(workerCapabilities('running').canStop, true);
  assert.equal(workerCapabilities('stopped').canStart, true); assert.equal(workerCapabilities('stopped').canStop, false);
  assert.equal(workerCapabilities('error').canStart, true);
  assert.equal(workerCapabilities(undefined).canStart, false); assert.equal(workerCapabilities('unknown').canStop, false);
  assert.match(workerStatusDescription('idle'), /automatically/); assert.match(workerStatusDescription('stopped'), /503/);
});
test('worker response guards reject mismatched projects and unknown/malformed states', () => {
  assert.equal(isWorkerProject({ id: projectId, status: 'running', endpoints: [] }, projectId), true);
  for (const value of [null, {}, { id: 'b'.repeat(32), status: 'running', endpoints: [] }, { id: projectId, status: 'unknown', endpoints: [] }, { id: projectId, status: 'running' }]) assert.equal(isWorkerProject(value, projectId), false);
});
