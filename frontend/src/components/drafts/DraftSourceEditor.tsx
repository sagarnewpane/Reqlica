import { useEffect, useRef, useState } from 'react';
import { Download, FilePlus2, RefreshCw, Save } from 'lucide-react';
import Button from '@/components/ui/Button/Button';
import Input from '@/components/ui/Input/Input';
import Textarea from '@/components/ui/Textarea/Textarea';
import { workspaceRequest } from '@/lib/chat';
import type { Draft } from '@/lib/drafts';
import {
  draftFilePathError,
  draftFileRoute,
  MAX_DRAFT_SOURCE_BYTES,
  type DraftFile,
  type DraftEditorState,
} from '@/lib/draft-files';
import styles from './DraftSourceEditor.module.css';

export default function DraftSourceEditor({
  projectId,
  draft,
  disabled,
  onSaved,
  onStateChange,
  initialPath,
}: {
  projectId: string;
  draft: Draft;
  disabled: boolean;
  onSaved: (draft: Draft) => void;
  onStateChange: (state: DraftEditorState) => void;
  initialPath?: string;
}) {
  const draftId = draft.id;
  const [firstPath] = useState(() => initialPath || draft.changes?.added?.[0] || draft.changes?.modified?.[0] || draft.files?.[0] || '');
  const [loaded, setLoaded] = useState<DraftFile | null>(null);
  const [path, setPath] = useState(firstPath);
  const [text, setText] = useState('');
  const [newFile, setNewFile] = useState(false);
  const [query, setQuery] = useState('');
  const [loading, setLoading] = useState(!!firstPath);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [needsRefresh, setNeedsRefresh] = useState(false);
  const locked = useRef(!!firstPath);
  const alive = useRef(true);
  const initialized = useRef(false);
  const dirty = loaded
    ? text !== loaded.content
    : newFile && (!!path || !!text);
  const stale = !!loaded && loaded.revision !== draft.revision;

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, [projectId, draftId]);
  useEffect(() => {
    onStateChange({
      dirty,
      busy: loading || saving,
      blocked: needsRefresh || stale,
    });
  }, [dirty, loading, saving, needsRefresh, stale, onStateChange]);
  useEffect(
    () => () => onStateChange({ dirty: false, busy: false, blocked: false }),
    [onStateChange],
  );

  useEffect(() => {
    if (!firstPath || initialized.current) return;
    let active = true;
    Promise.resolve()
      .then(() =>
        workspaceRequest<DraftFile>(
          projectId,
          draftFileRoute(draftId, firstPath),
        ),
      )
      .then((file) => {
        if (!active) return;
        if (
          file.path !== firstPath ||
          typeof file.content !== 'string' ||
          !/^[a-f0-9]{64}$/.test(file.revision)
        )
          throw new Error(
            'The source response could not be verified. Refresh the draft.',
          );
        initialized.current = true;
        setLoaded(file);
        setPath(file.path);
        setText(file.content);
      })
      .catch((err) => {
        if (active) setError((err as Error).message);
      })
      .finally(() => {
        if (active) {
          locked.current = false;
          setLoading(false);
        }
      });
    return () => {
      active = false;
    };
  }, [projectId, draftId, firstPath]);

  function canDiscard() {
    return (
      !dirty ||
      window.confirm(
        'Discard unsaved source changes in this editor? Download or copy the local text first if you need to keep it.',
      )
    );
  }

  async function loadFile(next: string) {
    if (disabled || locked.current || !canDiscard()) return;
    locked.current = true;
    setLoading(true);
    setError('');
    setNotice('');
    try {
      const file = await workspaceRequest<DraftFile>(
        projectId,
        draftFileRoute(draftId, next),
      );
      if (!alive.current) return;
      if (
        file.path !== next ||
        typeof file.content !== 'string' ||
        !/^[a-f0-9]{64}$/.test(file.revision)
      )
        throw new Error(
          'The source response could not be verified. Refresh the draft.',
        );
      setLoaded(file);
      setPath(file.path);
      setText(file.content);
      setNewFile(false);
    } catch (err) {
      if (alive.current) setError((err as Error).message);
    } finally {
      locked.current = false;
      if (alive.current) setLoading(false);
    }
  }

  function createFile() {
    if (disabled || locked.current || needsRefresh || !canDiscard()) return;
    setLoaded(null);
    setPath('');
    setText('');
    setNewFile(true);
    setError('');
    setNotice('');
  }

  function discard() {
    if (locked.current || !canDiscard()) return;
    setText(loaded?.content || '');
    if (newFile) setPath('');
    setNotice('Local changes discarded. The saved draft was not modified.');
  }

  async function save() {
    if (
      disabled ||
      locked.current ||
      !dirty ||
      needsRefresh ||
      stale ||
      (!loaded && !newFile)
    )
      return;
    const invalid = draftFilePathError(path);
    if (invalid) {
      setError(invalid);
      return;
    }
    if (newFile && draft.files?.includes(path)) {
      setError(
        'Load this existing file before overwriting it. Local text has been kept.',
      );
      return;
    }
    if (new TextEncoder().encode(text).length > MAX_DRAFT_SOURCE_BYTES) {
      setError(
        'This editor supports UTF-8 source up to 1 MiB. Local text has been kept.',
      );
      return;
    }
    const expectedRevision = loaded?.revision || draft.revision;
    locked.current = true;
    setSaving(true);
    setError('');
    setNotice('');
    try {
      const updated = await workspaceRequest<Draft>(
        projectId,
        draftFileRoute(draftId, path),
        'PUT',
        { content: text, expected_revision: expectedRevision },
      );
      if (!alive.current) return;
      if (
        updated.id !== draftId ||
        !/^[a-f0-9]{64}$/.test(updated.revision) ||
        !updated.files?.includes(path)
      )
        throw new Error(
          'The saved file response was not verified. Keep local text and refresh the saved draft.',
        );
      setLoaded({ path, content: text, revision: updated.revision });
      setNewFile(false);
      setNotice(
        'Source saved to this unpublished draft. Validation, tests, and approval must be renewed.',
      );
      onSaved(updated);
    } catch (err) {
      if (alive.current) {
        setError((err as Error).message);
        setNeedsRefresh(true);
      }
    } finally {
      locked.current = false;
      if (alive.current) setSaving(false);
    }
  }

  function download() {
    const url = URL.createObjectURL(
      new Blob([text], { type: 'text/plain;charset=utf-8' }),
    );
    const link = document.createElement('a');
    link.href = url;
    link.download = path.split('/').pop() || 'draft-source.txt';
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 0);
  }

  const files = (draft.files || []).filter((file) =>
    file.toLowerCase().includes(query.trim().toLowerCase()),
  );
  const busy = disabled || loading || saving;
  return (
    <section className={styles.section} aria-labelledby="source-heading">
      <header className={styles.heading}>
        <div>
          <h2 id="source-heading">Draft source files</h2>
          <p>Unpublished code. Register new routes in manifest.json.</p>
        </div>
        <button
          type="button"
          className={styles.secondary}
          disabled={busy || needsRefresh}
          onClick={createFile}
        >
          <FilePlus2 size={16} />
          New source file
        </button>
      </header>
      <p className={styles.warning}>
        Save only trusted code. Later validation, tests, and activation can run
        Python with the runtime machine’s permissions. File edits clear saved
        checks and approval.
      </p>
      <div className={styles.workspace}>
        <aside className={styles.files} aria-label="Draft source files">
          <label htmlFor="source-search">Find a file</label>
          <Input
            id="source-search"
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Filter file paths…"
          />
          <p>{draft.files?.length || 0} saved files</p>
          <ul>
            {files.map((file) => (
              <li key={file}>
                <button
                  type="button"
                  disabled={busy}
                  aria-pressed={!newFile && loaded?.path === file}
                  className={styles.file}
                  onClick={() => void loadFile(file)}
                >
                  <code>{file}</code>
                </button>
              </li>
            ))}
          </ul>
          {!files.length && (
            <p>
              {draft.files?.length
                ? 'No matching files.'
                : 'No saved source files.'}
            </p>
          )}
        </aside>
        <div className={styles.editor}>
          <div className={styles.field}>
            <label htmlFor="source-path">Project-relative file path</label>
            <Input
              id="source-path"
              value={path}
              readOnly={!newFile}
              disabled={busy}
              onChange={(event) => setPath(event.target.value)}
              placeholder="endpoints/new_route.py"
            />
          </div>
          <p className={styles.meta}>
            {dirty
              ? 'Unsaved local changes'
              : loaded
                ? 'Saved source · unpublished'
                : newFile
                  ? 'New local file · not yet saved'
                  : 'Select a source file'}
            {loaded && (
              <>
                {' '}
                · Loaded revision <code>{loaded.revision.slice(0, 12)}</code>
              </>
            )}
          </p>
          {loading && <p role="status">Loading draft source…</p>}
          {saving && <p role="status">Saving draft source…</p>}
          {error && (
            <p role="alert" className={styles.warning}>
              {error}
            </p>
          )}
          {(needsRefresh || stale) && (
            <p role="alert" className={styles.warning}>
              Do not retry this save against an unverified revision. Keep or
              download local text, then discard local edits and use Refresh at
              the top of the draft workspace to inspect saved state.
            </p>
          )}
          {notice && (
            <p role="status" className={styles.notice}>
              {notice}
            </p>
          )}
          <label htmlFor="source-content">Source code</label>
          <Textarea
            id="source-content"
            rows={22}
            value={text}
            spellCheck={false}
            disabled={busy || (!loaded && !newFile)}
            onChange={(event) => {
              setText(event.target.value);
              setNotice('');
            }}
          />
          <div className={styles.actions}>
            <div className={styles.buttons}>
              <button
                type="button"
                className={styles.secondary}
                disabled={busy || (!loaded && !newFile)}
                onClick={download}
              >
                <Download size={16} />
                Download local source
              </button>
              <button
                type="button"
                className={styles.secondary}
                disabled={busy || !loaded}
                onClick={() => void loadFile(loaded!.path)}
              >
                <RefreshCw size={16} />
                Reload file
              </button>
              <button
                type="button"
                className={styles.secondary}
                disabled={busy || !dirty}
                onClick={discard}
              >
                Discard local edits
              </button>
            </div>
            <Button
              type="button"
              disabled={busy || !dirty || needsRefresh || stale}
              onClick={() => void save()}
            >
              <Save size={16} />
              Save file to draft
            </Button>
          </div>
        </div>
      </div>
    </section>
  );
}
