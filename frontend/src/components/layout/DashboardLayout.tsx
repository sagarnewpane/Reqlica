import Link from 'next/link';
import Head from 'next/head';
import { useRouter } from 'next/router';
import { type ReactNode } from 'react';
import { useHydrated } from '@/hooks/use-hydrated';
import {
  MessageSquare,
  FileCheck2,
  Route,
  Send,
  Database,
  ScrollText,
  SlidersHorizontal,
  Settings,
  ArrowLeft,
} from 'lucide-react';
import {
  Sidebar,
  SidebarHeader,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupLabel,
  SidebarGroupContent,
  SidebarMenu,
  SidebarMenuItem,
  SidebarMenuButton,
  SidebarTrigger,
  useSidebar,
} from '@/components/ui/shadcn/sidebar';
import { projectFromRoute } from '@/lib/project-route';
import styles from './DashboardLayout.module.css';
const menuItems = [
  {
    label: 'Project chat',
    href: '/generate',
    icon: MessageSquare,
    group: 'Build',
  },
  { label: 'Drafts', href: '/draft-review', icon: FileCheck2, group: 'Build' },
  { label: 'Endpoints', href: '/endpoints', icon: Route, group: 'Build' },
  {
    label: 'Request tester',
    href: '/request-tester',
    icon: Send,
    group: 'Test & inspect',
  },
  {
    label: 'Sample data',
    href: '/sample-data',
    icon: Database,
    group: 'Test & inspect',
  },
  {
    label: 'Request logs',
    href: '/request-logs',
    icon: ScrollText,
    group: 'Test & inspect',
  },
  {
    label: 'Runtime',
    href: '/runtime',
    icon: SlidersHorizontal,
    group: 'Configure',
  },
  {
    label: 'AI settings',
    href: '/settings',
    icon: Settings,
    group: 'Configure',
  },
];
function ProjectSidebar({ projectId }: { projectId: string }) {
  const router = useRouter();
  const { setOpenMobile } = useSidebar();
  return (
    <Sidebar
      collapsible="offcanvas"
      className={styles.sidebarPanel}
      aria-label="Project navigation"
    >
      <SidebarHeader className={styles.sidebarHeader}>
        <Link
          href="/"
          className={styles.logo}
          aria-label="Reqlica projects"
          onClick={() => setOpenMobile(false)}
        >
          <span className={styles.brandMark} aria-hidden="true" />
          Reqlica
        </Link>
      </SidebarHeader>
      <SidebarContent>
        {['Build', 'Test & inspect', 'Configure'].map((group) => (
          <SidebarGroup key={group}>
            <SidebarGroupLabel className={styles.groupLabel}>
              {group}
            </SidebarGroupLabel>
            <SidebarGroupContent>
              <SidebarMenu>
                {menuItems
                  .filter((item) => item.group === group)
                  .map((item) => (
                    <SidebarMenuItem key={item.href}>
                      <SidebarMenuButton
                        asChild
                        isActive={
                          router.pathname === item.href ||
                          (item.href === '/generate' &&
                            router.pathname === '/project')
                        }
                        className={styles.menuButton}
                      >
                        <Link
                          href={
                            item.href === '/generate'
                              ? `/project?projectId=${projectId}`
                              : { pathname: item.href, query: { projectId } }
                          }
                          onClick={() => setOpenMobile(false)}
                          aria-current={
                            router.pathname === item.href ||
                            (item.href === '/generate' &&
                              router.pathname === '/project')
                              ? 'page'
                              : undefined
                          }
                        >
                          <item.icon size={18} aria-hidden="true" />
                          <span>{item.label}</span>
                        </Link>
                      </SidebarMenuButton>
                    </SidebarMenuItem>
                  ))}
              </SidebarMenu>
            </SidebarGroupContent>
          </SidebarGroup>
        ))}
      </SidebarContent>
      <SidebarFooter className={styles.sidebarFooter}>
        <span>Reqlica Console</span>
        <span>Local API workspace</span>
      </SidebarFooter>
    </Sidebar>
  );
}
export default function DashboardLayout({
  children,
  title,
}: {
  children: ReactNode;
  title: string;
}) {
  const router = useRouter();
  // Static Pages Router output has no query yet. Keep the first hydration pass identical.
  const mounted = useHydrated();
  const projectId = mounted
    ? projectFromRoute(router.query.projectId, router.asPath)
    : null;
  return (
    <div className={styles.dashboard}>
      <Head>
        <title>{`${title} · Reqlica`}</title>
      </Head>
      <a className={styles.skip} href="#main-content">
        Skip to content
      </a>
      {projectId && <ProjectSidebar projectId={projectId} />}
      <div className={styles.main}>
        <header className={styles.header}>
          {projectId ? (
            <div className={styles.projectBreadcrumb}>
              <SidebarTrigger className={styles.sidebarToggle} />
              <Link href="/" className={styles.backToProjects}>
                <ArrowLeft size={15} aria-hidden="true" />
                Back to projects
              </Link>
              <span title={title}>{title}</span>
            </div>
          ) : (
            <div className={styles.workspaceBrand}>
              <Link href="/" aria-label="Reqlica projects">
                <span className={styles.brandMark} aria-hidden="true" />
                Reqlica
              </Link>
              <span title={title}>{title}</span>
            </div>
          )}
          <Link
            className={styles.headerSettings}
            aria-label="Settings"
            aria-current={router.pathname === '/settings' ? 'page' : undefined}
            href={
              projectId
                ? { pathname: '/settings', query: { projectId } }
                : '/settings'
            }
          >
            <Settings size={16} aria-hidden="true" />
            <span>Settings</span>
          </Link>
        </header>
        <main id="main-content" tabIndex={-1}>
          {children}
        </main>
      </div>
    </div>
  );
}
