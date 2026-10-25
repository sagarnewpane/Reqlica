import { useState } from 'react';
import Button from '@/components/ui/Button/Button';
import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogCancel,
} from '@/components/ui/shadcn/alert-dialog';
import { workspaceRequest } from '@/lib/chat';
import type { Draft } from '@/lib/drafts';
import styles from './DatabaseSetup.module.css';

type Preview = {
  status: string;
  draft_id: string;
  revision: string;
  path: string;
  plan: unknown;
  pending_tables: string[];
  sql: string[];
  schema_fingerprint: string;
  preview_digest: string;
  validation: string;
  live_changes: false;
};
type Applied = {
  status: string;
  created_tables: string[];
  backup: string | null;
};

export default function DatabaseSetup({
  projectId,
  draft,
  disabled,
}: {
  projectId: string;
  draft: Draft;
  disabled: boolean;
}) {
  const [preview, setPreview] = useState<Preview | null>(null);
  const [applied, setApplied] = useState<Applied | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const checksCurrent =
    draft.validation?.status === 'passed' &&
    draft.validation.revision === draft.revision &&
    draft.test?.status === 'passed' &&
    draft.test.revision === draft.revision &&
    (draft.test.tests_run || 0) > 0 &&
    (draft.test.tests_passed || 0) > 0;
  const currentPreview =
    preview?.revision === draft.revision && preview.draft_id === draft.id
      ? preview
      : null;
  async function loadPreview() {
    if (busy || disabled) return;
    setBusy(true);
    setError('');
    setPreview(null);
    setApplied(null);
    try {
      const result = await workspaceRequest<Preview>(
        projectId,
        `drafts/${draft.id}/database/preview`,
        'POST',
        { expected_revision: draft.revision },
      );
      setPreview(result);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function apply() {
    if (!currentPreview || busy || disabled || !checksCurrent) return;
    setBusy(true);
    setError('');
    try {
      const result = await workspaceRequest<Applied>(
        projectId,
        `drafts/${draft.id}/database/apply`,
        'POST',
        {
          expected_revision: draft.revision,
          expected_schema: currentPreview.schema_fingerprint,
          expected_preview_digest: currentPreview.preview_digest,
          confirm: true,
        },
      );
      setApplied(result);
      setPreview(null);
      setConfirmOpen(false);
    } catch (err) {
      setError(
        `${(err as Error).message} Refresh and preview again before retrying.`,
      );
      setPreview(null);
      setConfirmOpen(false);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className={styles.panel} aria-label="Database setup">
      <h2>Database setup</h2>
      <p>
        Edit <code>database/schema.json</code> in the Source tab, then preview
        the SQL on a disposable database copy. Applying changes the live project
        database and stores a backup.
      </p>
      <Button
        type="button"
        variant="secondary"
        disabled={disabled || busy}
        onClick={() => void loadPreview()}
      >
        {busy ? 'Working…' : 'Preview schema'}
      </Button>
      {error && (
        <p role="alert" className={styles.error}>
          {error}
        </p>
      )}
      {applied && (
        <p role="status">
          {applied.status === 'already_applied'
            ? 'Schema already applied.'
            : `Created ${applied.created_tables.join(', ') || 'database objects'}. Backup: ${applied.backup || 'none'}.`}
        </p>
      )}
      {currentPreview && (
        <div className={styles.preview}>
          <p role="status">
            Preview only · no live changes.{' '}
            {currentPreview.pending_tables.length} pending table(s).
          </p>
          {!currentPreview.plan && (
            <p>No schema plan in this draft. Add one in the Source tab.</p>
          )}
          {!!currentPreview.plan && (
            <>
              <h3>Pending tables</h3>
              <p>{currentPreview.pending_tables.join(', ') || 'None'}</p>
              <h3>SQL to review</h3>
              <pre>
                {currentPreview.sql.join('\n\n') || 'No SQL statements.'}
              </pre>
              <details>
                <summary>Schema plan</summary>
                <pre>{JSON.stringify(currentPreview.plan, null, 2)}</pre>
              </details>
              <p>
                {checksCurrent
                  ? 'Validation and tests passed for this revision.'
                  : 'Run validation and tests for this revision before applying.'}
              </p>
              <Button
                type="button"
                disabled={
                  disabled ||
                  busy ||
                  !checksCurrent ||
                  !currentPreview.pending_tables.length
                }
                onClick={() => setConfirmOpen(true)}
              >
                Apply to live database
              </Button>
            </>
          )}
        </div>
      )}
      <AlertDialog
        open={confirmOpen}
        onOpenChange={(next) => {
          if (!busy) setConfirmOpen(next);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Apply database setup?</AlertDialogTitle>
            <AlertDialogDescription>
              This creates{' '}
              {currentPreview?.pending_tables.join(', ') ||
                'the pending tables'}{' '}
              in the live project database. A private pre-change backup is
              saved. This action persists even if this draft is never published.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={busy}>Cancel</AlertDialogCancel>
            <Button
              type="button"
              disabled={busy || !currentPreview}
              onClick={() => void apply()}
            >
              {busy ? 'Applying…' : 'Apply reviewed schema'}
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </section>
  );
}
