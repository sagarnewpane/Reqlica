import { ImportProject } from '@/components/projects/ProjectTransfer';
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
} from 'react';
import ProjectActions from '@/components/projects/ProjectActions';
import { Skeleton } from '@/components/ui/shadcn/skeleton';
import Link from 'next/link';
import DashboardLayout from '@/components/layout/DashboardLayout';
import Badge from '@/components/ui/Badge/Badge';
import Button from '@/components/ui/Button/Button';
import Card from '@/components/ui/Card/Card';
import Input from '@/components/ui/Input/Input';
import Select from '@/components/ui/Select/Select';
import PageHeader from '@/components/ui/PageHeader/PageHeader';
import { ChevronRight, Plus } from 'lucide-react';
import { projectRequest, type Project } from '@/lib/projects';
import styles from './Home.module.css';

export default function Home() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [query, setQuery] = useState('');
  const [status, setStatus] = useState('all');
  const [notice, setNotice] = useState('');
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState('');
  const [name, setName] = useState('');
  const [directory, setDirectory] = useState('');
  const dialog = useRef<HTMLDialogElement>(null);
  const request = useRef(0);

  const refresh = useCallback(async () => {
    const sequence = ++request.current;
    setLoading(true);
    setError('');
    try {
      const data = await projectRequest();
      if (sequence === request.current) setProjects(data.projects);
    } catch (err) {
      if (sequence === request.current) setError((err as Error).message);
    } finally {
      if (sequence === request.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    let active = true;
    const sequence = ++request.current;
    projectRequest()
      .then((data) => {
        if (active && sequence === request.current) setProjects(data.projects);
      })
      .catch((err) => {
        if (active && sequence === request.current) setError(err.message);
      })
      .finally(() => {
        if (active && sequence === request.current) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  function openCreate() {
    setFormError('');
    dialog.current?.showModal();
  }

  async function createProject(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (saving) return;
    setSaving(true);
    setFormError('');
    try {
      const project = await projectRequest({
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, directory }),
      });
      request.current++;
      setLoading(false);
      setProjects((current) => [
        project,
        ...current.filter((item) => item.id !== project.id),
      ]);
      setError('');
      setQuery('');
      setStatus('all');
      setNotice(
        `${project.name} created. Your project is idle and ready to configure.`,
      );
      dialog.current?.close();
      setName('');
      setDirectory('');
      void refresh();
    } catch (err) {
      setFormError((err as Error).message);
    } finally {
      setSaving(false);
    }
  }

  const filtered = projects
    .filter(
      (project) =>
        (status === 'all' || project.status === status) &&
        `${project.name ?? ''} ${project.directory ?? ''} ${project.id}`
          .toLowerCase()
          .includes(query.trim().toLowerCase()),
    )
    .sort((a, b) => (a.name ?? a.id).localeCompare(b.name ?? b.id));

  return (
    <DashboardLayout title="Projects">
      <section className={styles.content}>
        <PageHeader
          title="Projects"
          actions={
            <Button onClick={openCreate}>
              <Plus size={16} />
              New project
            </Button>
          }
        />
        <details className={styles.importPanel}>
          <summary>
            <ChevronRight size={16} aria-hidden="true" />
            <span>Import project / restore workspace</span>
          </summary>
          <ImportProject onImported={() => void refresh()} />
        </details>
        {notice && (
          <div className={styles.notice} role="status">
            {notice}
            <button
              onClick={() => setNotice('')}
              aria-label="Dismiss notification"
            >
              ×
            </button>
          </div>
        )}
        <div className={styles.toolbar}>
          <div className={styles.searchField}>
            <label htmlFor="project-search">Search projects</label>
            <Input
              id="project-search"
              type="search"
              placeholder="Search by name or directory…"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
          </div>
          <div className={styles.filterField}>
            <label htmlFor="project-status">Status</label>
            <Select
              id="project-status"
              value={status}
              onValueChange={(nextValue) => setStatus(nextValue)}
            >
              <option value="all">All statuses</option>
              <option value="running">Running</option>
              <option value="idle">Idle</option>
              <option value="stopped">Stopped</option>
              <option value="error">Error</option>
            </Select>
          </div>
          <button
            className={styles.secondaryButton}
            disabled={loading}
            onClick={() => void refresh()}
          >
            {loading ? 'Refreshing…' : 'Refresh'}
          </button>
        </div>
        {error && (
          <div className={styles.error} role="alert">
            <strong>Projects could not be refreshed</strong>
            <p>{error}</p>
            <button
              className={styles.secondaryButton}
              onClick={() => void refresh()}
            >
              Try again
            </button>
          </div>
        )}
        <p className={styles.resultCount} role="status">
          {loading
            ? 'Loading projects…'
            : `${filtered.length} of ${projects.length} projects${error ? ' · Last loaded data' : ''}`}
        </p>
        {loading && !projects.length ? (
          <div className={styles.projectGrid} aria-hidden="true">
            {[0, 1, 2].map((index) => (
              <Card key={index}>
                <Skeleton className="h-5 w-2/3 mb-6" />
                <Skeleton className="h-4 w-full mb-3" />
                <Skeleton className="h-4 w-4/5 mb-8" />
                <Skeleton className="h-9 w-32" />
              </Card>
            ))}
          </div>
        ) : !filtered.length && !error ? (
          <Card>
            <div className={styles.empty}>
              <h2>
                {projects.length
                  ? 'No matching projects'
                  : 'Your first API starts here'}
              </h2>
              <p>
                {projects.length
                  ? 'Try another name or choose a different status.'
                  : 'Create a project to give your mock API a home.'}
              </p>
              {projects.length ? (
                <button
                  className={styles.secondaryButton}
                  onClick={() => {
                    setQuery('');
                    setStatus('all');
                  }}
                >
                  Clear filters
                </button>
              ) : (
                <Button onClick={openCreate}>Create your first project</Button>
              )}
            </div>
          </Card>
        ) : null}
        <div className={styles.projectGrid} aria-busy={loading}>
          {filtered.map((project) => (
            <Card key={project.id}>
              <article className={styles.projectCard}>
                <div className={styles.projectHeading}>
                  <h2>
                    <Link
                      className={styles.projectLink}
                      href={`/projects/${project.id}`}
                    >
                      {project.name || 'Unavailable project'}
                    </Link>
                  </h2>
                  <div className={styles.cardActions}>
                    <Badge
                      variant={
                        project.status === 'running'
                          ? 'success'
                          : project.status === 'error'
                            ? 'danger'
                            : 'warning'
                      }
                    >
                      {project.status}
                    </Badge>
                    <ProjectActions
                      id={project.id}
                      name={project.name || 'project'}
                      onDeleted={() => {
                        setProjects((current) =>
                          current.filter((item) => item.id !== project.id),
                        );
                        setNotice('Project removed.');
                      }}
                    />
                  </div>
                </div>
                <p className={styles.directory}>
                  {project.directory || project.id}
                </p>
                {project.error && (
                  <p className={styles.projectError}>{project.error}</p>
                )}
                <Link
                  className={styles.openProject}
                  href={`/projects/${project.id}`}
                  aria-label={`Open ${project.name || 'project'}`}
                >
                  Open project →
                </Link>
                <div className={styles.projectFooter}>
                  <span>
                    {project.endpoints.length}{' '}
                    {project.endpoints.length === 1 ? 'endpoint' : 'endpoints'}
                  </span>
                  <details className={styles.details}>
                    <summary>Details</summary>
                    <dl>
                      <dt>Project ID</dt>
                      <dd>{project.id}</dd>
                      <dt>Endpoints</dt>
                      <dd>
                        {project.endpoints.length ? (
                          <ul>
                            {project.endpoints.map((endpoint, index) => (
                              <li
                                key={`${endpoint.method}-${endpoint.path}-${index}`}
                              >
                                <strong>{endpoint.method}</strong>{' '}
                                {endpoint.path}
                              </li>
                            ))}
                          </ul>
                        ) : (
                          'No endpoints yet'
                        )}
                      </dd>
                    </dl>
                  </details>
                </div>
              </article>
            </Card>
          ))}
        </div>
      </section>
      <dialog
        ref={dialog}
        className={styles.dialog}
        aria-labelledby="create-title"
        aria-describedby="create-description"
        onCancel={(event) => {
          if (saving) event.preventDefault();
        }}
      >
        <form onSubmit={createProject} className={styles.createForm}>
          <div>
            <h2 id="create-title">Create a project</h2>
            <p id="create-description">
              Give your API a name and choose where its files will live.
            </p>
          </div>
          <div className={styles.field}>
            <label htmlFor="project-name">Project name</label>
            <Input
              autoFocus
              id="project-name"
              placeholder="e.g. Bookstore API"
              required
              maxLength={128}
              value={name}
              onChange={(event) => setName(event.target.value)}
              disabled={saving}
            />
          </div>
          <div className={styles.field}>
            <label htmlFor="project-directory">Project directory</label>
            <Input
              id="project-directory"
              placeholder="~/Projects/bookstore-api"
              required
              pattern="(/|~/).*"
              aria-describedby="directory-help"
              value={directory}
              onChange={(event) => setDirectory(event.target.value)}
              disabled={saving}
            />
            <p id="directory-help">
              Use a new or empty folder on the machine running Reqlica. An
              absolute path or ~/ path is required.
            </p>
          </div>
          {formError && (
            <p className={styles.error} role="alert">
              {formError}
            </p>
          )}
          <p className={styles.formHint}>
            Creates a starter /hello endpoint. The project stays idle until
            started.
          </p>
          <div className={styles.formActions}>
            <button
              type="button"
              className={styles.secondaryButton}
              disabled={saving}
              onClick={() => dialog.current?.close()}
            >
              Cancel
            </button>
            <Button
              disabled={saving || !name.trim() || !directory.trim()}
              type="submit"
            >
              {saving ? 'Creating…' : 'Create project'}
            </Button>
          </div>
        </form>
      </dialog>
    </DashboardLayout>
  );
}
