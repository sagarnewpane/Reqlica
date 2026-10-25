import Link from 'next/link';
import { Download, RefreshCw } from 'lucide-react';
import Card from '@/components/ui/Card/Card';
import PageHeader from '@/components/ui/PageHeader/PageHeader';
import Button from '@/components/ui/Button/Button';
import Select from '@/components/ui/Select/Select';
import Textarea from '@/components/ui/Textarea/Textarea';
import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogCancel,
} from '@/components/ui/shadcn/alert-dialog';
import { dataCounts, parseDataRecords, type DataMode } from '@/lib/sample-data';
import SavedItems from '@/components/workspace/SavedItems';
import DatabaseDefinitions from './DatabaseDefinitions';
import { useSampleData } from './use-sample-data';
import styles from './SampleData.module.css';

export default function SampleDataWorkspace({
  projectId,
}: {
  projectId: string;
}) {
  const data = useSampleData(projectId);
  const { mode, table, source, schema, batch, target, busy } = data;
  const counts = batch
    ? dataCounts(batch.mode, batch.records, batch.table)
    : {};
  const confirmationCounts = target
    ? dataCounts(target.mode, target.records, target.table)
    : {};

  function download() {
    try {
      const records = parseDataRecords(source, mode);
      const url = URL.createObjectURL(
        new Blob([JSON.stringify(records, null, 2)], {
          type: 'application/json',
        }),
      );
      const link = document.createElement('a');
      link.href = url;
      link.download = `reqlica-${mode}-records.json`;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 0);
      data.setNotice(
        'JSON downloaded to your computer. No project files or live records were changed.',
      );
    } catch (err) {
      data.setError((err as Error).message);
    }
  }

  return (
    <div className={styles.content}>
      <PageHeader
        title="Sample data"
        actions={
          <Button asChild variant="secondary">
            <Link href={`/project?projectId=${projectId}`}>Generate in chat</Link>
          </Button>
        }
      />
      <p className={styles.context}>
        Save datasets in the project library or download JSON. Inserting adds
        new records; existing rows are unchanged.
      </p>
      <SavedItems
        projectId={projectId}
        kind="dataset"
        disabled={!!busy}
        data={{ mode, table, source }}
        onLoad={data.loadDataset}
      />
      <div className={styles.actions}>
        <Button
          variant="secondary"
          disabled={!!busy}
          onClick={() => {
            void navigator.clipboard
              .writeText(source)
              .then(() => data.setNotice('JSON copied.'))
              .catch(() => data.setError('Clipboard unavailable.'));
          }}
        >
          Copy JSON
        </Button>
        <Button
          variant="secondary"
          disabled={!!busy || (mode !== 'json' && !schema)}
          onClick={() => {
            try {
              const target = schema?.tables.find((item) => item.name === table);
              const row = Object.fromEntries(
                (target?.columns || [])
                  .filter((c) => !c.pk && !c.hidden)
                  .map((c) => [
                    c.name,
                    c.type.toUpperCase().includes('INT')
                      ? 0
                      : c.type.toUpperCase().match(/REAL|NUM|FLOAT|DOUBLE/)
                        ? 0
                        : '',
                  ]),
              );
              data.changeSource(
                JSON.stringify(
                  mode === 'related' ? { [table]: [row] } : [row],
                  null,
                  2,
                ),
              );
              data.setNotice(
                'Template inserted. Fill values and validate before inserting.',
              );
            } catch (reason) {
              data.setError((reason as Error).message);
            }
          }}
        >
          Insert record template
        </Button>
      </div>
      {data.error && (
        <p role="alert" className={styles.warning}>
          {data.error}
        </p>
      )}
      {data.notice && (
        <p role="status" className={styles.notice}>
          {data.notice}
        </p>
      )}
      {data.uncertain && (
        <section className={styles.warning} aria-label="Unconfirmed insertion">
          <h2>Check live data before continuing</h2>
          <p>
            The previous insertion may already have committed. Inserts are not
            idempotent. Use the project’s live endpoints to inspect records
            before preparing another attempt.
          </p>
          <Link
            className={styles.link}
            href={{ pathname: '/request-tester', query: { projectId } }}
          >
            Open request tester →
          </Link>
          <label className={styles.confirmation}>
            <input
              type="checkbox"
              checked={data.inspected}
              disabled={!!busy}
              onChange={(event) => data.setInspected(event.target.checked)}
            />
            I checked live data before preparing another validation.
          </label>
        </section>
      )}
      <div className={styles.workspace}>
        <Card className={styles.destination}>
          <h2>1. Choose a destination</h2>
          <div className={styles.field}>
            <label htmlFor="data-mode">Data target</label>
            <Select
              id="data-mode"
              value={mode}
              disabled={!!busy}
              onValueChange={(next) => data.changeMode(next as DataMode)}
            >
              <option value="json">JSON fixture (export only)</option>
              <option value="sqlite">Existing SQLite table</option>
              <option value="related">Related database records</option>
            </Select>
          </div>
          {mode === 'json' ? (
            <p className={styles.context}>
              Export only. Does not change your API.
            </p>
          ) : (
            <>
              <button
                type="button"
                className={styles.secondary}
                disabled={!!busy}
                onClick={() => void data.loadSchema()}
              >
                <RefreshCw size={16} />
                {busy === 'schema'
                  ? 'Loading definitions…'
                  : schema
                    ? 'Refresh tables'
                    : 'Load tables'}
              </button>
              {data.schemaError && (
                <p role="alert" className={styles.warning}>
                  {data.schemaError} JSON fixtures remain available.
                </p>
              )}
              {schema ? (
                <DatabaseDefinitions
                  schema={schema}
                  mode={mode}
                  table={table}
                  disabled={!!busy}
                  onTableChange={data.changeTable}
                />
              ) : (
                <p className={styles.context}>
                  Load tables to validate database records.
                </p>
              )}
            </>
          )}
        </Card>
        <Card className={styles.editor}>
          <h2>2. Edit & validate</h2>
          <p className={styles.context}>
            {mode === 'related'
              ? 'Provide an object mapping 1–10 existing table names to record arrays. Up to 100 records total.'
              : 'Provide an array of 1–100 nonempty JSON objects.'}{' '}
            Maximum 128 KiB. Paste records prepared manually or copied from
            project chat.
          </p>
          {mode === 'related' && (
            <p className={styles.context}>
              References follow declared foreign keys, for example{' '}
              <code>{'{"$ref":"users.0.id"}'}</code>. Row indices are
              zero-based. Preview IDs are provisional; actual IDs resolve during
              insertion. Cycles are rejected.
            </p>
          )}
          {mode === 'sqlite' && (
            <p className={styles.context}>
              Values must be scalar. Encode JSON columns as text and supply
              valid existing foreign-key IDs. Tables with unsupported
              application contracts may be refused by the backend.
            </p>
          )}
          <label htmlFor="data-records" className={styles.fieldLabel}>
            {mode === 'related' ? 'JSON records by table' : 'JSON records'}
          </label>
          <Textarea
            id="data-records"
            rows={16}
            value={source}
            disabled={!!busy}
            spellCheck={false}
            onChange={(event) => data.changeSource(event.target.value)}
          />
          <div className={styles.actions}>
            <button
              type="button"
              className={styles.secondary}
              disabled={!!busy}
              onClick={download}
            >
              <Download size={16} />
              Download JSON
            </button>
            <Button
              type="button"
              disabled={
                !!busy ||
                (mode !== 'json' && !schema) ||
                (data.uncertain && !data.inspected)
              }
              onClick={() => void data.validate()}
            >
              {busy === 'validate' ? 'Validating…' : 'Validate records'}
            </Button>
          </div>
          <p className={styles.context}>
            Validation never inserts live records. SQLite checks use a
            disposable copy; passing a preview does not reserve IDs or unique
            values.
          </p>
        </Card>
      </div>
      {batch && (
        <Card className={styles.preview}>
          <div className={styles.sectionHeading}>
            <div>
              <h2>Validated preview</h2>
              <p>{batch.preview.validation.message}</p>
            </div>
            <strong
              className={
                batch.preview.validation.status === 'passed'
                  ? styles.passed
                  : styles.failed
              }
            >
              {batch.preview.validation.status === 'passed'
                ? 'Passed'
                : 'Failed — edit and validate again'}
            </strong>
          </div>
          <ul className={styles.counts}>
            {Object.entries(counts).map(([name, count]) => (
              <li key={name}>
                <code>{name}</code>
                <span>
                  {count} {count === 1 ? 'record' : 'records'}
                </span>
              </li>
            ))}
          </ul>
          <pre className={styles.json}>
            {JSON.stringify(batch.records, null, 2)}
          </pre>
          {batch.preview.validation.issues && (
            <details className={styles.definition}>
              <summary>Validation issues</summary>
              <pre className={styles.json}>
                {JSON.stringify(batch.preview.validation.issues, null, 2)}
              </pre>
            </details>
          )}
          {batch.preview.note && (
            <p className={styles.context}>{batch.preview.note}</p>
          )}
          {batch.preview.insertion_order && (
            <details className={styles.definition}>
              <summary>Reviewed insertion order</summary>
              <ol>
                {batch.preview.insertion_order.map((item, index) => (
                  <li key={index}>
                    <code>{item.table}</code> · row {item.row} (zero-based)
                  </li>
                ))}
              </ol>
            </details>
          )}
          <details className={styles.definition}>
            <summary>Preview digest & application checks</summary>
            <code className={styles.digest}>
              {batch.preview.preview_digest}
            </code>
            <pre className={styles.json}>
              {JSON.stringify(
                {
                  application_revision: batch.preview.application_revision,
                  checks: batch.preview.application_checks,
                },
                null,
                2,
              )}
            </pre>
          </details>
          <div className={styles.actions}>
            <p>
              {mode === 'json'
                ? 'JSON fixtures can be exported, not inserted into SQLite.'
                : '3. Confirm an atomic append. Existing rows remain unchanged; all tables roll back if any row fails.'}
            </p>
            <Button
              type="button"
              disabled={!data.eligible}
              onClick={data.reviewInsertion}
            >
              Insert reviewed records…
            </Button>
          </div>
        </Card>
      )}
      {data.result && (
        <Card className={styles.preview}>
          <h2>Last confirmed insertion</h2>
          <p className={styles.context}>
            {data.result.inserted_count} records saved. This result is not a
            live row listing.
          </p>
          <details className={styles.definition}>
            <summary>Inserted IDs and counts</summary>
            <pre className={styles.json}>
              {JSON.stringify(
                data.result,
                (_, value) =>
                  typeof value === 'number' &&
                  Number.isInteger(value) &&
                  !Number.isSafeInteger(value)
                    ? '[Integer exceeds browser precision; inspect with backend tools]'
                    : value,
                2,
              )}
            </pre>
          </details>
        </Card>
      )}
      <AlertDialog
        open={target !== null}
        onOpenChange={(next) => {
          if (!next) data.closeInsertion();
        }}
      >
        <AlertDialogContent
          className={styles.dialog}
          onEscapeKeyDown={(event) => {
            if (busy === 'insert') event.preventDefault();
          }}
        >
          <AlertDialogHeader>
            <AlertDialogTitle>Insert these reviewed records?</AlertDialogTitle>
            <AlertDialogDescription>
              This appends records to the project’s persistent live SQLite
              database. No rows are replaced. All selected tables are saved in
              one transaction. The backend rechecks the exact preview, schema,
              application code, and current constraints. There is no automatic
              retry or undo.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <ul className={styles.counts}>
            {Object.entries(confirmationCounts).map(([name, count]) => (
              <li key={name}>
                <code>{name}</code>
                <span>
                  {count} {count === 1 ? 'record' : 'records'}
                </span>
              </li>
            ))}
          </ul>
          {target && (
            <dl className={styles.metadata}>
              <dt>Project</dt>
              <dd>
                <code>{projectId}</code>
              </dd>
              <dt>Exact preview digest</dt>
              <dd>
                <code>{target.preview.preview_digest}</code>
              </dd>
              <dt>Schema fingerprint</dt>
              <dd>
                <code>{target.preview.schema_fingerprint}</code>
              </dd>
            </dl>
          )}
          <AlertDialogFooter>
            <AlertDialogCancel disabled={busy === 'insert'}>
              Cancel
            </AlertDialogCancel>
            <Button
              type="button"
              disabled={!!busy}
              onClick={() => void data.insert()}
            >
              {busy === 'insert' ? 'Inserting…' : 'Confirm insertion'}
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
