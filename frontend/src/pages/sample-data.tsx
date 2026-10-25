import Link from 'next/link';
import { useRouter } from 'next/router';
import DashboardLayout from '@/components/layout/DashboardLayout';
import SampleDataWorkspace from '@/components/data/SampleDataWorkspace';
import { useHydrated } from '@/hooks/use-hydrated';
import { isProjectId } from '@/lib/project-route';
import styles from '@/components/data/SampleData.module.css';

export default function SampleDataPage() {
  const hydrated = useHydrated();
  const router = useRouter();
  const { projectId } = router.query;
  return (
    <DashboardLayout title="Sample data">
      {!hydrated || !router.isReady ? (
        <p role="status">Loading sample-data workspace…</p>
      ) : isProjectId(projectId) ? (
        <SampleDataWorkspace key={projectId} projectId={projectId} />
      ) : (
        <div className={styles.content}>
          <h1>Select a project</h1>
          <p>Open a project to prepare fixtures or review database inserts.</p>
          <Link href="/">Open projects →</Link>
        </div>
      )}
    </DashboardLayout>
  );
}
