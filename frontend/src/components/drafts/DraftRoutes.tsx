import { useState } from 'react';
import Link from 'next/link';
import Button from '@/components/ui/Button/Button';
import { workspaceRequest } from '@/lib/chat';
import type { Draft } from '@/lib/drafts';
import s from '@/components/workspace/Workspace.module.css';
export default function DraftRoutes({
  projectId,
  draft,
  disabled,
  onSaved,
}: {
  projectId: string;
  draft: Draft;
  disabled: boolean;
  onSaved: (draft: Draft) => void;
}) {
  const [method, setMethod] = useState('GET');
  const [path, setPath] = useState('');
  const [handler, setHandler] = useState('');
  const [file, setFile] = useState('');
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  async function change(
    kind: 'save' | 'remove' | 'file',
    route?: { method: string; path: string },
  ) {
    if (busy) return;
    setBusy(true);
    setError('');
    try {
      const response = await workspaceRequest<Draft>(
        projectId,
        kind === 'file'
          ? `drafts/${draft.id}/files?path=${encodeURIComponent(file)}`
          : `drafts/${draft.id}/endpoints`,
        kind === 'save' ? 'PUT' : 'DELETE',
        {
          expected_revision: draft.revision,
          ...(kind === 'save'
            ? { endpoint: { method, path, handler } }
            : kind === 'remove'
              ? route
              : {}),
        },
      );
      onSaved(response);
      setConfirm(false);
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className={s.panel}>
      <h2>Route editor</h2>
      <p>
        Save updates an existing method/path or creates a new route. To move a
        route, add its new address and remove the old one.
      </p>
      <ul className={s.list}>
        {draft.endpoints?.map((route) => (
          <li className={s.item} key={`${route.method}:${route.path}`}>
            <strong>
              {route.method} {route.path}
            </strong>
            <p>{route.handler}</p>
            <div className={s.row}>
              <Button
                variant="secondary"
                disabled={disabled || busy}
                onClick={() => {
                  setMethod(route.method);
                  setPath(route.path);
                  setHandler(route.handler);
                }}
              >
                Edit
              </Button>
              <Button
                variant="secondary"
                disabled={disabled || busy || !confirm}
                onClick={() => void change('remove', route)}
              >
                Remove route
              </Button>
              <Link
                href={`/project?projectId=${projectId}&draftId=${draft.id}&source=${encodeURIComponent(route.handler)}&prompt=${encodeURIComponent(`Review and repair ${route.method} ${route.path} in ${route.handler}. Inspect the saved draft and current failures first.`)}`}
              >
                Review in chat
              </Link>
            </div>
          </li>
        ))}
      </ul>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void change('save');
        }}
      >
        <div className={s.grid}>
          <label>
            Method
            <select
              value={method}
              disabled={disabled || busy}
              onChange={(e) => setMethod(e.target.value)}
            >
              {['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS'].map(
                (v) => (
                  <option key={v}>{v}</option>
                ),
              )}
            </select>
          </label>
          <label>
            Path
            <input
              value={path}
              required
              disabled={disabled || busy}
              onChange={(e) => setPath(e.target.value)}
              placeholder="/items/{item_id}"
            />
          </label>
          <label>
            Python handler
            <input
              value={handler}
              required
              disabled={disabled || busy}
              onChange={(e) => setHandler(e.target.value)}
              placeholder="endpoints/items.py:get_item"
            />
          </label>
        </div>
        <Button type="submit" disabled={disabled || busy}>
          Save route
        </Button>
      </form>
      <h3>Delete draft source file</h3>
      <p>
        Changes apply to this unpublished draft. Any affected routes must be
        repaired before validation.
      </p>
      <label>
        Source file
        <select
          value={file}
          onChange={(e) => {
            setFile(e.target.value);
            setConfirm(false);
          }}
          disabled={disabled || busy}
        >
          <option value="">Select file</option>
          {draft.files?.map((p) => (
            <option key={p}>{p}</option>
          ))}
        </select>
      </label>
      <label className={s.check}>
        <input
          type="checkbox"
          checked={confirm}
          onChange={(e) => setConfirm(e.target.checked)}
          disabled={disabled || busy}
        />
        Confirm the selected route or file removal
      </label>
      <div className={s.row}>
        <Button
          variant="secondary"
          disabled={disabled || busy || !file || !confirm}
          onClick={() => void change('file')}
        >
          Delete selected file
        </Button>
      </div>
      {error && <p role="alert">{error}</p>}
    </section>
  );
}
