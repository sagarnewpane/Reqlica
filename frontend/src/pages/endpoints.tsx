import Link from 'next/link';
import { useRouter } from 'next/router';
import DashboardLayout from '@/components/layout/DashboardLayout';
import EndpointBrowser from '@/components/endpoints/EndpointBrowser';
import { useHydrated } from '@/hooks/use-hydrated';
import { isProjectId } from '@/lib/project-route';
import styles from '@/components/endpoints/EndpointBrowser.module.css';

export default function EndpointsPage() {
  const hydrated = useHydrated();
  const router = useRouter();
  const { projectId } = router.query;

  return (
    <DashboardLayout title="Endpoints">
      {!hydrated || !router.isReady ? (
        <p role="status">Loading endpoints…</p>
      ) : isProjectId(projectId) ? (
        <EndpointBrowser key={projectId} projectId={projectId} />
      ) : (
        <div className={styles.content}>
          <h1>Select a project</h1>
          <p>Open a project to browse its published API routes.</p>
          <Link href="/">Open projects →</Link>
        </div>
      )}
    </DashboardLayout>
  );
}
