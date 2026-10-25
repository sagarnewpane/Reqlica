import { useState } from 'react';
import Link from 'next/link';
import Button from '@/components/ui/Button/Button';
import { workspaceRequest } from '@/lib/chat';
import { useResource } from '@/components/workspace/use-resource';
import { type Draft, draftLabel } from '@/lib/drafts';
import s from '@/components/workspace/Workspace.module.css';
type Operation = {
  op: string;
  table: string;
  name?: string;
  new_name?: string;
  column?: unknown;
  definition?: unknown;
  copy_columns?: unknown;
};
type Preview = {
  plan: {
    id: string;
    from_schema: string;
    target_schema: string;
    operations: Operation[];
  };
  plan_digest: string;
  sql: string[];
  removed_objects: unknown[];
  current_code_revision: string;
};
type Receipt = {
  id: string;
  phase: string;
  draft_id: string;
  from_schema: string;
  target_schema: string;
};
export default function MigrationsPanel({ projectId }: { projectId: string }) {
  const schema = useResource<{ current_schema_fingerprint: string }>(
    projectId,
    'data-baseline',
  );
  const history = useResource<{ migrations: Receipt[] }>(
    projectId,
    'database-migrations',
  );
  const listing = useResource<{ drafts: Draft[] }>(projectId, 'drafts');
  const [operation, setOperation] = useState<Operation>({
    op: 'add_column',
    table: '',
    name: '',
    new_name: '',
  });
  const [type, setType] = useState('TEXT');
  const [definition, setDefinition] = useState(
    '{"name":"items","columns":[{"name":"id","type":"INTEGER","primary_key":true}]}',
  );
  const [copies, setCopies] = useState('{}');
  const [operations, setOperations] = useState<Operation[]>([]);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [draftId, setDraftId] = useState('');
  const [saved, setSaved] = useState<{
    draftId: string;
    revision: string;
  } | null>(null);
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  function invalidate() {
    setPreview(null);
    setSaved(null);
    setConfirm(false);
  }
  async function run(action: () => Promise<void>) {
    if (busy) return;
    setBusy(true);
    setError('');
    try {
      await action();
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
      setConfirm(false);
    }
  }
  return (
    <div className={s.stack}>
      <section className={s.panel}>
        <h2>Database migrations</h2>
        <p>
          Build operations, inspect SQL on a database copy, save the plan to a
          draft, then validate/test that draft. Applying changes the live
          database and stops the worker.
        </p>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            try {
              const next: Operation = {
                op: operation.op,
                table: operation.table,
              };
              if (['drop_column', 'rename_column'].includes(next.op))
                next.name = operation.name;
              if (['rename_column', 'rename_table'].includes(next.op))
                next.new_name = operation.new_name;
              if (next.op === 'add_column')
                next.column = { name: operation.name, type, nullable: true };
              if (next.op === 'rebuild_table') {
                next.definition = JSON.parse(definition);
                next.copy_columns = JSON.parse(copies);
              }
              setOperations([...operations, next]);
              invalidate();
            } catch {
              setError(
                'Enter valid JSON for rebuild definition and column mapping.',
              );
            }
          }}
        >
          <div className={s.grid}>
            <label>
              Operation
              <select
                value={operation.op}
                onChange={(e) =>
                  setOperation({ ...operation, op: e.target.value })
                }
              >
                {[
                  'add_column',
                  'drop_column',
                  'rename_column',
                  'rename_table',
                  'drop_table',
                  'rebuild_table',
                ].map((v) => (
                  <option key={v}>{v}</option>
                ))}
              </select>
            </label>
            <label>
              Table
              <input
                value={operation.table}
                onChange={(e) =>
                  setOperation({ ...operation, table: e.target.value })
                }
                required
                pattern="[A-Za-z][A-Za-z0-9_]*"
              />
            </label>
            {['add_column', 'drop_column', 'rename_column'].includes(
              operation.op,
            ) && (
              <label>
                Column
                <input
                  value={operation.name}
                  onChange={(e) =>
                    setOperation({ ...operation, name: e.target.value })
                  }
                  required
                />
              </label>
            )}
            {operation.op === 'add_column' && (
              <label>
                Column type
                <select value={type} onChange={(e) => setType(e.target.value)}>
                  {['TEXT', 'INTEGER', 'REAL', 'BLOB', 'NUMERIC'].map((v) => (
                    <option key={v}>{v}</option>
                  ))}
                </select>
              </label>
            )}
            {['rename_column', 'rename_table'].includes(operation.op) && (
              <label>
                New name
                <input
                  value={operation.new_name}
                  onChange={(e) =>
                    setOperation({ ...operation, new_name: e.target.value })
                  }
                  required
                />
              </label>
            )}
          </div>
          {operation.op === 'rebuild_table' && (
            <>
              <label>
                Target table definition (JSON)
                <textarea
                  value={definition}
                  onChange={(e) => setDefinition(e.target.value)}
                />
              </label>
              <label>
                Copy columns (target: source, JSON)
                <textarea
                  value={copies}
                  onChange={(e) => setCopies(e.target.value)}
                />
              </label>
            </>
          )}
          <Button type="submit" variant="secondary" disabled={busy}>
            Add operation
          </Button>
        </form>
        <ol>
          {operations.map((item, index) => (
            <li key={index}>
              {item.op} {item.table} {item.name} {item.new_name}{' '}
              <Button
                variant="ghost"
                disabled={busy}
                onClick={() => {
                  setOperations(operations.filter((_, i) => i !== index));
                  invalidate();
                }}
              >
                Remove
              </Button>
            </li>
          ))}
        </ol>
        <div className={s.row}>
          <Button
            disabled={
              busy ||
              !operations.length ||
              !schema.data?.current_schema_fingerprint
            }
            onClick={() =>
              void run(async () => {
                const fresh = await workspaceRequest<{
                  current_schema_fingerprint: string;
                }>(projectId, 'data-baseline');
                setPreview(
                  await workspaceRequest<Preview>(
                    projectId,
                    'database-migrations/preview',
                    'POST',
                    {
                      expected_schema: fresh.current_schema_fingerprint,
                      operations,
                    },
                  ),
                );
                setSaved(null);
              })
            }
          >
            Preview SQL and impact
          </Button>
          <Button
            variant="secondary"
            onClick={() => {
              schema.refresh();
              listing.refresh();
              history.refresh();
            }}
          >
            Refresh schema / drafts
          </Button>
        </div>
        {preview && (
          <>
            <h3>SQL preview</h3>
            <pre>{preview.sql.join(';\n')}</pre>
            <h3>Objects removed or replaced</h3>
            <pre>{JSON.stringify(preview.removed_objects, null, 2)}</pre>
            <label>
              Draft for migration
              <select
                value={draftId}
                disabled={busy}
                onChange={(e) => {
                  setDraftId(e.target.value);
                  setSaved(null);
                }}
              >
                <option value="">Create migration draft</option>
                {listing.data?.drafts
                  .filter((d) => d.base_is_current !== false)
                  .map((d) => (
                    <option value={d.id} key={d.id}>
                      {draftLabel(d)}
                    </option>
                  ))}
              </select>
            </label>
            <div className={s.row}>
              <Button
                disabled={busy}
                onClick={() =>
                  void run(async () => {
                    const draft = await workspaceRequest<Draft>(
                      projectId,
                      draftId ? `drafts/${draftId}` : 'drafts',
                      draftId ? 'GET' : 'POST',
                      draftId
                        ? undefined
                        : {
                            name: `Database migration ${preview.plan.id.slice(0, 8)}`,
                          },
                    );
                    const updated = await workspaceRequest<Draft>(
                      projectId,
                      `drafts/${draft.id}/files?path=database%2Fmigration.json`,
                      'PUT',
                      {
                        expected_revision: draft.revision,
                        content: JSON.stringify(
                          { version: 1, ...preview.plan },
                          null,
                          2,
                        ),
                      },
                    );
                    setSaved({ draftId: draft.id, revision: updated.revision });
                    setNotice(
                      'Migration plan saved. Validate and test this draft before applying.',
                    );
                    listing.refresh();
                  })
                }
              >
                Save plan to draft
              </Button>
              {saved && (
                <Link
                  href={`/draft-review?projectId=${projectId}&draftId=${saved.draftId}`}
                >
                  Validate / test migration draft
                </Link>
              )}
            </div>
            {saved && (
              <>
                <label className={s.check}>
                  <input
                    type="checkbox"
                    checked={confirm}
                    onChange={(e) => setConfirm(e.target.checked)}
                  />
                  Apply the reviewed migration to live data and stop worker
                </label>
                <Button
                  disabled={!confirm || busy}
                  onClick={() =>
                    void run(async () => {
                      await workspaceRequest(
                        projectId,
                        'database-migrations/apply',
                        'POST',
                        {
                          draft_id: saved.draftId,
                          expected_revision: saved.revision,
                          expected_schema: preview.plan.from_schema,
                          expected_plan_digest: preview.plan_digest,
                          confirm: true,
                        },
                      );
                      setNotice('Migration applied. Worker stopped.');
                      setSaved(null);
                      history.refresh();
                      schema.refresh();
                    })
                  }
                >
                  Apply reviewed migration
                </Button>
              </>
            )}
          </>
        )}
        {error && <p role="alert">{error}</p>}
        {notice && <p role="status">{notice}</p>}
        {schema.error && <p role="alert">{schema.error}</p>}
      </section>
      <section className={s.panel}>
        <h2>Migration history / recovery</h2>
        <p>
          Rollback restores the saved pre-migration database. Recovery resolves
          an interrupted prepared receipt against the current schema.
        </p>
        <label className={s.check}>
          <input
            type="checkbox"
            checked={confirm}
            onChange={(e) => setConfirm(e.target.checked)}
          />
          Confirm selected rollback or recovery and worker stop
        </label>
        <ul className={s.list}>
          {history.data?.migrations.map((item) => (
            <li className={s.item} key={item.id}>
              <strong>{item.id.slice(0, 8)}</strong> · {item.phase}
              <div className={s.row}>
                {(item.phase === 'prepared'
                  ? ['recover']
                  : item.phase === 'applied'
                    ? ['rollback']
                    : []
                ).map((action) => (
                  <Button
                    key={action}
                    disabled={!confirm || busy}
                    variant="secondary"
                    onClick={() =>
                      void run(async () => {
                        const fresh = await workspaceRequest<{
                          current_schema_fingerprint: string;
                        }>(projectId, 'data-baseline');
                        await workspaceRequest(
                          projectId,
                          `database-migrations/${action}`,
                          'POST',
                          {
                            expected_revision: item.id,
                            expected_schema: fresh.current_schema_fingerprint,
                            confirm: true,
                          },
                        );
                        setNotice(`${action} complete. Worker stopped.`);
                        history.refresh();
                        schema.refresh();
                      })
                    }
                  >
                    {action}
                  </Button>
                ))}
              </div>
            </li>
          ))}
        </ul>
        {history.error && <p role="alert">{history.error}</p>}
      </section>
    </div>
  );
}
