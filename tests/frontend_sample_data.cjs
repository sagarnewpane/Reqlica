const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const ts = require('../frontend/node_modules/typescript');

function load(file, globals = {}) {
  const exports = {};
  const code = ts.transpileModule(fs.readFileSync(file, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText;
  vm.runInNewContext(code, { exports, URL, Buffer, TextEncoder, AbortSignal, process: { env: {} }, ...globals });
  return exports;
}
const projectId = 'a'.repeat(32), fingerprint = 'b'.repeat(64), digest = 'c'.repeat(64);
const records = [{ title: 'Example book' }];
const relatedRecords = { books: records, reviews: [{ book_id: { $ref: 'books.0.id' }, rating: 5 }] };
const sqlite = { target: 'sqlite', table: 'books', expected_schema: fingerprint, records };
const related = { expected_schema: fingerprint, records_by_table: relatedRecords };
const never = () => assert.fail('Unexpected upstream data operation');
const ok = { ok: true, status: 200, json: async () => ({ status: 'preview', validation: { status: 'passed' } }) };
async function call(route, { method = 'POST', body, fetch = never, headers = {}, id = projectId } = {}) {
  const { default: handler } = load('frontend/src/pages/api/projects/[projectId]/data/[...operation].ts', { fetch });
  const result = { headers: {} };
  const response = { setHeader(name, value) { result.headers[name] = value; }, status(code) { result.status = code; return this; }, json(value) { result.body = value; return this; } };
  await handler({ method, body, query: { projectId: id, operation: route.split('/') }, headers: { host: 'localhost:3000', 'content-type': 'application/json', ...headers } }, response);
  return result;
}

test('data schema reads are project-scoped, uncached and do not read records or request insertion', async () => {
  const response = await call('schema', { method: 'GET', fetch: async (url, init) => {
    assert.equal(url.href, `http://127.0.0.1:3001/api/projects/${projectId}/data/schema`);
    assert.equal(init.method, 'GET'); assert.equal(init.body, undefined); assert.equal(init.redirect, 'error'); return ok;
  } });
  assert.equal(response.status, 200); assert.equal(response.headers['Cache-Control'], 'no-store');
});

test('JSON, SQLite and related validations forward only their supported fields', async () => {
  for (const [route, expected] of [['validate', { target: 'json', records }], ['validate', sqlite], ['related/validate', related]]) {
    const response = await call(route, { body: { ...expected, api_key: 'never-forward', confirm: true }, headers: { 'x-reqlica-groq-key': 'never-forward' }, fetch: async (url, init) => {
      assert.equal(url.pathname, `/api/projects/${projectId}/data/${route}`);
      assert.deepEqual(JSON.parse(init.body), expected);
      assert.equal(init.headers['X-Reqlica-Groq-Key'], undefined); return ok;
    } });
    assert.equal(response.status, 200);
  }
});

test('live inserts require literal consent, a matching preview digest and a schema fingerprint', async () => {
  for (const [route, valid] of [['insert', sqlite], ['related/insert', related]]) {
    const body = { ...valid, confirm: true, expected_preview_digest: digest };
    for (const change of [{ confirm: undefined }, { confirm: false }, { confirm: 'true' }, { expected_preview_digest: undefined }, { expected_preview_digest: 'invalid' }, { expected_schema: undefined }, { expected_schema: [] }]) {
      assert.equal((await call(route, { body: { ...body, ...change } })).status, 400);
    }
    assert.equal((await call(route, { body, fetch: async (url, init) => {
      assert.equal(url.port, '3001'); assert.deepEqual(JSON.parse(init.body), body); return ok;
    } })).status, 200);
  }
  assert.equal((await call('insert', { body: { target: 'json', records, confirm: true, expected_preview_digest: digest } })).status, 400);
});

test('invalid batches, unknown operations, unsupported methods and project traversal never reach the console', async () => {
  for (const rows of [[], [{}], [null], [[1]], [1], Array.from({ length: 101 }, () => ({ x: 1 }))]) {
    assert.equal((await call('validate', { body: { target: 'json', records: rows } })).status, 400);
  }
  for (const records_by_table of [{}, [], { books: [] }, Object.fromEntries(Array.from({ length: 11 }, (_, i) => [`table${i}`, [{ x: 1 }]])), { books: Array.from({ length: 60 }, () => ({ x: 1 })), reviews: Array.from({ length: 60 }, () => ({ x: 1 })) }]) {
    assert.equal((await call('related/validate', { body: { expected_schema: fingerprint, records_by_table } })).status, 400);
  }
  assert.equal((await call('validate', { body: { target: 'json', records: [{ value: 'x'.repeat(128 * 1024) }] } })).status, 413);
  assert.equal((await call('validate', { body: { target: 'json', records: [{ id: Number.MAX_SAFE_INTEGER + 1 }] } })).status, 400);
  assert.equal((await call('validate', { body: { target: 'json', records: [{ amount: Infinity }] } })).status, 400);
  assert.equal((await call('validate', { body: { target: 'json', table: 'books', records } })).status, 400);
  assert.equal((await call('validate', { body: sqlite, id: '../other' })).status, 400);
  for (const route of ['delete', 'reset', '../insert', 'related/delete', 'schema']) assert.equal((await call(route, { body: sqlite })).status, 405);
  assert.equal((await call('insert', { method: 'GET' })).status, 405);
});

test('data operations reject cross-origin requests and non-JSON input', async () => {
  for (const headers of [{ origin: 'https://other.example' }, { 'sec-fetch-site': 'cross-site' }]) assert.equal((await call('validate', { body: sqlite, headers })).status, 403);
  assert.equal((await call('validate', { body: sqlite, headers: { 'content-type': 'text/plain' } })).status, 415);
});

test('backend validation failures and application refusals retain actionable messages', async () => {
  const response = await call('validate', { body: sqlite, fetch: async () => ({ ok: false, status: 422, json: async () => ({ detail: { message: "This data can't be mocked reliably" } }) }) });
  assert.equal(response.status, 422); assert.match(response.body.error, /can't be mocked reliably/);
});

test('an uncertain insertion is never retried, while preview failures explicitly indicate no live insertion', async () => {
  for (const route of ['insert', 'related/insert', 'validate']) {
    let requests = 0;
    const body = { ...(route.startsWith('related/') ? related : sqlite), confirm: true, expected_preview_digest: digest };
    const response = await call(route, { body, fetch: async () => { requests++; throw Error('lost'); } });
    assert.equal(requests, 1); assert.equal(response.status, 502);
    assert.match(response.body.error, route === 'validate' ? /No live insertion was requested/ : /Records may already have been saved/);
  }
});

test('live constraint conflicts preserve atomic-rollback diagnostics without retrying', async () => {
  let requests = 0;
  const response = await call('related/insert', { body: { ...related, confirm: true, expected_preview_digest: digest }, fetch: async () => {
    requests++;
    return { ok: false, status: 409, json: async () => ({ detail: { message: 'All tables were rolled back; no automatic retry was made.' } }) };
  } });
  assert.equal(requests, 1);
  assert.equal(response.status, 409);
  assert.match(response.body.error, /All tables were rolled back/);
});

const helpers = load('frontend/src/lib/sample-data.ts');
const schema = { status: 'available', fingerprint, truncated: false, records_included: false, triggers: [], tables: [{ name: 'books', sql: 'CREATE TABLE books(id INTEGER, title TEXT)', storage_type: 'table', columns: [], foreign_keys: [] }, { name: 'reviews', sql: 'CREATE TABLE reviews(rating INTEGER)', columns: [], foreign_keys: [] }] };
test('local parsing supports bounded JSON and related references without inventing fields', () => {
  assert.equal(JSON.stringify(helpers.parseDataRecords(JSON.stringify(records), 'json')), JSON.stringify(records));
  assert.equal(JSON.stringify(helpers.parseDataRecords(JSON.stringify(relatedRecords), 'related')), JSON.stringify(relatedRecords));
  assert.equal(JSON.stringify(helpers.dataCounts('related', relatedRecords, '')), JSON.stringify({ books: 1, reviews: 1 }));
});

test('local parsing rejects malformed, overlarge, non-finite, or precision-losing input', () => {
  for (const source of ['not json', '[]', '[{}]', '[{"id":9223372036854775807}]', '[{"n":1e400}]', JSON.stringify([{ title: 'x'.repeat(8193) }]), ' '.repeat(128 * 1024 + 1)]) assert.throws(() => helpers.parseDataRecords(source, 'json'));
  assert.throws(() => helpers.parseDataRecords('[]', 'related'));
  assert.throws(() => helpers.parseDataRecords(JSON.stringify({ books: Array.from({ length: 100 }, () => ({ x: 1 })), reviews: [{ x: 1 }] }), 'related'), /100 records/);
});

test('validation builders require current definitions and refuse missing, virtual or triggered tables', () => {
  assert.equal(JSON.stringify(helpers.dataValidationBody('json', '', records, null)), JSON.stringify({ target: 'json', records }));
  assert.equal(JSON.stringify(helpers.dataValidationBody('sqlite', 'books', records, schema)), JSON.stringify(sqlite));
  assert.throws(() => helpers.dataValidationBody('sqlite', 'books', records, null), /definitions/);
  assert.throws(() => helpers.dataValidationBody('sqlite', 'missing', records, schema), /existing table/);
  assert.throws(() => helpers.dataValidationBody('sqlite', 'books', records, { ...schema, truncated: true }), /complete/);
  assert.throws(() => helpers.dataValidationBody('sqlite', 'books', records, { ...schema, triggers: [{ tbl_name: 'BOOKS' }] }), /triggers/);
  assert.throws(() => helpers.dataValidationBody('sqlite', 'books', records, { ...schema, tables: [{ ...schema.tables[0], storage_type: 'virtual' }] }), /ordinary/);
});

test('only the current passed preview can enable insertion, never JSON fixtures or edited/stale batches', () => {
  const source = JSON.stringify(records);
  const batch = { mode: 'sqlite', table: 'books', source, records, preview: { status: 'preview', target: 'sqlite', table: 'books', schema_fingerprint: fingerprint, preview_digest: digest, validation: { status: 'passed' } } };
  assert.equal(helpers.canInsertBatch(batch, 'sqlite', 'books', source, schema), true);
  for (const [candidate, mode, table, text, definitions] of [
    [null, 'sqlite', 'books', source, schema], [batch, 'json', 'books', source, schema],
    [batch, 'sqlite', 'reviews', source, schema], [batch, 'sqlite', 'books', source + ' ', schema],
    [batch, 'sqlite', 'books', source, null], [batch, 'sqlite', 'books', source, { ...schema, fingerprint: 'e'.repeat(64) }],
    [{ ...batch, preview: { ...batch.preview, validation: { status: 'failed' } } }, 'sqlite', 'books', source, schema],
    [{ ...batch, preview: { ...batch.preview, preview_digest: 'bad' } }, 'sqlite', 'books', source, schema],
  ]) assert.equal(helpers.canInsertBatch(candidate, mode, table, text, definitions), false);
});
