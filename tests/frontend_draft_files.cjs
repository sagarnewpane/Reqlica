// Server adapter coverage moved to test_dashboard.py. Browser helper tests remain here.
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
const success = data => ({ ok: true, status: 200, json: async () => data });
test('file helper validates the same safe paths and encodes source query values', () => {
  const { draftFilePathError, draftFileRoute } = load('frontend/src/lib/draft-files.ts');
  assert.equal(draftFilePathError('endpoints/hello.py'), null);
  assert.match(draftFilePathError('../bad.py'), /parent-directory/);
  assert.match(draftFilePathError('.reqlica/environment.json'), /Control/);
  assert.match(draftFileRoute(draftId, 'examples/a+b #?.txt'), /a%2Bb%20%23%3F.txt/);
});
