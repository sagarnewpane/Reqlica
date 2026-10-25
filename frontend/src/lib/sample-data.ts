export type DataMode = 'json' | 'sqlite' | 'related';
export type JsonValue =
  null | boolean | number | string | JsonValue[] | { [key: string]: JsonValue };
export type DataRecord = Record<string, JsonValue>;
export type RecordBatch = DataRecord[] | Record<string, DataRecord[]>;
export type DatabaseColumn = {
  name: string;
  type: string;
  notnull: number;
  pk: number;
  hidden: number;
  dflt_value: unknown;
};
export type DatabaseTable = {
  name: string;
  sql: string;
  storage_type?: string;
  columns: DatabaseColumn[];
  foreign_keys: { from: string; table: string; to: string | null }[];
};
export type DatabaseSchema = {
  status: string;
  fingerprint: string;
  truncated: boolean;
  records_included: boolean;
  tables: DatabaseTable[];
  triggers: { tbl_name: string }[];
};
export type DataPreview = {
  status: string;
  target: DataMode;
  table?: string | null;
  records?: DataRecord[];
  records_by_table?: Record<string, DataRecord[]>;
  schema_fingerprint: string | null;
  preview_digest: string;
  validation: { status: string; message: string; issues?: unknown[] };
  counts?: Record<string, number>;
  note?: string;
  insertion_order?: { table: string; row: number }[];
  application_revision?: string | null;
  application_checks?: unknown;
};
export type ValidatedBatch = {
  mode: DataMode;
  table: string;
  source: string;
  records: RecordBatch;
  preview: DataPreview;
};

const MAX_BYTES = 128 * 1024;
export function tableRestriction(table: DatabaseTable, schema: DatabaseSchema) {
  if (
    (table.storage_type && table.storage_type !== 'table') ||
    !table.sql ||
    /^\s*CREATE\s+VIRTUAL\s+TABLE\b/i.test(table.sql)
  )
    return 'Only existing ordinary SQLite tables are supported.';
  if (
    schema.triggers.some(
      (trigger) => trigger.tbl_name.toLowerCase() === table.name.toLowerCase(),
    )
  )
    return 'Tables with triggers cannot be seeded by this importer.';
  return null;
}

export function parseDataRecords(source: string, mode: DataMode): RecordBatch {
  if (new TextEncoder().encode(source).length > MAX_BYTES)
    throw new Error('Keep the JSON input within 128 KiB.');
  let value: unknown;
  try {
    value = JSON.parse(source);
  } catch {
    throw new Error('Enter valid JSON before validating or exporting records.');
  }

  function check(item: unknown, depth = 0): void {
    if (depth > 6) throw new Error('Records may be nested at most six levels.');
    if (
      typeof item === 'number' &&
      (!Number.isFinite(item) ||
        (Number.isInteger(item) && !Number.isSafeInteger(item)))
    )
      throw new Error(
        'Use finite numbers and safely representable integers; this browser cannot preserve larger integer IDs exactly.',
      );
    if (
      typeof item === 'string' &&
      new TextEncoder().encode(item).length > 8192
    )
      throw new Error('Individual strings must fit within 8 KiB.');
    if (Array.isArray(item)) {
      if (item.length > 100)
        throw new Error('Nested arrays may contain at most 100 items.');
      item.forEach((child) => check(child, depth + 1));
    } else if (item && typeof item === 'object') {
      const entries = Object.entries(item);
      if (
        entries.length > 50 ||
        entries.some(([key]) => !key || key.length > 128)
      )
        throw new Error(
          'Use at most 50 named fields per object, with names up to 128 characters.',
        );
      entries.forEach(([, child]) => check(child, depth + 1));
    }
  }

  function checkRows(rows: unknown): asserts rows is DataRecord[] {
    if (
      !Array.isArray(rows) ||
      rows.length < 1 ||
      rows.length > 100 ||
      rows.some(
        (row) =>
          !row ||
          typeof row !== 'object' ||
          Array.isArray(row) ||
          !Object.keys(row).length,
      )
    )
      throw new Error('Provide an array of 1–100 nonempty record objects.');
    rows.forEach((row) => check(row));
  }

  if (mode === 'related') {
    if (!value || typeof value !== 'object' || Array.isArray(value))
      throw new Error(
        'Related records must be an object mapping table names to record arrays.',
      );
    const tables = Object.entries(value);
    if (
      tables.length < 1 ||
      tables.length > 10 ||
      tables.some(([name]) => !name || name.length > 128)
    )
      throw new Error('Provide records for 1–10 named existing tables.');
    let count = 0;
    tables.forEach(([, rows]) => {
      checkRows(rows);
      count += rows.length;
    });
    if (count > 100)
      throw new Error('Use at most 100 records across all tables.');
    return value as Record<string, DataRecord[]>;
  }
  checkRows(value);
  return value;
}

export function dataCounts(
  mode: DataMode,
  records: RecordBatch,
  table: string,
) {
  return mode === 'related'
    ? Object.fromEntries(
        Object.entries(records).map(([name, rows]) => [
          name,
          (rows as DataRecord[]).length,
        ]),
      )
    : {
        [mode === 'json' ? 'JSON fixture' : table]: (records as DataRecord[])
          .length,
      };
}

export function dataValidationBody(
  mode: DataMode,
  table: string,
  records: RecordBatch,
  schema: DatabaseSchema | null,
) {
  if (mode === 'json') return { target: 'json', records };
  if (
    !schema ||
    schema.status !== 'available' ||
    schema.truncated ||
    !/^[a-f0-9]{64}$/.test(schema.fingerprint)
  )
    throw new Error(
      'Load complete, current database definitions before validation.',
    );
  const names = mode === 'related' ? Object.keys(records) : [table];
  for (const name of names) {
    const definition = schema.tables.find((item) => item.name === name);
    if (!definition)
      throw new Error(
        `Select an existing table: ${name || '(none selected)'}.`,
      );
    const restriction = tableRestriction(definition, schema);
    if (restriction) throw new Error(`${name}: ${restriction}`);
  }
  return mode === 'related'
    ? { expected_schema: schema.fingerprint, records_by_table: records }
    : { target: 'sqlite', table, expected_schema: schema.fingerprint, records };
}

export function canInsertBatch(
  batch: ValidatedBatch | null,
  mode: DataMode,
  table: string,
  source: string,
  schema: DatabaseSchema | null,
) {
  return (
    !!batch &&
    mode !== 'json' &&
    batch.mode === mode &&
    batch.table === table &&
    batch.source === source &&
    (mode === 'related' || batch.preview.table === table) &&
    batch.preview.status === 'preview' &&
    batch.preview.target === mode &&
    batch.preview.validation.status === 'passed' &&
    /^[a-f0-9]{64}$/.test(batch.preview.preview_digest) &&
    !!schema &&
    schema.status === 'available' &&
    !schema.truncated &&
    batch.preview.schema_fingerprint === schema.fingerprint
  );
}
