import { useEffect, useState, type FormEvent } from 'react';
import Link from 'next/link';
import { RefreshCw } from 'lucide-react';
import Card from '@/components/ui/Card/Card';
import PageHeader from '@/components/ui/PageHeader/PageHeader';
import Button from '@/components/ui/Button/Button';
import Input from '@/components/ui/Input/Input';
import Select from '@/components/ui/Select/Select';
import Badge from '@/components/ui/Badge/Badge';
import { workspaceRequest } from '@/lib/chat';
import {
  LOG_KINDS,
  logKindLabel,
  logListPath,
  logTimestamp,
  type LogFilters,
  type LogPage,
} from '@/lib/logs';
import LogEventDetails from './LogEventDetails';
import CaptureSettingsPanel from './CaptureSettingsPanel';
import styles from './RequestLogs.module.css';

type PageQuery = { filters: LogFilters; beforeId?: number };
const emptyFilters: LogFilters = { kind: 'all', requestId: '' };

export default function RequestLogBrowser({
  projectId,
}: {
  projectId: string;
}) {
  const [filters, setFilters] = useState<LogFilters>(emptyFilters);
  const [query, setQuery] = useState<PageQuery>({ filters: emptyFilters });
  const [page, setPage] = useState<LogPage | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [filterError, setFilterError] = useState('');
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [captureOpen, setCaptureOpen] = useState(false);

  useEffect(() => {
    let active = true;
    async function read() {
      try {
        const data = await workspaceRequest<LogPage>(
          projectId,
          logListPath(query.filters, query.beforeId),
        );
        if (active) setPage(data);
      } catch (err) {
        if (active) setError((err as Error).message);
      } finally {
        if (active) setLoading(false);
      }
    }
    void read();
    return () => {
      active = false;
    };
  }, [projectId, query]);

  function loadPage(next: PageQuery) {
    setPage(null);
    setSelectedId(null);
    setError('');
    setLoading(true);
    setQuery(next);
  }

  function applyFilters(event: FormEvent) {
    event.preventDefault();
    const next = { ...filters, requestId: filters.requestId.trim() };
    try {
      logListPath(next);
    } catch (err) {
      setFilterError((err as Error).message);
      return;
    }
    setFilterError('');
    setFilters(next);
    loadPage({ filters: next });
  }

  function correlate(requestId: string) {
    const next: LogFilters = { kind: 'all', requestId };
    setFilters(next);
    setFilterError('');
    loadPage({ filters: next });
  }

  function clearFilters() {
    setFilters(emptyFilters);
    setFilterError('');
    loadPage({ filters: emptyFilters });
  }

  return (
    <div className={styles.content}>
      <PageHeader
        title="Request logs"
        actions={
          <>
            <Button
              type="button"
              variant="secondary"
              onClick={() => {
                setCaptureOpen(true);
                requestAnimationFrame(() =>
                  document
                    .getElementById('capture-options')
                    ?.scrollIntoView({ block: 'start' }),
                );
              }}
            >
              Capture settings
            </Button>
            <Button
              type="button"
              variant="secondary"
              disabled={loading}
              onClick={() => loadPage({ filters: query.filters })}
            >
              <RefreshCw size={16} />
              Refresh
            </Button>
          </>
        }
      />
      <p className={styles.context}>
        Newest first · Last 1,000 events · Refresh to see new activity
      </p>
      <Card>
        <form onSubmit={applyFilters} className={styles.filters}>
          <div>
            <label htmlFor="log-kind">Event kind</label>
            <Select
              id="log-kind"
              value={filters.kind}
              onValueChange={(kind) =>
                setFilters((current) => ({
                  ...current,
                  kind: kind as LogFilters['kind'],
                }))
              }
            >
              <option value="all">All events</option>
              {LOG_KINDS.map((kind) => (
                <option key={kind} value={kind}>
                  {logKindLabel(kind)}
                </option>
              ))}
            </Select>
          </div>
          <div className={styles.requestFilter}>
            <label htmlFor="log-request-id">Request ID</label>
            <Input
              id="log-request-id"
              value={filters.requestId}
              onChange={(event) =>
                setFilters((current) => ({
                  ...current,
                  requestId: event.target.value,
                }))
              }
              placeholder="32-character request ID (optional)"
              autoComplete="off"
              spellCheck={false}
            />
          </div>
          <div className={styles.actions}>
            <Button type="submit">Apply filters</Button>
            <button
              type="button"
              className={styles.secondary}
              onClick={clearFilters}
            >
              Clear filters
            </button>
          </div>
        </form>
        {filterError && (
          <p role="alert" className={styles.warning}>
            {filterError}
          </p>
        )}
      </Card>
      <section className={styles.history} aria-labelledby="history-heading">
        <div className={styles.sectionHeading}>
          <div>
            <h2 id="history-heading">Saved events</h2>
            <p>
              {query.beforeId
                ? `Events older than #${query.beforeId}`
                : 'Newest retained events'}{' '}
              ·{' '}
              {query.filters.kind === 'all'
                ? 'All events'
                : logKindLabel(query.filters.kind)}
            </p>
            {query.filters.requestId && (
              <p className={styles.requestId}>
                Request ID <code>{query.filters.requestId}</code>
              </p>
            )}
          </div>
        </div>
        {loading && <p role="status">Loading request history…</p>}
        {error && (
          <Card>
            <p role="alert" className={styles.warning}>
              {error}
            </p>
            <Button type="button" onClick={() => loadPage({ ...query })}>
              Retry history
            </Button>
          </Card>
        )}
        {page && (
          <>
            <p role="status" className={styles.context}>
              {page.events.length}{' '}
              {page.events.length === 1 ? 'event' : 'events'} on this page
            </p>
            {!page.events.length ? (
              <Card>
                <h3>No saved events on this page</h3>
                <p className={styles.context}>
                  Try clearing filters or returning to the newest page. Only
                  published gateway traffic is recorded; management calls and
                  disposable draft checks are excluded.
                </p>
                <Link
                  className={styles.link}
                  href={{ pathname: '/request-tester', query: { projectId } }}
                >
                  Open request tester →
                </Link>
              </Card>
            ) : (
              <div className={styles.browser}>
                <Card className={styles.listCard}>
                  <div className={styles.tableWrapper}>
                    <table className={styles.table}>
                      <caption className={styles.srOnly}>
                        Saved inspection events for this project
                      </caption>
                      <thead>
                        <tr>
                          <th scope="col">Event</th>
                          <th scope="col">Evidence</th>
                          <th scope="col">Details</th>
                        </tr>
                      </thead>
                      <tbody>
                        {page.events.map((event) => (
                          <tr
                            key={event.id}
                            className={
                              event.id === selectedId
                                ? styles.selectedRow
                                : undefined
                            }
                          >
                            <td>
                              <strong>{logKindLabel(event.kind)}</strong>
                              <span className={styles.eventId}>
                                #{event.id}
                              </span>
                            </td>
                            <td>
                              <div className={styles.eventSummary}>
                                {event.method ? (
                                  <code>
                                    {event.method} {event.path}
                                  </code>
                                ) : (
                                  <span>
                                    {event.exception_type ||
                                      (event.kind === 'stdout' ||
                                      event.kind === 'stderr'
                                        ? 'Captured process output'
                                        : 'Worker error')}
                                  </span>
                                )}
                                <time dateTime={event.timestamp}>
                                  {logTimestamp(event.timestamp)}
                                </time>
                                <div className={styles.numbers}>
                                  {event.status_code !== undefined && (
                                    <Badge
                                      variant={
                                        event.status_code >= 400
                                          ? 'danger'
                                          : 'success'
                                      }
                                    >
                                      {event.status_code}
                                    </Badge>
                                  )}
                                  {event.duration_ms !== undefined && (
                                    <span>{event.duration_ms} ms</span>
                                  )}
                                </div>
                              </div>
                            </td>
                            <td>
                              <button
                                type="button"
                                className={styles.viewButton}
                                aria-label={`View event ${event.id}`}
                                aria-pressed={event.id === selectedId}
                                aria-controls="event-details"
                                onClick={() => setSelectedId(event.id)}
                              >
                                View details
                              </button>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </Card>
                <section
                  id="event-details"
                  className={styles.detailsPanel}
                  aria-label="Event details"
                >
                  {selectedId !== null ? (
                    <LogEventDetails
                      key={`${projectId}:${selectedId}`}
                      projectId={projectId}
                      eventId={selectedId}
                      onCorrelate={correlate}
                    />
                  ) : (
                    <Card>
                      <h2>Select an event</h2>
                      <p className={styles.context}>
                        Open a row to inspect request IDs, recorded errors, or
                        captured evidence. No request is replayed.
                      </p>
                    </Card>
                  )}
                </section>
              </div>
            )}
          </>
        )}
        <nav className={styles.pagination} aria-label="History pages">
          <button
            type="button"
            className={styles.secondary}
            disabled={loading || query.beforeId === undefined}
            onClick={() => loadPage({ filters: query.filters })}
          >
            Newest events
          </button>
          <button
            type="button"
            className={styles.secondary}
            disabled={loading || !page?.next_before_id}
            onClick={() => {
              if (page?.next_before_id)
                loadPage({
                  filters: query.filters,
                  beforeId: page.next_before_id,
                });
            }}
          >
            Older events
          </button>
        </nav>
      </section>
      <details
        id="capture-options"
        className={styles.captureDisclosure}
        open={captureOpen}
        onToggle={(event) => setCaptureOpen(event.currentTarget.open)}
      >
        <summary>Capture settings</summary>
        <CaptureSettingsPanel projectId={projectId} />
      </details>
    </div>
  );
}
