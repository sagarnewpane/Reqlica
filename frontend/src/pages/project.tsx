import Link from 'next/link';
import { useRouter } from 'next/router';
import { useEffect, useState } from 'react';
import DashboardLayout from '@/components/layout/DashboardLayout';
import WorkerControls from '@/components/runtime/WorkerControls';
import ProjectChat from '@/components/chat/ProjectChat';
import { workspaceRequest } from '@/lib/chat';
import type { Project } from '@/lib/projects';
import { isProjectId } from '@/lib/project-route';
import { useHydrated } from '@/hooks/use-hydrated';
import styles from '@/components/chat/ProjectChat.module.css';

function Workspace({ projectId }: { projectId: string }) {
  const [project, setProject] = useState<Project | null>(null);
  const [error, setError] = useState('');
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let active = true;
    workspaceRequest<Project>(projectId, 'endpoints')
      .then((value) => {
        if (active) {
          setProject(value);
          setError('');
        }
      })
      .catch((err) => {
        if (active) setError(err.message);
      });
    return () => {
      active = false;
    };
  }, [projectId, attempt]);
  return (
    <DashboardLayout title={project?.name || 'Project'}>
      <div className={styles.workspace}>
        {error && (
          <div role="alert" className={styles.error}>
            {error}{' '}
            <button onClick={() => setAttempt((value) => value + 1)}>
              Try again
            </button>
          </div>
        )}
        {!project ? (
          !error && <p role="status">Loading project…</p>
        ) : (
          <>
            <WorkerControls
              projectId={projectId}
              title={project.name || 'Project'}
              project={project}
              onProjectChange={setProject}
            />
            <ProjectChat
              projectId={projectId}
              projectName={project.name || 'this project'}
            />
          </>
        )}
      </div>
    </DashboardLayout>
  );
}

export default function ProjectPage() {
  const router = useRouter();
  const hydrated = useHydrated();
  if (!hydrated || !router.isReady)
    return (
      <DashboardLayout title="Project">
        <p role="status">Loading project…</p>
      </DashboardLayout>
    );
  const id = router.query.projectId;
  if (!isProjectId(id))
    return (
      <DashboardLayout title="Project">
        <div className={styles.workspace}>
          <h1>Invalid project link</h1>
          <Link href="/">Return to projects</Link>
        </div>
      </DashboardLayout>
    );
  return <Workspace key={id} projectId={id} />;
}
