import { useState } from 'react';
import { useRouter } from 'next/router';
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
  const router = useRouter();
  const passed = preview.validation?.status === 'passed';
  return (
    <section className={s.panel}>
      <h2>Generated sample data</h2>
      {passed && (
        <p>Ready for your review. No live records have been inserted.</p>
      )}
      <p>
        {preview.validation?.message || preview.note} Validation:{' '}
        {preview.validation?.status || 'not verified'}.
      </p>
      <details open>
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
          disabled={!passed}
          onClick={async () => {
            try {
              sessionStorage.setItem(
                `reqlica:data-handoff:${projectId}`,
                JSON.stringify(preview),
              );
              setError('');
              await router.push(
                `/sample-data?projectId=${projectId}&fromChat=1`,
              );
            } catch {
              setError(
                'Could not open Sample data. Copy the preview records or try again.',
              );
            }
          }}
        >
          Review mock data
        </Button>
      </div>
      {error && <p role="alert">{error}</p>}
    </section>
  );
}
