import { useEffect, useState } from 'react';
import { LoaderCircle } from 'lucide-react';
import type { GenerationActivity } from '@/lib/chat';
import { activityLabel, formatElapsed } from '@/lib/chat-observability';
import styles from './ChatInsights.module.css';

export default function GenerationStatus({ activity, pausing }: {
  activity: GenerationActivity | null;
  pausing: boolean;
}) {
  const [elapsed, setElapsed] = useState(0);
  const startedAt = activity?.startedAt;
  useEffect(() => {
    if (!startedAt) return;
    const timer = setInterval(() => setElapsed(Math.max(0, Math.floor((Date.now() - startedAt) / 1000))), 1000);
    return () => clearInterval(timer);
  }, [startedAt]);

  return (
    <div className={styles.progress} aria-label="Generation progress">
      <div className={styles.progressRow}>
        <LoaderCircle size={16} className={styles.spinner} aria-hidden="true" />
        <span role="status" className={styles.phase} title={activityLabel(activity, pausing)}>{activityLabel(activity, pausing)}</span>
        <span className={styles.elapsed} aria-label={`Elapsed ${elapsed} seconds`}>{formatElapsed(elapsed)}</span>
      </div>
      {!pausing && elapsed >= 20 && activity?.phase === 'model' && (
        <p>Still waiting for the provider. Pause takes effect after this step.</p>
      )}
    </div>
  );
}
