import { useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { CheckCircle2, Rocket, ShieldCheck } from 'lucide-react';
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
import { draftPublicationState, draftLabel, type Draft } from '@/lib/drafts';
import styles from './DraftPublication.module.css';

type PublicationTarget = {
  action: 'approve' | 'activate';
  draft: Draft;
};

type Props = {
  projectId: string;
  draft: Draft;
  disabled: boolean;
  onBusyChange: (busy: boolean) => void;
  onCompleted: (draft: Draft | null, message: string) => void;
  refreshVersion?: number;
};

export default function DraftPublication({
  projectId,
  draft,
  disabled,
  onBusyChange,
  onCompleted,
  refreshVersion,
}: Props) {
  const [project, setProject] = useState<{
    active_version: string | null;
  } | null>(null);
  const [projectError, setProjectError] = useState('');
  const [target, setTarget] = useState<PublicationTarget | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [needsRefresh, setNeedsRefresh] = useState(false);
  const locked = useRef(false);

  useEffect(() => {
    let active = true;
    workspaceRequest<{ active_version: string | null }>(projectId, 'endpoints')
      .then((value) => {
        if (active) setProject(value);
      })
      .catch((err) => {
        if (active) setProjectError((err as Error).message);
      });
    return () => {
      active = false;
    };
  }, [projectId, draft.id, draft.base_version, refreshVersion]);

  const state = draftPublicationState(draft, project?.active_version);
  const blocked = disabled || busy || needsRefresh || !project;
  const staleBase = project && !state.baseIsCurrent;

  async function recover() {
    if (blocked || locked.current) return;
    locked.current = true;
    setBusy(true);
    onBusyChange(true);
    setError('');
    try {
      const recovered = await workspaceRequest<Draft>(projectId,
        `drafts/${draft.id}/recover`, 'POST', { expected_revision: draft.revision });
      onCompleted(recovered, 'Changes recovered onto the current API in a new draft. Original retained. Add behavior tests, then validate and test before publishing.');
    } catch (err) {
      setError((err as Error).message);
      setNeedsRefresh(true);
    } finally {
      locked.current = false;
      setBusy(false);
      onBusyChange(false);
    }
  }

  function open(action: PublicationTarget['action']) {
    if (
      blocked ||
      (action === 'approve' ? !state.canApprove : !state.canActivate)
    )
      return;
    setError('');
    // Keep the confirmation bound to the revision that was actually displayed.
    setTarget({ action, draft });
  }

  async function confirm() {
    if (!target || locked.current || blocked) return;
    if (
      target.draft.id !== draft.id ||
      target.draft.revision !== draft.revision
    ) {
      setError(
        'The selected draft changed. Cancel and refresh drafts before continuing.',
      );
      setNeedsRefresh(true);
      return;
    }
    locked.current = true;
    setBusy(true);
    onBusyChange(true);
    setError('');
    try {
      const revision = target.draft.revision;
      if (target.action === 'approve') {
        const approved = await workspaceRequest<Draft>(
          projectId,
          `drafts/${target.draft.id}/approve`,
          'POST',
          {
            expected_revision: revision,
            confirm: true,
          },
        );
        onCompleted(
          approved,
          'Revision approved. The live API is unchanged. Activate it separately when ready.',
        );
      } else {
        await workspaceRequest(projectId, 'activate', 'POST', {
          draft_id: target.draft.id,
          expected_revision: revision,
          confirm: true,
        });
        onCompleted(
          null,
          `Revision ${revision.slice(0, 12)} activated. The project now uses the new published API.`,
        );
      }
      setTarget(null);
    } catch (err) {
      setError((err as Error).message);
      // A failed response may hide a committed activation. Inspect, never replay.
      setNeedsRefresh(true);
    } finally {
      locked.current = false;
      setBusy(false);
      onBusyChange(false);
    }
  }

  return (
    <section
      className={styles.publication}
      aria-labelledby="publication-heading"
    >
      <header className={styles.heading}>
        <div>
          <h2 id="publication-heading">Review & publish</h2>
          <p>Approval records your review. Activation changes the live API.</p>
        </div>
        {state.approved && (
          <span className={styles.approved}>
            <CheckCircle2 size={16} />
            Revision approved
          </span>
        )}
      </header>
      <ul
        className={styles.requirements}
        aria-label="Publication prerequisites"
      >
        <li>
          <strong>Validation</strong>
          <span>
            {state.validationPassed
              ? 'Passed for this revision'
              : 'Run validation for this revision'}
          </span>
        </li>
        <li>
          <strong>Tests</strong>
          <span>
            {state.testsPassed
              ? 'Passing tests for this revision'
              : 'At least one passing test required'}
          </span>
        </li>
        <li>
          <strong>Base version</strong>
          <span>
            {projectError
              ? 'Could not verify'
              : !project
                ? 'Checking published version…'
                : state.baseIsCurrent
                  ? 'Matches the published version'
                  : 'No longer current'}
          </span>
        </li>
      </ul>
      {projectError && (
        <p role="alert" className={styles.warning}>
          {projectError} Use Refresh above to check again.
        </p>
      )}
      {draft.published_matches && <p role="status">This exact source is already published.</p>}
      {staleBase && !draft.published_matches && (
        <p className={styles.warning}>
          Unpublished changes based on an older API. Recover them onto the current
          version before approval. Conflicting files are reported without overwriting either version.
          {' '}<button type="button" className={styles.secondary} disabled={blocked}
            onClick={() => void recover()}>Recover changes onto current API</button>
        </p>
      )}
      {!!draft.test?.handler_coverage?.missing?.length && (
        <p className={styles.warning}>Tests have not exercised changed handlers: {' '}
          {draft.test.handler_coverage.missing.join(', ')}. Passing unrelated tests does not verify these changes.</p>
      )}
      {needsRefresh && (
        <p role="alert" className={styles.warning}>
          {!target && error ? `${error} ` : ''}Refresh drafts before taking
          another publication action. An unconfirmed operation may still finish.
        </p>
      )}
      <div className={styles.actions}>
        <p>
          Code or environment changes and rerunning checks can invalidate
          approval. The backend verifies prerequisites again before activation.
        </p>
        <div className={styles.buttons}>
          <button
            type="button"
            className={styles.secondary}
            disabled={blocked || !state.canApprove}
            onClick={() => open('approve')}
          >
            <ShieldCheck size={16} />
            Approve revision…
          </button>
          <Button
            type="button"
            disabled={blocked || !state.canActivate}
            onClick={() => open('activate')}
          >
            <Rocket size={16} />
            Activate approved revision…
          </Button>
        </div>
      </div>
      <AlertDialog
        open={target !== null}
        onOpenChange={(next) => {
          if (!next && !locked.current) setTarget(null);
        }}
      >
        <AlertDialogContent
          className={styles.dialog}
          onEscapeKeyDown={(event) => {
            if (locked.current) event.preventDefault();
          }}
        >
          <AlertDialogHeader>
            <AlertDialogTitle>
              {target?.action === 'activate'
                ? 'Activate this approved revision?'
                : 'Approve this checked revision?'}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {target?.action === 'activate'
                ? 'This publishes trusted project Python code and may briefly interrupt requests while the worker switches versions. Startup code can access live data and the runtime machine. Required database setup must already be complete.'
                : 'Confirm that you reviewed this draft’s source and its passing checks. Approval applies only to the revision below and does not change the live API.'}
            </AlertDialogDescription>
          </AlertDialogHeader>
          {target && (
            <dl className={styles.target}>
              <dt>Draft</dt>
              <dd>{draftLabel(target.draft)}</dd>
              <dt>Exact revision</dt>
              <dd>
                <code>{target.draft.revision}</code>
              </dd>
              <dt>Base version</dt>
              <dd>
                <code>{target.draft.base_version}</code>
              </dd>
            </dl>
          )}
          {error && (
            <p role="alert" className={styles.warning}>
              {error} Cancel and use Refresh above before continuing.
            </p>
          )}
          <AlertDialogFooter>
            <AlertDialogCancel disabled={busy}>Cancel</AlertDialogCancel>
            <Button
              type="button"
              disabled={blocked}
              onClick={() => void confirm()}
            >
              {busy
                ? 'Working…'
                : target?.action === 'activate'
                  ? 'Activate revision'
                  : 'Approve revision'}
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
      <Link className={styles.projectLink} href={`/projects/${projectId}`}>
        View project and published endpoints →
      </Link>
    </section>
  );
}
