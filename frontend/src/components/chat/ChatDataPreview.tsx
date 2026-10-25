import { useState } from 'react';
import Link from 'next/link';
import Button from '@/components/ui/Button/Button';
import type { DataPreview } from '@/lib/sample-data';
import s from '@/components/workspace/Workspace.module.css';
export default function ChatDataPreview({
  projectId,
  preview,
}: {
  projectId: string;
  preview: DataPreview;
}) {
  const [error, setError] = useState('');
  const [ready, setReady] = useState(false);
  return (
    <section className={s.panel}>
      <h2>Generated sample data</h2>
      <p>
        {preview.validation?.message || preview.note} Validation:{' '}
        {preview.validation?.status || 'not verified'}.
      </p>
      <details>
        <summary>Preview records</summary>
        <pre>
          {JSON.stringify(
            preview.records_by_table || preview.records || [],
            null,
            2,
          )}
        </pre>
      </details>
      <div className={s.row}>
        <Button
          type="button"
          variant="secondary"
          onClick={() => {
            try {
              sessionStorage.setItem(
                `reqlica:data-handoff:${projectId}`,
                JSON.stringify(preview),
              );
              setReady(true);
              setError('');
            } catch {
              setError(
                'Browser storage unavailable. Copy or download the preview records.',
              );
            }
          }}
        >
          Prepare for Sample data
        </Button>
        {ready && (
          <Link href={`/sample-data?projectId=${projectId}&fromChat=1`}>
            Review in Sample data
          </Link>
        )}
      </div>
      {error && <p role="alert">{error}</p>}
    </section>
  );
}
