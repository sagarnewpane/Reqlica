// Server adapter coverage moved to test_dashboard.py. Browser helper tests remain here.
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
