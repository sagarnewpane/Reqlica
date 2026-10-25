// Server adapter coverage moved to test_dashboard.py. Browser helper tests remain here.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const ts = require('../frontend/node_modules/typescript');
const projectId = 'a'.repeat(32);
const draftId = 'b'.repeat(32);
const revision = 'c'.repeat(64);
const version = 'd'.repeat(32);
function load(file, extra = {}) {
  const exports = {};
  const code = ts.transpileModule(fs.readFileSync(file, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS },
  }).outputText;
  vm.runInNewContext(code, { exports, URL, AbortSignal, process: { env: {} }, ...extra });
  return exports;
}
const approvalRoute = `drafts/${draftId}/approve`;
const confirmed = { confirm: true, expected_revision: revision, draft_id: draftId };
const ok = { ok: true, status: 200, json: async () => ({ revision }) };
const never = () => assert.fail('Publication must not reach an upstream server');
const { draftPublicationState } = load('frontend/src/lib/drafts.ts');
function checkedDraft(overrides = {}) {
  return {
    id: draftId, name: 'Reviewed API', revision, base_version: version,
    validation: { status: 'passed', revision },
    test: { status: 'passed', revision, tests_run: 2, tests_passed: 2 },
    approval: null,
    ...overrides,
  };
}
test('approval and activation are separate gates for an exact checked revision', () => {
  const checked = draftPublicationState(checkedDraft(), version);
  assert.equal(checked.canApprove, true);
  assert.equal(checked.canActivate, false);
  const approved = draftPublicationState(checkedDraft({ approval: { revision } }), version);
  assert.equal(approved.canApprove, false);
  assert.equal(approved.canActivate, true);
});
test('missing, failing, stale, and skipped-only checks prevent publication', () => {
  const cases = [
    { validation: null }, { test: null },
    { validation: { status: 'failed', revision } },
    { validation: { status: 'passed', revision: 'e'.repeat(64) } },
    { test: { status: 'passed', revision: 'e'.repeat(64), tests_run: 2, tests_passed: 2 } },
    { test: { status: 'passed', revision, tests_run: 0, tests_passed: 0 } },
    { test: { status: 'passed', revision, tests_run: 2, tests_passed: 0 } },
  ];
  for (const changes of cases) {
    const state = draftPublicationState(checkedDraft({ approval: { revision }, ...changes }), version);
    assert.equal(state.canApprove, false);
    assert.equal(state.canActivate, false);
  }
});
test('an unknown or stale published version blocks both publication actions', () => {
  for (const active of [undefined, null, 'f'.repeat(32)]) {
    const state = draftPublicationState(checkedDraft({ approval: { revision } }), active);
    assert.equal(state.baseIsCurrent, false);
    assert.equal(state.canApprove, false);
    assert.equal(state.canActivate, false);
  }
});
test('approval for a different revision cannot activate the current draft', () => {
  const state = draftPublicationState(checkedDraft({ approval: { revision: 'f'.repeat(64) } }), version);
  assert.equal(state.approved, false);
  assert.equal(state.canActivate, false);
  assert.equal(state.canApprove, true);
});
