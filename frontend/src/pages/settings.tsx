import { useRouter } from 'next/router';
import DashboardLayout from '@/components/layout/DashboardLayout';
import ConnectionSettings from '@/components/settings/ConnectionSettings';
import SettingsForm from '@/components/settings/SettingsForm';
import { useHydrated } from '@/hooks/use-hydrated';
import { isProjectId } from '@/lib/project-route';

export default function Settings() {
  const hydrated = useHydrated();
  const router = useRouter();
  const id = router.query.projectId;
  const projectId = isProjectId(id) ? id : undefined;

  return (
    <DashboardLayout title="Settings">
      {hydrated && router.isReady ? (
        <>
          <SettingsForm key={projectId || 'workspace'} projectId={projectId} />
          <ConnectionSettings projectId={projectId} />
        </>
      ) : (
        <p role="status">Loading settings…</p>
      )}
    </DashboardLayout>
  );
}
