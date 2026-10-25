import Link from 'next/link';
import { useRouter } from 'next/router';
import DashboardLayout from '@/components/layout/DashboardLayout';
import DraftWorkspace from '@/components/drafts/DraftWorkspace';
import { useHydrated } from '@/hooks/use-hydrated';
import { isProjectId } from '@/lib/project-route';
import styles from '@/components/drafts/DraftReview.module.css';

export default function DraftReview() {
  const hydrated = useHydrated();
  const router = useRouter();
  const { projectId, draftId } = router.query;

  return (
    <DashboardLayout title="Draft workspace">
      {!hydrated || !router.isReady ? (
        <p>Loading drafts…</p>
      ) : isProjectId(projectId) ? (
        <DraftWorkspace
          key={`${projectId}:${draftId || ''}`}
          projectId={projectId}
          initialDraftId={typeof draftId === 'string' ? draftId : undefined}
        />
      ) : (
        <div className={styles.content}>
          <h1>Select a project</h1>
          <Link href="/">Open projects →</Link>
        </div>
      )}
    </DashboardLayout>
  );
}
