import { useEffect, useState } from 'react';
import * as Tabs from '@radix-ui/react-tabs';
import Link from 'next/link';
import PageHeader from '@/components/ui/PageHeader/PageHeader';
import {
  Plus,
  Pencil,
  Trash2,
  RefreshCw,
  FileCheck2,
  Play,
} from 'lucide-react';
import Card from '@/components/ui/Card/Card';
import Button from '@/components/ui/Button/Button';
import Input from '@/components/ui/Input/Input';
import Select from '@/components/ui/Select/Select';
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
import {
  draftLabel,
  draftCreatedLabel,
  orderedDrafts,
  type Draft,
} from '@/lib/drafts';
import DraftPublication from './DraftPublication';
import DraftSourceEditor from './DraftSourceEditor';
import ContractViewer from '@/components/endpoints/ContractViewer';
import DraftRoutes from './DraftRoutes';
import DatabaseSetup from './DatabaseSetup';
import type { DraftEditorState } from '@/lib/draft-files';
import { useUnsavedDraftNavigation } from '@/hooks/use-unsaved-draft-navigation';
import styles from './DraftReview.module.css';
type Action = 'create' | 'rename' | 'delete' | 'validate' | 'test';
export default function DraftWorkspace({
  projectId,
  initialDraftId,
}: {
  projectId: string;
  initialDraftId?: string;
}) {
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [selected, setSelected] = useState(initialDraftId || '');
  const [detail, setDetail] = useState<Draft | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [attempt, setAttempt] = useState(0);
  const [action, setAction] = useState<Action | null>(null);
  const [name, setName] = useState('');
  const [result, setResult] = useState<unknown>(null);
  const [dialogError, setDialogError] = useState('');
  const [view, setView] = useState('changes');
  const [sourcePath, setSourcePath] = useState('');
  const [editor, setEditor] = useState<DraftEditorState>({
    dirty: false,
    busy: false,
    blocked: false,
  });
  const editorBlocked = editor.dirty || editor.busy || editor.blocked;
  useUnsavedDraftNavigation(editor.dirty);
  useEffect(() => {
    let active = true;
    Promise.resolve().then(async () => {
      if (!active) return;
      setLoading(true);
      try {
        const data = await workspaceRequest<{ drafts: Draft[] }>(
          projectId,
          'drafts',
        );
        if (active) {
          const ordered = orderedDrafts(data.drafts);
          setDrafts(ordered);
          setSelected((current) =>
            ordered.some((item) => item.id === current)
              ? current
              : ordered[0]?.id || '',
          );
          setError('');
        }
      } catch (err) {
        if (active) setError((err as Error).message);
      } finally {
        if (active) setLoading(false);
      }
    });
    return () => {
      active = false;
    };
  }, [projectId, attempt]);
  useEffect(() => {
    let active = true;
    Promise.resolve().then(async () => {
      if (!active) return;
      if (!selected) {
        setDetail(null);
        return;
      }
      try {
        const data = await workspaceRequest<Draft>(
          projectId,
          `drafts/${selected}`,
        );
        if (active) {
          setDetail(data);
          setError('');
        }
      } catch (err) {
        if (active) setError((err as Error).message);
      }
    });
    return () => {
      active = false;
    };
  }, [projectId, selected, attempt]);
  function open(next: Action) {
    if (busy || editorBlocked) return;
    setAction(next);
    setDialogError('');
    setName(next === 'rename' ? detail?.name || '' : '');
  }
  async function perform() {
    if (!action || busy || editorBlocked || (action !== 'create' && !detail))
      return;
    setBusy(true);
    setDialogError('');
    try {
      if (action === 'create') {
        const created = await workspaceRequest<Draft>(
          projectId,
          'drafts',
          'POST',
          name.trim() ? { name: name.trim() } : {},
        );
        setDrafts((current) => [created, ...current]);
        setSelected(created.id);
        setDetail(created);
        setNotice('Draft created from the published API.');
      } else if (detail) {
        const path = `drafts/${detail.id}`;
        const revision = { expected_revision: detail.revision };
        if (action === 'rename') {
          const renamed = await workspaceRequest<Draft>(
            projectId,
            path,
            'PATCH',
            { ...revision, name: name.trim() },
          );
          setDetail(renamed);
          setDrafts((current) =>
            current.map((item) => (item.id === renamed.id ? renamed : item)),
          );
          setNotice('Draft renamed.');
        } else if (action === 'delete') {
          await workspaceRequest(projectId, path, 'DELETE', {
            ...revision,
            confirm: true,
          });
          setDetail(null);
          setSelected('');
          setResult(null);
          setAttempt((count) => count + 1);
          setNotice('Draft deleted. Published API unchanged.');
        } else {
          const output = await workspaceRequest(
            projectId,
            `${path}/${action}`,
            'POST',
            revision,
          );
          setResult(output);
          setAttempt((count) => count + 1);
          setNotice(
            `${action === 'validate' ? 'Validation' : 'Tests'} finished. Review the results below.`,
          );
        }
      }
      setAction(null);
    } catch (err) {
      setDialogError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const activeDraft = drafts.find((item) => item.id === selected);

  function publicationCompleted(updated: Draft | null, message: string) {
    setNotice(message);
    if (updated) {
      setDetail(updated);
      setSelected(updated.id);
      setDrafts((current) => [
        updated,
        ...current.filter((item) => item.id !== updated.id),
      ]);
    } else {
      setAttempt((count) => count + 1);
    }
  }

  return (
    <div className={styles.content}>
      <PageHeader
        title="Drafts"
        actions={
          <>
            <Button
              variant="secondary"
              disabled={busy || loading || editor.dirty || editor.busy}
              onClick={() => {
                setAttempt((count) => count + 1);
              }}
            >
              <RefreshCw size={15} />
              Refresh
            </Button>
            <Button
              disabled={busy || editorBlocked}
              onClick={() => open('create')}
            >
              <Plus size={16} />
              New draft
            </Button>
          </>
        }
      />
      {error && (
        <p role="alert" className={styles.error}>
          {error}
        </p>
      )}
      {notice && (
        <p role="status" className={styles.notice}>
          {notice}
        </p>
      )}
      {editor.dirty && (
        <p role="status" className={styles.notice}>
          Unsaved source edits: save or discard local changes before switching
          drafts, refreshing, running checks, or publishing.
        </p>
      )}
      <Card>
        <label htmlFor="draft-select" className={styles.fieldLabel}>
          Saved drafts
        </label>
        <Select
          id="draft-select"
          value={selected}
          disabled={
            busy || loading || editor.dirty || editor.busy || !drafts.length
          }
          onValueChange={(next) => {
            setSelected(next);
            setDetail(null);
            setResult(null);
          }}
        >
          <option value="" disabled>
            {loading ? 'Loading drafts…' : 'Select a draft'}
          </option>
          {orderedDrafts(drafts).map((item) => (
            <option key={item.id} value={item.id}>
              {draftLabel(item)}
              {item.error ? ' · Unreadable' : ''}
            </option>
          ))}
        </Select>
        {!loading && !drafts.length && (
          <div className={styles.empty}>
            <h2>No drafts yet</h2>
            <p>
              Create a draft here or ask the project assistant to prepare a
              change.
            </p>
            <Link href={`/projects/${projectId}`}>Open project chat →</Link>
          </div>
        )}
        {selected && !detail && !error && (
          <p role="status">Loading draft details…</p>
        )}
        {activeDraft?.error && (
          <p className={styles.error}>{activeDraft.error}</p>
        )}
        {detail && (
          <>
            <div className={styles.draftHeader}>
              <div>
                <h2>{detail.name}</h2>
                <p className={styles.meta}>
                  Draft {detail.id.slice(0, 8)} · Revision{' '}
                  {detail.revision.slice(0, 12)}
                  {' · '}
                  <Link
                    href={{
                      pathname: '/draft-review',
                      query: { projectId, draftId: detail.id },
                    }}
                  >
                    Open this draft
                  </Link>
                </p>
                {detail.created_at && (
                  <p className={styles.meta}>
                    Created {draftCreatedLabel(detail)} · Local time
                  </p>
                )}
                <p className={styles.meta}>
                  {detail.published_matches
                    ? 'Already published'
                    : detail.base_is_current === false
                      ? 'Unpublished · Older base — recover in Review & publish'
                      : 'Unpublished · Not visible in published endpoints yet'}
                </p>
              </div>
              <div className={styles.actionButtons}>
                <button
                  className={styles.secondary}
                  disabled={busy || editorBlocked}
                  onClick={() => open('rename')}
                >
                  <Pencil size={14} />
                  Rename
                </button>
                <button
                  className={styles.secondary}
                  disabled={busy || editorBlocked}
                  onClick={() => open('delete')}
                >
                  <Trash2 size={14} />
                  Delete draft
                </button>
              </div>
            </div>
            <Tabs.Root value={view} onValueChange={setView} key={detail.id}>
              <Tabs.List className={styles.tabs} aria-label="Draft views">
                <Tabs.Trigger value="changes">Changes & checks</Tabs.Trigger>
                <Tabs.Trigger value="source">
                  Source files{editor.dirty ? ' •' : ''}
                </Tabs.Trigger>
                <Tabs.Trigger value="routes">Route editor</Tabs.Trigger>
                <Tabs.Trigger value="contract">API contract</Tabs.Trigger>
                <Tabs.Trigger value="database">Database setup</Tabs.Trigger>
                <Tabs.Trigger value="publish">Review & publish</Tabs.Trigger>
              </Tabs.List>
              <Tabs.Content value="changes" className={styles.tabPanel}>
                <div className={styles.checks}>
                  <div>
                    <strong>Validation</strong>
                    <p>
                      {detail.validation?.status || 'Not run'}
                      {detail.validation &&
                      detail.validation.revision !== detail.revision
                        ? ' · Outdated'
                        : ''}
                    </p>
                  </div>
                  <div>
                    <strong>Tests</strong>
                    <p>
                      {detail.test?.status || 'Not run'}
                      {detail.test && detail.test.revision !== detail.revision
                        ? ' · Outdated'
                        : ''}
                      {detail.test?.tests_run !== undefined
                        ? ` · ${detail.test.tests_passed || 0}/${detail.test.tests_run} passed`
                        : ''}
                    </p>
                  </div>
                </div>
                <div className={styles.actionButtons}>
                  <Link
                    href={{
                      pathname: '/request-tester',
                      query: { projectId, draftId: detail.id },
                    }}
                  >
                    Try draft requests →
                  </Link>
                  <button
                    className={styles.secondary}
                    disabled={busy || editorBlocked}
                    onClick={() => open('validate')}
                  >
                    <FileCheck2 size={15} />
                    Validate draft
                  </button>
                  <button
                    className={styles.secondary}
                    disabled={busy || editorBlocked}
                    onClick={() => open('test')}
                  >
                    <Play size={15} />
                    Run tests
                  </button>
                </div>
                <div className={styles.section}>
                  <h2>Draft endpoints</h2>
                  <p>
                    These routes belong to this draft. Approval and activation
                    publish them.
                  </p>
                  <ul>
                    {detail.endpoints?.map((route) => (
                      <li key={`${route.method}:${route.path}`}>
                        <code>
                          {route.method} {route.path}
                        </code>
                        {' · '}
                        <button
                          type="button"
                          className={styles.secondary}
                          disabled={busy || editorBlocked}
                          onClick={() => {
                            setSourcePath(route.handler.split(':')[0]);
                            setView('source');
                          }}
                        >
                          {route.handler}
                        </button>
                      </li>
                    ))}
                  </ul>
                  {!!detail.test?.handler_coverage?.missing?.length && (
                    <p role="status" className={styles.error}>
                      Changed handlers not exercised by saved tests:{' '}
                      {detail.test.handler_coverage.missing.join(', ')}. Add
                      behavior tests and rerun.
                    </p>
                  )}
                  <h2>Changed files</h2>
                  {detail.changes &&
                  Object.values(detail.changes).every(
                    (items) => !items.length,
                  ) ? (
                    <p>No file changes from the draft’s base version.</p>
                  ) : (
                    Object.entries(detail.changes || {}).map(
                      ([kind, files]) =>
                        files.length > 0 && (
                          <div key={kind}>
                            <h3 className={styles.changeKind}>
                              {kind} · {files.length}
                            </h3>
                            <ul>
                              {files.map((file) => (
                                <li key={file}>
                                  <button
                                    type="button"
                                    className={styles.secondary}
                                    disabled={
                                      busy ||
                                      editorBlocked ||
                                      kind === 'deleted'
                                    }
                                    onClick={() => {
                                      setSourcePath(file);
                                      setView('source');
                                    }}
                                  >
                                    <code>{file}</code>
                                  </button>
                                </li>
                              ))}
                            </ul>
                          </div>
                        ),
                    )
                  )}
                </div>
              </Tabs.Content>
              <Tabs.Content
                value="source"
                forceMount
                className={styles.tabPanel}
              >
                <DraftSourceEditor
                  key={`${detail.id}:${sourcePath}`}
                  initialPath={
                    sourcePath && detail.files?.includes(sourcePath)
                      ? sourcePath
                      : undefined
                  }
                  projectId={projectId}
                  draft={detail}
                  disabled={busy || loading}
                  onStateChange={setEditor}
                  onSaved={(updated) => {
                    setResult(null);
                    publicationCompleted(
                      updated,
                      'Draft source saved. Rerun validation and tests before approval.',
                    );
                  }}
                />
              </Tabs.Content>
              <Tabs.Content
                value="publish"
                forceMount
                className={styles.tabPanel}
              >
                <DraftPublication
                  key={detail.id}
                  projectId={projectId}
                  draft={detail}
                  refreshVersion={attempt}
                  disabled={busy || loading || editorBlocked}
                  onBusyChange={setBusy}
                  onCompleted={publicationCompleted}
                />
              </Tabs.Content>
              <Tabs.Content value="routes" className={styles.tabPanel}>
                <DraftRoutes
                  projectId={projectId}
                  draft={detail}
                  disabled={busy || loading || editorBlocked}
                  onSaved={(updated) => {
                    setResult(null);
                    publicationCompleted(
                      updated,
                      'Draft updated. Rerun validation and tests.',
                    );
                  }}
                />
              </Tabs.Content>
              <Tabs.Content value="contract" className={styles.tabPanel}>
                <ContractViewer
                  projectId={projectId}
                  draftId={detail.id}
                  revision={detail.revision}
                />
              </Tabs.Content>
              <Tabs.Content value="database" className={styles.tabPanel}>
                <DatabaseSetup
                  projectId={projectId}
                  draft={detail}
                  disabled={busy || loading || editorBlocked}
                />
              </Tabs.Content>
            </Tabs.Root>
          </>
        )}
      </Card>
      {result !== null && (
        <Card className={styles.result}>
          <h2>Check result</h2>
          {detail && (
            <Link
              href={`/projects/${projectId}?draftId=${detail.id}&prompt=${encodeURIComponent('Repair the current draft using its saved validation and test failures. Inspect the draft and failures first; fix the cause, then rerun checks when execution is enabled.')}`}
            >
              Open this draft and failures in chat
            </Link>
          )}
          <pre>{JSON.stringify(result, null, 2)}</pre>
        </Card>
      )}
      <AlertDialog
        open={!!action}
        onOpenChange={(next) => {
          if (!busy && !next) setAction(null);
        }}
      >
        <AlertDialogContent
          onEscapeKeyDown={(event) => {
            if (busy) event.preventDefault();
          }}
        >
          <AlertDialogHeader>
            <AlertDialogTitle>
              {action === 'create'
                ? 'Create a draft'
                : action === 'rename'
                  ? 'Rename draft'
                  : action === 'delete'
                    ? `Delete ${detail?.name}?`
                    : action === 'validate'
                      ? 'Validate this draft?'
                      : 'Run draft tests?'}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {action === 'create'
                ? 'Copy the published API into a new unpublished draft.'
                : action === 'rename'
                  ? 'Choose a descriptive name. Source and saved checks are preserved.'
                  : action === 'delete'
                    ? 'Permanently deletes this draft’s source and checks. Published versions are separate and remain unchanged.'
                    : 'Executes project Python code on disposable data with the runtime machine’s permissions. Review and trust the draft source before continuing.'}
            </AlertDialogDescription>
          </AlertDialogHeader>
          {(action === 'create' || action === 'rename') && (
            <Input
              aria-label="Draft name"
              placeholder="Draft name"
              value={name}
              maxLength={100}
              disabled={busy}
              onChange={(event) => setName(event.target.value)}
            />
          )}
          {dialogError && (
            <p role="alert" className={styles.error}>
              {dialogError} Refresh drafts if their revision changed.
            </p>
          )}
          <AlertDialogFooter>
            <AlertDialogCancel disabled={busy}>Cancel</AlertDialogCancel>
            <Button
              variant={action === 'delete' ? 'danger' : 'primary'}
              disabled={busy || (action === 'rename' && !name.trim())}
              onClick={() => void perform()}
            >
              {busy
                ? 'Working…'
                : action === 'create'
                  ? 'Create draft'
                  : action === 'rename'
                    ? 'Save name'
                    : action === 'delete'
                      ? 'Delete draft'
                      : 'Run check'}
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
