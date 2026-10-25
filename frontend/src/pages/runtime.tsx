import { useRouter } from 'next/router';
import { useHydrated } from '@/hooks/use-hydrated';
import { isProjectId } from '@/lib/project-route';
import Link from 'next/link';
import * as Tabs from '@radix-ui/react-tabs';
import { KeyRound, Braces, Package } from 'lucide-react';
import DashboardLayout from '@/components/layout/DashboardLayout';
import TrafficPanel from '@/components/runtime/TrafficPanel';
import AccessPanel from '@/components/runtime/AccessPanel';
import ProjectManagement from '@/components/projects/ProjectManagement';
import MigrationsPanel from '@/components/drafts/MigrationsPanel';
import BaselinePanel from '@/components/data/BaselinePanel';
import EnvironmentPanel from '@/components/runtime/EnvironmentPanel';
import HeadersPanel from '@/components/runtime/HeadersPanel';
import PackagesPanel from '@/components/runtime/PackagesPanel';
import WorkerControls from '@/components/runtime/WorkerControls';
import PageHeader from '@/components/ui/PageHeader/PageHeader';
import styles from './Runtime.module.css';
export default function RuntimePage() {
  const mounted = useHydrated();
  const router = useRouter();
  const id = router.query.projectId;
  const projectId = isProjectId(id) ? id : null;
  return (
    <DashboardLayout title="Runtime">
      <div className={styles.content}>
        {!projectId && <PageHeader title="Runtime" />}
        {!mounted || !router.isReady ? (
          <p role="status">Loading configuration…</p>
        ) : !projectId ? (
          <p>
            Select a project to manage its runtime.{' '}
            <Link href="/">Open projects →</Link>
          </p>
        ) : (
          <div key={projectId}>
            <div className={styles.worker}>
              <WorkerControls projectId={projectId} title="Runtime" />
            </div>
            <Tabs.Root
              value={
                [
                  'environment',
                  'headers',
                  'packages',
                  'traffic',
                  'access',
                  'data',
                  'migrations',
                  'project',
                ].includes(String(router.query.tab))
                  ? String(router.query.tab)
                  : 'environment'
              }
              onValueChange={(tab) =>
                void router.replace(
                  { pathname: '/runtime', query: { projectId, tab } },
                  undefined,
                  { shallow: true, scroll: false },
                )
              }
            >
              <Tabs.List
                className={styles.tabs}
                aria-label="Runtime configuration"
              >
                <Tabs.Trigger value="environment">
                  <KeyRound size={16} />
                  Environment
                </Tabs.Trigger>
                <Tabs.Trigger value="headers">
                  <Braces size={16} />
                  Request headers
                </Tabs.Trigger>
                <Tabs.Trigger value="packages">
                  <Package size={16} />
                  Python packages
                </Tabs.Trigger>
                <Tabs.Trigger value="traffic">Traffic</Tabs.Trigger>
                <Tabs.Trigger value="access">API access</Tabs.Trigger>
                <Tabs.Trigger value="data">Data baseline</Tabs.Trigger>
                <Tabs.Trigger value="migrations">Migrations</Tabs.Trigger>
                <Tabs.Trigger value="project">Project & history</Tabs.Trigger>
              </Tabs.List>
              <Tabs.Content value="environment">
                <EnvironmentPanel projectId={projectId} />
              </Tabs.Content>
              <Tabs.Content value="headers">
                <HeadersPanel projectId={projectId} />
              </Tabs.Content>
              <Tabs.Content value="packages">
                <PackagesPanel projectId={projectId} />
              </Tabs.Content>
              <Tabs.Content value="traffic">
                <TrafficPanel projectId={projectId} />
              </Tabs.Content>
              <Tabs.Content value="access">
                <AccessPanel projectId={projectId} />
              </Tabs.Content>
              <Tabs.Content value="data">
                <BaselinePanel projectId={projectId} />
              </Tabs.Content>
              <Tabs.Content value="migrations">
                <MigrationsPanel projectId={projectId} />
              </Tabs.Content>
              <Tabs.Content value="project">
                <ProjectManagement projectId={projectId} />
              </Tabs.Content>
            </Tabs.Root>
          </div>
        )}
      </div>
    </DashboardLayout>
  );
}
