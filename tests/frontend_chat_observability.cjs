const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const ts = require('../frontend/node_modules/typescript');
const projectId = 'a'.repeat(32), runId = 'b'.repeat(32), activityId = 'c'.repeat(32);
function load(file, globals = {}) {
  const exports = {};
  const code = ts.transpileModule(fs.readFileSync(file, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText;
  vm.runInNewContext(code, { exports, URL, AbortSignal, process: { env: {} }, ...globals });
  return exports;
}
const { activityLabel, totalUsage, formatBytes, formatElapsed } = load('frontend/src/lib/chat-observability.ts');

test('generation labels describe actual provider/tool stages without fake streaming progress', () => {
  const state = { phase: 'model', events: [] };
  assert.equal(activityLabel(state, false), 'Waiting for the model');
  assert.equal(activityLabel({ ...state, events: [{ stage: 'checking' }] }, false), 'Processing model response');
  assert.equal(activityLabel({ phase: 'tool', tool: 'get_project_context', events: [] }, false), 'Running get project context');
  assert.equal(activityLabel(state, true), 'Pausing after this step');
  assert.equal(activityLabel({ phase: 'step', events: [{ stage: 'unknown' }] }, false), 'Processing next step');
});

test('usage totals use reported counts and do not invent missing usage or double-count reasoning', () => {
  assert.equal(totalUsage({ usage: [null] }), null);
  const result = totalUsage({ usage: [
    { prompt_tokens: 100, completion_tokens: 20, total_tokens: 120, reasoning_tokens: 10 },
    { prompt_tokens: 50, completion_tokens: 5 }, null,
  ] });
  assert.equal(result.total_tokens, 175);
  assert.equal(result.prompt_tokens, 150);
  assert.equal(result.completion_tokens, 25);
  assert.equal(result.reasoning_tokens, 10);
  assert.equal(totalUsage({ usage: [], last_model_response: { usage: { total_tokens: 12 } } }).total_tokens, 12);
  assert.equal(formatBytes(2048), '2.0 KiB');
  assert.equal(formatElapsed(65), '1m 5s');
});

async function proxy(path, method = 'GET', headers = {}) {
  const requests = [];
  const { default: handler } = load('frontend/src/pages/api/projects/[projectId]/[...path].ts', { fetch: async (url, init) => {
    requests.push({ url, init }); return { ok: true, status: 200, json: async () => ({ events: [] }) };
  } });
  const result = {};
  await handler({ query: { projectId, path }, method, body: {}, headers: { host: 'localhost:3000', 'content-type': 'application/json', ...headers } }, {
    setHeader() {}, status(code) { result.status = code; return this; }, json(data) { result.data = data; return this; },
  });
  return { ...result, requests };
}

test('context and activity reads are narrowly allowlisted and do not forward keys', async () => {
  for (const path of [['agent-runs', runId, 'context'], ['activity', activityId]]) {
    const result = await proxy(path, 'GET', { 'x-reqlica-groq-key': 'private', 'x-reqlica-activity-id': activityId });
    assert.equal(result.status, 200);
    assert.equal(result.requests[0].url.port, '3001');
    assert.equal(result.requests[0].init.headers['X-Reqlica-Groq-Key'], undefined);
    assert.equal(result.requests[0].init.headers['X-Reqlica-Activity-ID'], undefined);
    assert.equal((await proxy(path, 'POST')).status, 405);
  }
  assert.equal((await proxy(['activity', '../other'])).status, 405);
  assert.equal((await proxy(['agent-runs', 'invalid', 'context'])).status, 405);
});

test('only a model step forwards a validated activity identity', async () => {
  const result = await proxy(['agent-runs', runId, 'step'], 'POST', { 'x-reqlica-activity-id': activityId });
  assert.equal(result.status, 200);
  assert.equal(result.requests[0].init.headers['X-Reqlica-Activity-ID'], activityId);
  const invalid = await proxy(['agent-runs', runId, 'step'], 'POST', { 'x-reqlica-activity-id': 'bad' });
  assert.equal(invalid.status, 400);
  assert.equal(invalid.requests.length, 0);
});
