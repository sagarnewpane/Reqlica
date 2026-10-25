const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const ts = require('../frontend/node_modules/typescript');
const projectId = 'a'.repeat(32), draftId = 'b'.repeat(32), revision = 'c'.repeat(64);
function load(file, globals = {}) {
  const exports = {};
  const code = ts.transpileModule(fs.readFileSync(file, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText;
  vm.runInNewContext(code, { exports, URL, Buffer, AbortSignal, process: { env: {} }, ...globals });
  return exports;
}
async function call({ method = 'GET', path = 'endpoints/hello.py', body, headers = {}, id = projectId, draft = draftId, fetch = () => assert.fail('Unexpected draft file request') } = {}) {
  const { default: handler } = load('frontend/src/pages/api/projects/[projectId]/drafts/[draftId]/files.ts', { fetch });
  const result = { headers: {} };
  const res = { setHeader(name, value) { result.headers[name] = value; }, status(code) { result.status = code; return this; }, json(value) { result.body = value; return this; } };
  await handler({ method, body, query: { projectId: id, draftId: draft, path }, headers: { host: 'localhost:3000', 'content-type': 'application/json', ...headers } }, res);
  return result;
}
const success = data => ({ ok: true, status: 200, json: async () => data });
test('draft file reads scope and encode project-relative names without changing the URL path', async () => {
  const name = 'examples/a+b #?.txt';
  const response = await call({ path: name, fetch: async (url, init) => {
    assert.equal(url.origin, 'http://127.0.0.1:3001');
    assert.equal(url.pathname, `/api/projects/${projectId}/drafts/${draftId}/files`);
    assert.equal(url.searchParams.get('path'), name); assert.equal(init.method, 'GET'); assert.equal(init.body, undefined); assert.equal(init.redirect, 'error');
    return success({ path: name, content: '<script>not executed</script>', revision });
  } });
  assert.equal(response.status, 200); assert.equal(response.headers['Cache-Control'], 'no-store');
});
test('draft file saves forward only content and the exact inspected revision, including empty files', async () => {
  for (const content of ['', 'def hello():\n    return "edited"\n']) {
    const response = await call({ method: 'PUT', body: { content, expected_revision: revision, api_key: 'never-forward', confirm: true }, headers: { 'x-reqlica-groq-key': 'never-forward' }, fetch: async (_, init) => {
      assert.deepEqual(JSON.parse(init.body), { content, expected_revision: revision });
      assert.equal(init.headers['X-Reqlica-Groq-Key'], undefined);
      return success({ id: draftId, revision: 'd'.repeat(64) });
    } });
    assert.equal(response.status, 200);
  }
});
test('source traversal, aliases, private controls, database paths, duplicate parameters and invalid IDs are rejected', async () => {
  for (const path of [undefined, ['', 'x'], '', '/etc/passwd', '../secret', 'a/../secret', './hello.py', 'a//hello.py', 'a\\hello.py', 'a\x00.py', '.reqlica/environment.json', '.git/config', 'versions/x.py', 'drafts/x.py', '__pycache__/x.py', 'project.json', 'nested/project.json', 'data.sqlite', 'data.db-wal']) assert.equal((await call({ path: path === undefined ? null : path })).status, 400);
  assert.equal((await call({ id: '../other' })).status, 400);
  assert.equal((await call({ draft: '../other' })).status, 400);
  assert.equal((await call({ method: 'DELETE' })).status, 400);
  assert.equal((await call({ method: 'POST' })).status, 405);
});
test('writes require a pinned revision, bounded UTF-8 content, JSON, and same-origin intent', async () => {
  for (const body of [{ content: 'x' }, { content: null, expected_revision: revision }, { content: 'x', expected_revision: 'invalid' }]) assert.equal((await call({ method: 'PUT', body })).status, 400);
  assert.equal((await call({ method: 'PUT', body: { content: 'x'.repeat(1024 * 1024 + 1), expected_revision: revision } })).status, 413);
  const body = { content: 'x', expected_revision: revision };
  assert.equal((await call({ method: 'PUT', body, headers: { origin: 'https://other.example' } })).status, 403);
  assert.equal((await call({ method: 'PUT', body, headers: { 'sec-fetch-site': 'cross-site' } })).status, 403);
  assert.equal((await call({ method: 'PUT', body, headers: { 'content-type': 'text/plain' } })).status, 415);
});
test('conflicts and missing files preserve backend diagnostics; unconfirmed saves are never retried', async () => {
  const conflict = await call({ method: 'PUT', body: { content: 'kept locally', expected_revision: revision }, fetch: async () => ({ ok: false, status: 409, json: async () => ({ detail: 'Draft revision conflict; inspect the current draft' }) }) });
  assert.equal(conflict.status, 409); assert.match(conflict.body.error, /revision conflict/);
  const missing = await call({ fetch: async () => ({ ok: false, status: 404, json: async () => ({ detail: 'Unknown draft file' }) }) });
  assert.equal(missing.status, 404);
  let requests = 0;
  const lost = await call({ method: 'PUT', body: { content: 'kept locally', expected_revision: revision }, fetch: async () => { requests++; throw Error('lost'); } });
  assert.equal(requests, 1); assert.equal(lost.status, 502); assert.match(lost.body.error, /Keep or download your local text/);
});
test('oversized source reads fail without exposing a truncated editable file', async () => {
  const response = await call({ fetch: async () => success({ path: 'endpoints/hello.py', content: 'x'.repeat(1024 * 1024 + 1), revision }) });
  assert.equal(response.status, 413);
});
test('file helper validates the same safe paths and encodes source query values', () => {
  const { draftFilePathError, draftFileRoute } = load('frontend/src/lib/draft-files.ts');
  assert.equal(draftFilePathError('endpoints/hello.py'), null);
  assert.match(draftFilePathError('../bad.py'), /parent-directory/);
  assert.match(draftFilePathError('.reqlica/environment.json'), /Control/);
  assert.match(draftFileRoute(draftId, 'examples/a+b #?.txt'), /a%2Bb%20%23%3F.txt/);
});


test('draft file deletion forwards only the inspected revision', async () => {
  const response = await call({ method: 'DELETE', body: { expected_revision: revision, content: 'not forwarded' }, fetch: async (_, init) => {
    assert.equal(init.method, 'DELETE'); assert.deepEqual(JSON.parse(init.body), { expected_revision: revision });
    return success({ id: draftId, revision });
  } });
  assert.equal(response.status, 200);
});
