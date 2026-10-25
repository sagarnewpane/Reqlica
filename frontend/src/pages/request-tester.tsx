import Link from 'next/link';
import { useRouter } from 'next/router';
import DashboardLayout from '@/components/layout/DashboardLayout';
import RequestTester from '@/components/requests/RequestTester';
import PageHeader from '@/components/ui/PageHeader/PageHeader';
import { useHydrated } from '@/hooks/use-hydrated';
import { isProjectId } from '@/lib/project-route';
import styles from './RequestTester.module.css';

export default function RequestTesterPage() {
  const hydrated = useHydrated();
  const router = useRouter();
  const { projectId, method, path, draftId } = router.query;

  return (
    <DashboardLayout title="Request Tester">
      <div className={styles.content}>
        <PageHeader title="Request tester" />
        {!hydrated || !router.isReady ? (
          <p role="status">Loading request tester…</p>
        ) : isProjectId(projectId) ? (
          <RequestTester
            key={`${projectId}:${typeof draftId === 'string' ? draftId : ''}:${typeof method === 'string' ? method : ''}:${typeof path === 'string' ? path : ''}`}
            projectId={projectId}
            initialDraftId={typeof draftId === 'string' ? draftId : undefined}
            initialMethod={typeof method === 'string' ? method : undefined}
            initialPath={typeof path === 'string' ? path : undefined}
          />
        ) : (
          <p>
            <Link href="/">Select a project to test its API →</Link>
          </p>
        )}
      </div>
    </DashboardLayout>
  );
}
