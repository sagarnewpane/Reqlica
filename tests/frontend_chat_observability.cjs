// Server adapter coverage moved to test_dashboard.py. Browser helper tests remain here.
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
