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

async function call(route, body, fetch, headers = {}, method = 'POST') {
  const { default: handler } = load('frontend/src/pages/api/projects/[projectId]/[...path].ts', { fetch });
  const result = { headers: {} };
  const response = {
    setHeader(name, value) { result.headers[name] = value; },
    status(value) { result.status = value; return this; },
    json(value) { result.body = value; return this; },
  };
  await handler({
    method, body, query: { projectId, path: route.split('/') },
    headers: { host: 'localhost:3000', 'content-type': 'application/json', ...headers },
  }, response);
  return result;
}

const approvalRoute = `drafts/${draftId}/approve`;
const confirmed = { confirm: true, expected_revision: revision, draft_id: draftId };
const ok = { ok: true, status: 200, json: async () => ({ revision }) };
const never = () => assert.fail('Publication must not reach an upstream server');

test('approval reaches only the console and activation reaches only the runtime', async () => {
  for (const [route, port, expectedBody] of [
    [approvalRoute, '3001', { expected_revision: revision, confirm: true }],
    ['activate', '4000', { draft_id: draftId, expected_revision: revision }],
  ]) {
    const result = await call(route, { ...confirmed, unexpected: 'discard', api_key: 'never-forward' }, async (url, init) => {
      assert.equal(url.port, port);
      assert.equal(url.pathname, `/api/projects/${projectId}/${route}`);
      assert.equal(init.method, 'POST');
      assert.equal(init.redirect, 'error');
      assert.equal(init.headers['X-Reqlica-Groq-Key'], undefined);
      assert.deepEqual(JSON.parse(init.body), expectedBody);
      return ok;
    }, { 'x-reqlica-groq-key': 'session-secret' });
    assert.equal(result.status, 200);
    assert.equal(result.headers['Cache-Control'], 'no-store');
  }
});

test('publication rejects missing or nonliteral confirmation and malformed revisions', async () => {
  for (const route of [approvalRoute, 'activate']) {
    for (const body of [
      undefined, {}, { ...confirmed, confirm: undefined }, { ...confirmed, confirm: false },
      { ...confirmed, confirm: 'true' }, { ...confirmed, confirm: 1 },
      { ...confirmed, expected_revision: undefined }, { ...confirmed, expected_revision: ['c'.repeat(64)] },
      { ...confirmed, expected_revision: 'C'.repeat(64) }, { ...confirmed, expected_revision: 'c'.repeat(63) },
    ]) {
      assert.equal((await call(route, body, never)).status, 400);
    }
  }
  for (const draft_id of [undefined, '../other', 'x'.repeat(32), [draftId]]) {
    assert.equal((await call('activate', { ...confirmed, draft_id }, never)).status, 400);
  }
});

test('publication is POST-only and rejects cross-origin and non-JSON requests', async () => {
  for (const route of [approvalRoute, 'activate']) {
    for (const method of ['GET', 'PUT', 'PATCH', 'DELETE']) {
      assert.equal((await call(route, confirmed, never, {}, method)).status, 405);
    }
    assert.equal((await call(route, confirmed, never, { origin: 'https://other.example' })).status, 403);
    assert.equal((await call(route, confirmed, never, { 'sec-fetch-site': 'cross-site' })).status, 403);
    assert.equal((await call(route, confirmed, never, { 'content-type': 'text/plain' })).status, 415);
  }
  assert.equal((await call('drafts/invalid/approve', confirmed, never)).status, 405);
});

test('publication preserves backend conflicts and never retries unconfirmed writes', async () => {
  for (const route of [approvalRoute, 'activate']) {
    const conflict = await call(route, confirmed, async () => ({
      ok: false, status: 409, json: async () => ({ detail: 'Draft base version is no longer active' }),
    }));
    assert.equal(conflict.status, 409);
    assert.equal(conflict.body.error, 'Draft base version is no longer active');
    let requests = 0;
    const unconfirmed = await call(route, confirmed, async () => { requests++; throw Error('lost response'); });
    assert.equal(requests, 1);
    assert.equal(unconfirmed.status, 502);
    assert.match(unconfirmed.body.error, /Refresh the project or draft before retrying/);
  }
});

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
