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
  vm.runInNewContext(code, { exports, URL, URLSearchParams, AbortSignal, process: { env: {} }, ...globals });
  return exports;
}

const projectId = 'a'.repeat(32), requestId = 'b'.repeat(32), revision = 'c'.repeat(64);
const settings = { revision, capture_payloads: false, capture_output: false, max_body_bytes: 1024 };
const success = value => ({ ok: true, status: 200, json: async () => value });
const never = () => assert.fail('Unexpected upstream request');

async function call(path, { query = {}, method = 'GET', body, fetch = never, headers = {}, id = projectId } = {}) {
  const { default: handler } = load('frontend/src/pages/api/projects/[projectId]/[...path].ts', { fetch });
  const result = { headers: {} };
  const response = {
    setHeader(name, value) { result.headers[name] = value; },
    status(code) { result.status = code; return this; },
    json(value) { result.body = value; return this; },
  };
  await handler({ method, body, query: { ...query, projectId: id, path: path.split('/') }, headers: { host: 'localhost:3000', 'content-type': 'application/json', ...headers } }, response);
  return result;
}

test('history listing forwards only bounded filters to the selected project console', async () => {
  const page = { project_id: projectId, events: [], next_before_id: 120 };
  const response = await call('logs', {
    query: { limit: '50', before_id: '200', kind: 'runtime_error', request_id: requestId, ignored: 'not-forwarded' },
    fetch: async (url, init) => {
      assert.equal(url.origin, 'http://127.0.0.1:3001');
      assert.equal(url.pathname, `/api/projects/${projectId}/logs`);
      assert.deepEqual(Object.fromEntries(url.searchParams), { limit: '50', before_id: '200', kind: 'runtime_error', request_id: requestId });
      assert.equal(init.method, 'GET');
      assert.equal(init.body, undefined);
      assert.equal(init.redirect, 'error');
      assert.equal(init.headers['X-Reqlica-Groq-Key'], undefined);
      return success(page);
    },
    headers: { 'x-reqlica-groq-key': 'never-forward' },
  });
  assert.equal(response.status, 200);
  assert.equal(response.headers['Cache-Control'], 'no-store');
  assert.equal(response.body, page);
});

test('history rejects invalid, duplicated, oversized and injected query values before fetching', async () => {
  const queries = [
    { limit: '0' }, { limit: '101' }, { limit: '1.5' }, { limit: ['50', '100'] },
    { before_id: '-1' }, { before_id: '1e5' }, { before_id: '9007199254740992' },
    { request_id: '../other' }, { request_id: requestId.toUpperCase() }, { request_id: [requestId] },
    { kind: 'other' }, { kind: ['request', 'stdout'] }, { kind: 'request&limit=10000' },
  ];
  for (const query of queries) assert.equal((await call('logs', { query })).status, 400);
  assert.equal((await call('logs/9007199254740992')).status, 400);
  assert.equal((await call('logs/0')).status, 405);
  assert.equal((await call('logs/../settings')).status, 405);
  assert.equal((await call('logs', { id: '../other' })).status, 400);
});

test('event details and capture settings are separate uncached GETs with no side effects', async () => {
  for (const path of ['logs/42', 'logs/settings']) {
    const response = await call(path, { fetch: async (url, init) => {
      assert.equal(url.href, `http://127.0.0.1:3001/api/projects/${projectId}/${path}`);
      assert.equal(init.method, 'GET');
      return success(path === 'logs/settings' ? settings : { id: 42, kind: 'stdout', text: '<script>unsafe()</script>' });
    } });
    assert.equal(response.status, 200);
    assert.equal(response.headers['Cache-Control'], 'no-store');
  }
});

test('capture writes require strict confirmation, valid bounds, booleans and current revision', async () => {
  const valid = { ...settings, expected_revision: revision, confirm: true };
  for (const changes of [
    { confirm: undefined }, { confirm: 'true' }, { expected_revision: undefined }, { expected_revision: 'bad' },
    { capture_payloads: 'true' }, { capture_output: 1 }, { max_body_bytes: '1024' },
    { max_body_bytes: 127 }, { max_body_bytes: 2049 }, { max_body_bytes: 512.5 },
  ]) {
    assert.equal((await call('logs/settings', { method: 'PUT', body: { ...valid, ...changes } })).status, 400);
  }
});

test('capture writes forward only backend-supported fields and never provider credentials', async () => {
  const body = { expected_revision: revision, capture_payloads: true, capture_output: true, max_body_bytes: 2048 };
  const response = await call('logs/settings', { method: 'PUT', body: { ...body, confirm: true, api_key: 'secret' }, headers: { 'x-reqlica-groq-key': 'secret' }, fetch: async (url, init) => {
    assert.equal(url.port, '3001');
    assert.deepEqual(JSON.parse(init.body), body);
    assert.equal(init.headers['X-Reqlica-Groq-Key'], undefined);
    return success({ ...settings, ...body });
  } });
  assert.equal(response.status, 200);
});

test('capture writes reject cross-origin requests, non-JSON, and unsupported methods', async () => {
  const body = { ...settings, expected_revision: revision, confirm: true };
  for (const headers of [{ origin: 'https://outside.example' }, { 'sec-fetch-site': 'cross-site' }]) {
    assert.equal((await call('logs/settings', { method: 'PUT', body, headers })).status, 403);
  }
  assert.equal((await call('logs/settings', { method: 'PUT', body, headers: { 'content-type': 'text/plain' } })).status, 415);
  for (const path of ['logs', 'logs/42', 'logs/settings']) {
    assert.equal((await call(path, { method: 'DELETE', body })).status, 405);
    assert.equal((await call(path, { method: 'POST', body })).status, 405);
  }
});

test('retention errors and capture revision conflicts preserve actionable backend messages', async () => {
  for (const [path, method, status, message] of [
    ['logs/42', 'GET', 404, 'Inspection event not found'],
    ['logs/settings', 'PUT', 409, 'Capture settings changed; reload them'],
  ]) {
    const response = await call(path, { method, body: { ...settings, expected_revision: revision, confirm: true }, fetch: async () => ({ ok: false, status, json: async () => ({ detail: message }) }) });
    assert.equal(response.status, status);
    assert.equal(response.body.error, message);
  }
});

test('unconfirmed capture writes are not retried and explicitly require reinspection', async () => {
  let requests = 0;
  const response = await call('logs/settings', { method: 'PUT', body: { ...settings, expected_revision: revision, confirm: true }, fetch: async () => { requests++; throw Error('lost'); } });
  assert.equal(requests, 1);
  assert.equal(response.status, 502);
  assert.match(response.body.error, /Reload capture settings before saving again/);
});

const { logListPath, validateCaptureSettings, logTimestamp, isRequestId } = load('frontend/src/lib/logs.ts');
test('history helper keeps filters when requesting an older page and trims correlation IDs', () => {
  const url = new URL(logListPath({ kind: 'stderr', requestId: ` ${requestId} ` }, 42), 'http://local/');
  assert.deepEqual(Object.fromEntries(url.searchParams), { limit: '50', kind: 'stderr', request_id: requestId, before_id: '42' });
  assert.equal(logListPath({ kind: 'all', requestId: '' }), 'logs?limit=50');
  assert.throws(() => logListPath({ kind: 'request', requestId: 'bad' }), /Request ID/);
  assert.throws(() => logListPath({ kind: 'invalid', requestId: '' }), /event kind/);
  assert.throws(() => logListPath({ kind: 'all', requestId: '' }, 0), /cursor/);
});

test('capture validation uses backend byte limits and display timestamps are explicitly UTC', () => {
  assert.equal(validateCaptureSettings(settings), null);
  assert.match(validateCaptureSettings({ ...settings, max_body_bytes: 2049 }), /128/);
  assert.match(validateCaptureSettings({ ...settings, capture_output: 'yes' }), /explicit/);
  assert.equal(isRequestId(requestId), true);
  assert.equal(isRequestId('not-a-request-id'), false);
  assert.equal(logTimestamp('2026-10-24T12:00:00+02:00'), '2026-10-24 10:00:00.000 UTC');
  assert.equal(logTimestamp('unknown'), 'unknown');
});
