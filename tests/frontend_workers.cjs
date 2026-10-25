// Server adapter coverage moved to test_dashboard.py. Browser helper tests remain here.
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
