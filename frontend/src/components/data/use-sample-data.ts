import { useEffect, useRef, useState } from 'react';
import { workspaceRequest, WorkspaceError } from '@/lib/chat';
import {
  canInsertBatch,
  dataValidationBody,
  parseDataRecords,
  tableRestriction,
  type DatabaseSchema,
  type DataMode,
  type DataPreview,
  type ValidatedBatch,
} from '@/lib/sample-data';

export type InsertionResult = {
  status: 'inserted';
  inserted_count: number;
  row_ids?: (number | null)[];
  inserted_counts?: Record<string, number>;
  primary_keys?: unknown;
};

export function useSampleData(projectId: string) {
  const [mode, setMode] = useState<DataMode>('json');
  const [table, setTable] = useState('');
  const [editors, setEditors] = useState<Record<DataMode, string>>({
    json: '[]',
    sqlite: '[]',
    related: '{}',
  });
  const [schema, setSchema] = useState<DatabaseSchema | null>(null);
  const [schemaError, setSchemaError] = useState('');
  const [batch, setBatch] = useState<ValidatedBatch | null>(null);
  const [target, setTarget] = useState<ValidatedBatch | null>(null);
  const [busy, setBusy] = useState<'schema' | 'validate' | 'insert' | null>(
    null,
  );
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [result, setResult] = useState<InsertionResult | null>(null);
  const [uncertain, setUncertain] = useState(false);
  const [inspected, setInspected] = useState(false);
  const locked = useRef(false);
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  const source = editors[mode];

  function loadDataset(value: unknown) {
    const saved = value as { mode: DataMode; table?: string; source: string };
    if (
      !['json', 'sqlite', 'related'].includes(saved.mode) ||
      typeof saved.source !== 'string'
    )
      throw new Error('Invalid saved dataset.');
    parseDataRecords(saved.source, saved.mode);
    if (locked.current) return;
    setMode(saved.mode);
    setTable(saved.table || '');
    setEditors((current) => ({ ...current, [saved.mode]: saved.source }));
    setBatch(null);
    setTarget(null);
    setError('');
    setNotice(
      'Dataset loaded. Validate it against the current target before insertion.',
    );
  }
  useEffect(() => {
    if (new URLSearchParams(window.location.search).get('fromChat') !== '1')
      return;
    let active = true;
    Promise.resolve().then(() => {
      if (!active) return;
      try {
        const raw = sessionStorage.getItem(`reqlica:data-handoff:${projectId}`);
        if (!raw) return;
        const preview = JSON.parse(raw) as DataPreview;
        const mode = preview.target;
        const records =
          mode === 'related' ? preview.records_by_table : preview.records;
        if (!['json', 'sqlite', 'related'].includes(mode) || !records)
          throw new Error('Invalid chat data preview.');
        const source = JSON.stringify(records, null, 2);
        parseDataRecords(source, mode);
        setMode(mode);
        setTable(preview.table || '');
        setEditors((current) => ({ ...current, [mode]: source }));
        setNotice('Chat preview loaded. Validate again before insertion.');
        sessionStorage.removeItem(`reqlica:data-handoff:${projectId}`);
      } catch (reason) {
        setError((reason as Error).message);
      }
    });
    return () => {
      active = false;
    };
  }, [projectId]);

  function invalidate() {
    setBatch(null);
    setTarget(null);
    setError('');
    setNotice('');
  }
  function changeMode(next: DataMode) {
    if (locked.current) return;
    invalidate();
    setMode(next);
  }
  function changeTable(next: string) {
    if (locked.current) return;
    invalidate();
    setTable(next);
  }
  function changeSource(next: string) {
    if (locked.current) return;
    invalidate();
    setEditors((current) => ({ ...current, [mode]: next }));
  }

  async function loadSchema() {
    if (locked.current) return;
    locked.current = true;
    setBusy('schema');
    invalidate();
    setSchema(null);
    setSchemaError('');
    try {
      const data = await workspaceRequest<DatabaseSchema>(
        projectId,
        'data/schema',
      );
      if (!alive.current) return;
      setSchema(data);
      setTable((current) =>
        data.tables.some(
          (item) => item.name === current && !tableRestriction(item, data),
        )
          ? current
          : data.tables.find((item) => !tableRestriction(item, data))?.name ||
            '',
      );
    } catch (err) {
      if (alive.current) setSchemaError((err as Error).message);
    } finally {
      locked.current = false;
      if (alive.current) setBusy(null);
    }
  }

  async function validate() {
    if (locked.current || (uncertain && !inspected)) return;
    locked.current = true;
    setBusy('validate');
    invalidate();
    try {
      const records = parseDataRecords(source, mode);
      const body = dataValidationBody(mode, table, records, schema);
      const preview = await workspaceRequest<DataPreview>(
        projectId,
        mode === 'related' ? 'data/related/validate' : 'data/validate',
        'POST',
        body,
      );
      if (alive.current) setBatch({ mode, table, source, records, preview });
    } catch (err) {
      if (alive.current) setError((err as Error).message);
    } finally {
      locked.current = false;
      if (alive.current) setBusy(null);
    }
  }

  const eligible =
    !busy &&
    canInsertBatch(batch, mode, table, source, schema) &&
    (!uncertain || inspected);
  function reviewInsertion() {
    if (!locked.current && eligible && batch) {
      setError('');
      setTarget(batch);
    }
  }
  function closeInsertion() {
    if (!locked.current) setTarget(null);
  }

  async function insert() {
    if (
      !target ||
      locked.current ||
      !canInsertBatch(target, mode, table, source, schema) ||
      (uncertain && !inspected)
    )
      return;
    locked.current = true;
    setBusy('insert');
    setError('');
    setNotice('');
    try {
      const body = dataValidationBody(
        target.mode,
        target.table,
        target.records,
        schema,
      );
      const output = await workspaceRequest<InsertionResult>(
        projectId,
        target.mode === 'related' ? 'data/related/insert' : 'data/insert',
        'POST',
        {
          ...body,
          expected_preview_digest: target.preview.preview_digest,
          confirm: true,
        },
      );
      if (
        output.status !== 'inserted' ||
        !Number.isInteger(output.inserted_count)
      )
        throw new Error(
          'The insertion response was not recognized. Check live data before another attempt.',
        );
      if (alive.current) {
        setResult(output);
        setUncertain(false);
        setInspected(false);
        setNotice(
          `${output.inserted_count} records inserted. Validate again before any further insertion; records are not deduplicated automatically.`,
        );
      }
    } catch (err) {
      if (alive.current) {
        setError((err as Error).message);
        if (!(err instanceof WorkspaceError) || err.status >= 500) {
          setUncertain(true);
          setInspected(false);
        }
      }
    } finally {
      locked.current = false;
      if (alive.current) {
        setBatch(null);
        setTarget(null);
        setBusy(null);
      }
    }
  }

  return {
    mode,
    table,
    source,
    schema,
    schemaError,
    batch,
    target,
    busy,
    error,
    setError,
    notice,
    setNotice,
    result,
    uncertain,
    inspected,
    setInspected,
    eligible,
    loadDataset,
    changeMode,
    changeTable,
    changeSource,
    loadSchema,
    validate,
    reviewInsertion,
    closeInsertion,
    insert,
  };
}
