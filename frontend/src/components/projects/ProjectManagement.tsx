import { useState } from 'react';
import Link from 'next/link';
import Button from '@/components/ui/Button/Button';
import { useResource } from '@/components/workspace/use-resource';
import ProjectTransfer, { ImportProject } from './ProjectTransfer';
import s from '@/components/workspace/Workspace.module.css';
type Maintenance = { directory: string; active_revision: string };
type Version = {
  id: string;
  revision: string;
  active: boolean;
  created_at: string;
};
export default function ProjectManagement({
  projectId,
}: {
  projectId: string;
}) {
  const maintenance = useResource<Maintenance>(projectId, 'maintenance');
  const versions = useResource<{
    versions: Version[];
    active_revision: string;
  }>(projectId, 'versions');
  const [directory, setDirectory] = useState('');
  const [confirm, setConfirm] = useState(false);
  const [notice, setNotice] = useState('');
  const [restored, setRestored] = useState('');
  return (
    <div className={s.stack}>
      <ProjectTransfer
        projectId={projectId}
        revision={maintenance.data?.active_revision || ''}
      />
      <section className={s.panel}>
        <h2>Published version history</h2>
        <p>
          Restore creates an unpublished draft from historical code. Database
          data stays current; validate the restored draft before publishing.
        </p>
        <Button variant="secondary" onClick={versions.refresh}>
          Refresh history
        </Button>
        <ul className={s.list}>
          {versions.data?.versions.map((version) => (
            <li key={version.id} className={s.item}>
              <strong>
                {version.active ? 'Active · ' : ''}
                {version.id.slice(0, 8)}
              </strong>{' '}
              · {new Date(version.created_at).toLocaleString()}
              <p>Revision {version.revision.slice(0, 12)}</p>
              <Button
                disabled={versions.busy}
                variant="secondary"
                onClick={() => {
                  void versions
                    .action('versions/restore', 'POST', {
                      version_id: version.id,
                      expected_revision: versions.data!.active_revision,
                      expected_version_revision: version.revision,
                    })
                    .then((result) => {
                      if (result)
                        setRestored((result as unknown as { id: string }).id);
                    })
                    .catch(() => {});
                }}
              >
                Restore into new draft
              </Button>
            </li>
          ))}
        </ul>
        {restored && (
          <Link
            href={`/draft-review?projectId=${projectId}&draftId=${restored}`}
          >
            Review restored draft
          </Link>
        )}
        {versions.error && <p role="alert">{versions.error}</p>}
      </section>
      <section className={s.panel}>
        <h2>Project location and storage</h2>
        <p>
          Current directory: {maintenance.data?.directory || 'loading'}.
          Relocation moves project files and stops the worker. Conversion
          enables versioned drafts for legacy projects.
        </p>
        <label>
          New absolute directory
          <input
            value={directory}
            onChange={(e) => setDirectory(e.target.value)}
            placeholder="/absolute/path/new-location"
          />
        </label>
        <label className={s.check}>
          <input
            type="checkbox"
            checked={confirm}
            onChange={(e) => setConfirm(e.target.checked)}
          />
          Confirm the selected maintenance action and worker stop
        </label>
        <div className={s.row}>
          <Button
            disabled={
              !maintenance.data ||
              maintenance.busy ||
              !directory.trim() ||
              !confirm
            }
            onClick={() => {
              void maintenance
                .action('relocate', 'POST', {
                  expected_revision: maintenance.data!.active_revision,
                  expected_directory: maintenance.data!.directory,
                  directory,
                  confirm: true,
                })
                .then(() => {
                  setConfirm(false);
                  setNotice('Project moved. Refresh other open views.');
                })
                .catch(() => setConfirm(false));
            }}
          >
            Move project
          </Button>
          <Button
            variant="secondary"
            disabled={!confirm || maintenance.busy}
            onClick={() => {
              void maintenance
                .action('convert', 'POST', {})
                .then(() => {
                  setConfirm(false);
                  setNotice('Project uses versioned storage.');
                  versions.refresh();
                })
                .catch(() => setConfirm(false));
            }}
          >
            Convert legacy project
          </Button>
          <Button variant="secondary" onClick={maintenance.refresh}>
            Refresh
          </Button>
        </div>
        {maintenance.error && <p role="alert">{maintenance.error}</p>}
        {notice && <p role="status">{notice}</p>}
      </section>
      <ImportProject />
    </div>
  );
}
