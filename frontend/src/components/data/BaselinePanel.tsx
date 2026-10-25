import { useState } from 'react';
import Button from '@/components/ui/Button/Button';
import { useResource } from '@/components/workspace/use-resource';
import s from '@/components/workspace/Workspace.module.css';
type Baseline = {
  revision: string;
  current_schema_fingerprint: string;
  baseline: { created_at: string; schema_fingerprint: string } | null;
};
export default function BaselinePanel({ projectId }: { projectId: string }) {
  const resource = useResource<Baseline>(projectId, 'data-baseline');
  const [confirm, setConfirm] = useState(false);
  const [notice, setNotice] = useState('');
  async function act(restore: boolean) {
    if (!resource.data) return;
    try {
      await resource.action(
        restore ? 'data-reset' : 'data-baseline',
        restore ? 'POST' : 'PUT',
        {
          expected_revision: resource.data.revision,
          confirm: true,
          ...(restore
            ? { expected_schema: resource.data.current_schema_fingerprint }
            : {}),
        },
      );
      setConfirm(false);
      setNotice(
        restore
          ? 'Data restored. Worker stopped; restart it when ready.'
          : 'Baseline captured.',
      );
    } catch {
      setConfirm(false);
    }
  }
  return (
    <section className={s.panel}>
      <h2>Data baseline</h2>
      <p>
        {resource.data?.baseline
          ? `Captured ${new Date(resource.data.baseline.created_at).toLocaleString()}`
          : 'No captured baseline.'}{' '}
        Restoring replaces SQLite data; the server preserves a safety backup.
      </p>
      <label className={s.check}>
        <input
          type="checkbox"
          checked={confirm}
          onChange={(e) => setConfirm(e.target.checked)}
        />
        Confirm capture or replacement of this project’s database data
      </label>
      <div className={s.row}>
        <Button
          disabled={!resource.data || resource.busy || !confirm}
          onClick={() => void act(false)}
        >
          Capture baseline
        </Button>
        <Button
          variant="secondary"
          disabled={!resource.data?.baseline || resource.busy || !confirm}
          onClick={() => void act(true)}
        >
          Restore baseline
        </Button>
        <Button variant="secondary" onClick={resource.refresh}>
          Refresh
        </Button>
      </div>
      {resource.error && <p role="alert">{resource.error}</p>}
      {notice && <p role="status">{notice}</p>}
    </section>
  );
}
