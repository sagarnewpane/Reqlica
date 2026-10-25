// Server adapter coverage moved to test_dashboard.py. Browser helper tests remain here.
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
