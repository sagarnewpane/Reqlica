import Link from 'next/link';
import { useRouter } from 'next/router';
import DashboardLayout from '@/components/layout/DashboardLayout';
import RequestLogBrowser from '@/components/logs/RequestLogBrowser';
import { useHydrated } from '@/hooks/use-hydrated';
import { isProjectId } from '@/lib/project-route';
import styles from '@/components/logs/RequestLogs.module.css';

export default function RequestLogsPage() {
  const hydrated = useHydrated();
  const router = useRouter();
  const { projectId } = router.query;
  return (
    <DashboardLayout title="Request logs">
      {!hydrated || !router.isReady ? (
        <p role="status">Loading request history…</p>
      ) : isProjectId(projectId) ? (
        <RequestLogBrowser key={projectId} projectId={projectId} />
      ) : (
        <div className={styles.content}>
          <h1>Select a project</h1>
          <p>Open a project to inspect its saved request history.</p>
          <Link href="/">Open projects →</Link>
        </div>
      )}
    </DashboardLayout>
  );
}
