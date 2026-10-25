import { useState } from 'react';
import Button from '@/components/ui/Button/Button';
import s from '@/components/workspace/Workspace.module.css';
export function ImportProject({ onImported }: { onImported?: () => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [directory, setDirectory] = useState('');
  const [name, setName] = useState('');
  const [kind, setKind] = useState('import');
  const [clone, setClone] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  async function submit() {
    if (!file || busy) return;
    setBusy(true);
    setError('');
    try {
      if (file.size > 32 * 1024 * 1024)
        throw new Error('Archive exceeds 32 MiB.');
      const query = new URLSearchParams({
        action: kind,
        directory,
        clone: String(clone),
        ...(name ? { name } : {}),
      });
      const reply = await fetch(`/api/transfer?${query}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/zip' },
        body: file,
      });
      const data = await reply.json();
      if (!reply.ok) throw new Error(data.error);
      setNotice('Imported. Open the project from the refreshed project list.');
      setFile(null);
      onImported?.();
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className={s.panel}>
      <h2>Import project / restore workspace</h2>
      <p>
        Archives import into a new, empty directory. Existing projects are
        preserved.
      </p>
      {kind === 'workspace-import' && (
        <p>
          Workspace restore keeps the original project identity. Remove its
          existing registration while keeping its files before restoring to
          another directory.
        </p>
      )}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void submit();
        }}
      >
        <div className={s.grid}>
          <label>
            Archive type
            <select
              value={kind}
              disabled={busy}
              onChange={(e) => setKind(e.target.value)}
            >
              <option value="import">Portable project ZIP</option>
              <option value="workspace-import">Full workspace backup</option>
            </select>
          </label>
          <label>
            ZIP archive
            <input
              type="file"
              accept=".zip"
              disabled={busy}
              onChange={(e) => setFile(e.target.files?.[0] || null)}
            />
          </label>
          <label>
            New destination directory
            <input
              value={directory}
              disabled={busy}
              onChange={(e) => setDirectory(e.target.value)}
              placeholder="/absolute/path/new-project"
              required
            />
          </label>
          {kind === 'import' && (
            <label>
              Project name (optional)
              <input value={name} onChange={(e) => setName(e.target.value)} />
            </label>
          )}
        </div>
        {kind === 'import' && (
          <label className={s.check}>
            <input
              type="checkbox"
              checked={clone}
              onChange={(e) => setClone(e.target.checked)}
            />
            Clone with a new project identity
          </label>
        )}
        <div className={s.row}>
          <Button type="submit" disabled={busy || !file || !directory.trim()}>
            {busy ? 'Importing…' : 'Import archive'}
          </Button>
        </div>
      </form>
      {error && <p role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}
    </section>
  );
}
export default function ProjectTransfer({
  projectId,
  revision,
}: {
  projectId: string;
  revision: string;
}) {
  const [secrets, setSecrets] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  async function backup() {
    setBusy(true);
    setError('');
    try {
      const reply = await fetch(
        `/api/transfer?action=workspace-export&projectId=${projectId}`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            expected_revision: revision,
            confirm: true,
            include_secrets: secrets,
          }),
        },
      );
      if (!reply.ok) throw new Error((await reply.json()).error);
      const url = URL.createObjectURL(await reply.blob());
      const link = document.createElement('a');
      link.href = url;
      link.download = `workspace-${projectId}-${new Date().toISOString().slice(0, 10)}.zip`;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      setConfirm(false);
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className={s.panel}>
      <h2>Project exports</h2>
      <p>
        Portable ZIP contains the active API and data. Workspace backup also
        preserves drafts, versions and local settings. Backup stops the worker.
      </p>
      <div className={s.row}>
        <a href={`/api/transfer?action=export&projectId=${projectId}`}>
          Download active-project ZIP
        </a>
      </div>
      <label className={s.check}>
        <input
          type="checkbox"
          checked={secrets}
          onChange={(e) => setSecrets(e.target.checked)}
        />
        Include project secrets in workspace backup
      </label>
      <label className={s.check}>
        <input
          type="checkbox"
          checked={confirm}
          onChange={(e) => setConfirm(e.target.checked)}
        />
        Confirm workspace backup and worker stop
      </label>
      <div className={s.row}>
        <Button
          disabled={busy || !revision || !confirm}
          onClick={() => void backup()}
        >
          {busy ? 'Preparing…' : 'Download workspace backup'}
        </Button>
      </div>
      {error && <p role="alert">{error}</p>}
    </section>
  );
}
