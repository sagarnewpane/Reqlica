const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const ts = require('../frontend/node_modules/typescript');
const projectId = 'a'.repeat(32);

async function call(route, method, body, headers = {}) {
  const exports = {};
  const code = ts.transpileModule(fs.readFileSync('frontend/src/pages/api/projects/[projectId]/[...path].ts', 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS },
  }).outputText;
  const requests = [];
  vm.runInNewContext(code, { exports, URL, AbortSignal, process: { env: {} }, fetch: async (url, init) => {
    requests.push({ url, init });
    return { ok: true, status: 200, json: async () => ({ configured: true }) };
  } });
  const result = {};
  await exports.default({ method, body, query: { projectId, path: route.split('/') }, headers: {
    host: 'localhost:3000', 'content-type': 'application/json', 'x-reqlica-groq-key': 'must-not-forward', ...headers,
  } }, { setHeader() {}, status(code) { result.status = code; return this; }, json(value) { result.body = value; return this; } });
  return { ...result, requests };
}

test('project key management and discovery target the console without forwarding browser credentials', async () => {
  for (const [route, method] of [['ai/key', 'PUT'], ['ai/key', 'DELETE'], ['ai/key/status', 'POST'], ['ai/key/reveal', 'POST'], ['ai/models', 'POST']]) {
    const result = await call(route, method, { provider: 'groq' });
    assert.equal(result.status, 200);
    assert.equal(result.requests[0].url.href, `http://127.0.0.1:3001/api/projects/${projectId}/${route}`);
    assert.equal(result.requests[0].init.headers['X-Reqlica-Groq-Key'], undefined);
    assert.equal(result.requests[0].init.redirect, 'error');
  }
});

test('key mutations reject cross-origin requests and unsupported methods', async () => {
  assert.equal((await call('ai/key', 'GET', {})).status, 405);
  assert.equal((await call('ai/key', 'POST', {})).status, 405);
  assert.equal((await call('ai/key/reveal', 'GET', {})).status, 405);
  const deniedReveal = await call('ai/key/reveal', 'POST', { provider: 'groq' }, { origin: 'https://other.example' });
  assert.equal(deniedReveal.status, 403);
  assert.equal(deniedReveal.requests.length, 0);
  const denied = await call('ai/key', 'PUT', { provider: 'groq', api_key: 'secret' }, { origin: 'https://other.example' });
  assert.equal(denied.status, 403);
  assert.equal(denied.requests.length, 0);
  assert.equal((await call('ai/key', 'PUT', {}, { 'content-type': 'text/plain' })).status, 415);
});
