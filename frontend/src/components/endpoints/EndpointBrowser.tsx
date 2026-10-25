import { useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { ChevronRight, MessageSquare, RefreshCw, Send } from 'lucide-react';
import PageHeader from '@/components/ui/PageHeader/PageHeader';
import WorkerControls from '@/components/runtime/WorkerControls';
import Button from '@/components/ui/Button/Button';
import Card from '@/components/ui/Card/Card';
import Input from '@/components/ui/Input/Input';
import Select from '@/components/ui/Select/Select';
import { workspaceRequest } from '@/lib/chat';
import { endpointKey, filterEndpoints, REQUEST_METHODS } from '@/lib/endpoints';
import type { Project } from '@/lib/projects';
import ContractViewer from './ContractViewer';
import styles from './EndpointBrowser.module.css';

export default function EndpointBrowser({ projectId }: { projectId: string }) {
  const [project, setProject] = useState<Project | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [attempt, setAttempt] = useState(0);
  const [query, setQuery] = useState('');
  const [method, setMethod] = useState('all');
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const detailsHeading = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    let active = true;
    workspaceRequest<Project>(projectId, 'endpoints')
      .then((value) => {
        if (active) setProject(value);
      })
      .catch((err) => {
        if (active) setError((err as Error).message);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [projectId, attempt]);

  function refresh() {
    setProject(null);
    setError('');
    setLoading(true);
    setAttempt((current) => current + 1);
  }

  const endpoints = project?.endpoints || [];
  const methods = [
    ...new Set(endpoints.map((endpoint) => endpoint.method)),
  ].sort();
  // A previously chosen method can disappear after a publication and refresh.
  const activeMethod = methods.includes(method) ? method : 'all';
  const visible = filterEndpoints(endpoints, query, activeMethod);
  const selected =
    visible.find((endpoint) => endpointKey(endpoint) === selectedKey) ||
    visible[0];

  return (
    <div className={styles.content}>
      {project ? (
        <WorkerControls
          title="Endpoints"
          projectId={projectId}
          project={project}
          onProjectChange={setProject}
        />
      ) : (
        <PageHeader
          title="Endpoints"
          actions={
            <Button
              type="button"
              variant="secondary"
              disabled={loading}
              onClick={refresh}
            >
              <RefreshCw size={16} />
              Refresh
            </Button>
          }
        />
      )}
      {loading && <p role="status">Loading project endpoints…</p>}
      {error && (
        <Card>
          <div role="alert">
            <h2>Could not load endpoints</h2>
            <p>{error}</p>
          </div>
          <Button type="button" onClick={refresh}>
            Try again
          </Button>
        </Card>
      )}
      {project && (
        <>
          <details className={styles.projectDetails}>
            <summary>
              {project.name || 'Project details'} · {endpoints.length} routes
            </summary>
            <p className={styles.context}>
              {project.directory || 'Project directory unavailable'}
            </p>
            {project.active_version && (
              <p className={styles.version}>
                Published version <code>{project.active_version}</code>
              </p>
            )}
            <p className={styles.context}>
              {project.status === 'running'
                ? 'Routes reported by the running worker.'
                : 'Configured routes; this does not mean the worker is running.'}{' '}
              Browsing does not execute handlers or send test requests.
            </p>
          </details>
          <section className={styles.section} aria-labelledby="routes-heading">
            <div className={styles.sectionHeading}>
              <div>
                <h2 id="routes-heading">Published routes</h2>
              </div>
              <div className={styles.filters}>
                <Button asChild variant="secondary">
                  <Link href={`/projects/${projectId}`}>
                    <MessageSquare size={16} />
                    Edit in chat
                  </Link>
                </Button>
                <div className={styles.search}>
                  <label htmlFor="endpoint-search">Search routes</label>
                  <Input
                    id="endpoint-search"
                    type="search"
                    placeholder="Method, path, or handler…"
                    value={query}
                    onChange={(event) => setQuery(event.target.value)}
                  />
                </div>
                <div className={styles.methodFilter}>
                  <label htmlFor="endpoint-method">Method</label>
                  <Select
                    id="endpoint-method"
                    value={activeMethod}
                    onValueChange={setMethod}
                  >
                    <option value="all">All methods</option>
                    {methods.map((item) => (
                      <option key={item} value={item}>
                        {item}
                      </option>
                    ))}
                  </Select>
                </div>
              </div>
            </div>
            <p className={styles.count} role="status">
              {visible.length} of {endpoints.length} routes
            </p>
            {!endpoints.length ? (
              <Card className={styles.empty}>
                <h3>No published endpoints</h3>
                <p>
                  Ask the project assistant to prepare routes in a draft, then
                  validate, test, approve, and activate the changes.
                </p>
                <Link className={styles.link} href={`/projects/${projectId}`}>
                  Open project chat →
                </Link>
              </Card>
            ) : !visible.length ? (
              <Card className={styles.empty}>
                <h3>No matching endpoints</h3>
                <p>Try a different search or method filter.</p>
                <Button
                  type="button"
                  onClick={() => {
                    setQuery('');
                    setMethod('all');
                  }}
                >
                  Clear filters
                </Button>
              </Card>
            ) : (
              <div className={styles.browser}>
                <Card className={styles.routeCard}>
                  <div className={styles.tableWrapper}>
                    <table className={styles.table}>
                      <caption className={styles.srOnly}>
                        Published endpoints for {project.name || 'this project'}
                      </caption>
                      <thead>
                        <tr>
                          <th scope="col">Method</th>
                          <th scope="col">Route</th>
                        </tr>
                      </thead>
                      <tbody>
                        {visible.map((endpoint) => (
                          <tr
                            key={endpointKey(endpoint)}
                            className={
                              endpointKey(endpoint) === endpointKey(selected)
                                ? styles.selectedRow
                                : undefined
                            }
                          >
                            <td>
                              <span className={styles.method}>
                                {endpoint.method}
                              </span>
                            </td>
                            <td className={styles.path}>
                              <button
                                type="button"
                                className={styles.routeButton}
                                aria-label={`View ${endpoint.method} ${endpoint.path}`}
                                aria-controls="endpoint-details"
                                aria-pressed={
                                  endpointKey(endpoint) ===
                                  endpointKey(selected)
                                }
                                onClick={() => {
                                  setSelectedKey(endpointKey(endpoint));
                                  detailsHeading.current?.focus();
                                }}
                              >
                                <code>{endpoint.path}</code>
                                <ChevronRight size={16} aria-hidden="true" />
                              </button>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </Card>
                <section
                  id="endpoint-details"
                  className={styles.detailsPanel}
                  aria-labelledby="endpoint-details-heading"
                >
                  <Card>
                    <div className={styles.details}>
                      <h3
                        id="endpoint-details-heading"
                        ref={detailsHeading}
                        tabIndex={-1}
                      >
                        Endpoint details
                      </h3>
                      <div className={styles.detailsHeading}>
                        <span className={styles.method}>{selected.method}</span>
                        <code>{selected.path}</code>
                      </div>
                      <dl className={styles.metadata}>
                        <dt>Python handler</dt>
                        <dd>
                          <code>
                            {selected.handler || 'Not reported by the runtime'}
                          </code>
                        </dd>
                        <dt>Source</dt>
                        <dd>Published project manifest</dd>
                      </dl>
                      <p>Send a request to see the live response.</p>
                      {/\{[^}]+\}/.test(selected.path) && (
                        <p className={styles.warning}>
                          Replace path parameters with real values in the
                          request tester before sending.
                        </p>
                      )}
                      {REQUEST_METHODS.includes(selected.method) ? (
                        <Link
                          className={styles.testLink}
                          href={{
                            pathname: '/request-tester',
                            query: {
                              projectId,
                              method: selected.method,
                              path: selected.path,
                            },
                          }}
                        >
                          <Send size={16} />
                          Test endpoint
                        </Link>
                      ) : (
                        <p className={styles.warning}>
                          The request tester does not support {selected.method}.
                          This route is shown for inspection only.
                        </p>
                      )}
                      <Link
                        className={styles.link}
                        href={`/projects/${projectId}?prompt=${encodeURIComponent(`Inspect and edit ${selected.method} ${selected.path}, handler ${selected.handler || 'not reported'}. Read existing source first and make changes in an unpublished draft.`)}`}
                      >
                        Open endpoint in chat
                      </Link>
                      <Link
                        className={styles.link}
                        href={{
                          pathname: '/draft-review',
                          query: { projectId },
                        }}
                      >
                        Open draft workspace →
                      </Link>
                    </div>
                  </Card>
                </section>
              </div>
            )}
          </section>
          <ContractViewer projectId={projectId} />
        </>
      )}
    </div>
  );
}
