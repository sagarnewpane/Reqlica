import { useEffect, useState, type FormEvent } from 'react';
import { RefreshCw, Package } from 'lucide-react';
import Input from '@/components/ui/Input/Input';
import Button from '@/components/ui/Button/Button';
import { workspaceRequest } from '@/lib/chat';
import styles from './RuntimePanels.module.css';

type Job = {
  id: string;
  status: string;
  requirement: string;
  message?: string;
  plan_digest?: string;
  packages?: { name: string; version: string; action?: string }[];
  log?: string;
};
type Packages = {
  revision: string;
  prefix: string;
  installed: Record<string, string>;
  installer: { available: boolean; reason: string };
  jobs: Job[];
  scan: { issues: unknown[]; error?: string };
};
export default function PackagesPanel({ projectId }: { projectId: string }) {
  const [data, setData] = useState<Packages | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const [name, setName] = useState('');
  const [version, setVersion] = useState('');
  const [search, setSearch] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [history, setHistory] = useState<Job[]>([]);
  const [offset, setOffset] = useState<number | null>(0);
  async function loadHistory() {
    try {
      const page = await workspaceRequest<{
        jobs: Job[];
        next_offset: number | null;
      }>(projectId, `packages/jobs?offset=${offset || 0}`);
      setHistory((current) => [
        ...current,
        ...page.jobs.filter(
          (item) => !current.some((existing) => existing.id === item.id),
        ),
      ]);
      setOffset(page.next_offset);
    } catch (reason) {
      setError((reason as Error).message);
    }
  }
  useEffect(() => {
    let active = true;
    workspaceRequest<Packages>(projectId, 'packages')
      .then((value) => {
        if (active) {
          setData(value);
          setJob(
            (current) =>
              value.jobs.find((item) => item.id === current?.id) ||
              current ||
              value.jobs[0] ||
              null,
          );
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
  useEffect(() => {
    if (!job || !['resolving', 'installing'].includes(job.status)) return;
    let active = true;
    const timer = setTimeout(() => {
      workspaceRequest<Job>(projectId, `packages/jobs/${job.id}`)
        .then((value) => {
          if (active) {
            setJob(value);
            if (!['resolving', 'installing'].includes(value.status))
              setAttempt((count) => count + 1);
          }
        })
        .catch((err) => {
          if (active) setError(err.message);
        });
    }, 2000);
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [projectId, job]);
  async function preview(event: FormEvent) {
    event.preventDefault();
    if (!data || busy) return;
    setBusy(true);
    setError('');
    setConfirmed(false);
    try {
      setJob(
        await workspaceRequest<Job>(projectId, 'packages/preview', 'POST', {
          package: name.trim(),
          version: version.trim() || null,
          expected_revision: data.revision,
        }),
      );
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function install() {
    if (!job?.plan_digest || !confirmed || busy) return;
    setBusy(true);
    setError('');
    try {
      setJob(
        await workspaceRequest<Job>(
          projectId,
          `packages/jobs/${job.id}/install`,
          'POST',
          { expected_plan_digest: job.plan_digest, confirm: true },
        ),
      );
      setConfirmed(false);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const running =
    busy || (!!job && ['resolving', 'installing'].includes(job.status));
  const installed = Object.entries(data?.installed || {}).filter(
    ([packageName]) => packageName.includes(search.toLowerCase()),
  );
  return (
    <section className={styles.panel}>
      <header className={styles.heading}>
        <div>
          <h2>Python packages</h2>
          <p>Inspect imports and review dependencies before installation.</p>
        </div>
        <Package size={20} />
      </header>
      <div className={styles.info}>
        Packages are installed in the shared Python environment and can affect
        every project. Existing installed versions are pinned during resolution.
      </div>
      {!!data?.jobs.length && (
        <label>
          Saved package operation
          <select
            value={job?.id || ''}
            disabled={busy}
            onChange={(e) => {
              const selected = data.jobs.find(
                (item) => item.id === e.target.value,
              );
              if (selected) {
                setJob(selected);
                setConfirmed(false);
              }
            }}
          >
            {data.jobs.map((item) => (
              <option value={item.id} key={item.id}>
                {item.id.slice(0, 8)} · {item.status}
              </option>
            ))}
          </select>
        </label>
      )}
      <div className={styles.actions}>
        <Button
          variant="secondary"
          disabled={busy || offset === null}
          onClick={() => void loadHistory()}
        >
          Load older package operations
        </Button>
        {!!history.length && (
          <label>
            History
            <select
              value={job?.id || ''}
              disabled={busy}
              onChange={(event) => {
                void workspaceRequest<Job>(
                  projectId,
                  `packages/jobs/${event.target.value}`,
                )
                  .then((value) => {
                    setJob(value);
                    setConfirmed(false);
                  })
                  .catch((reason) => setError(reason.message));
              }}
            >
              <option value="">Select operation</option>
              {history.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.requirement} · {item.status} · {item.id.slice(0, 8)}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>
      {error && (
        <p className={styles.error} role="alert">
          {error}
        </p>
      )}
      {data && !data.installer.available && (
        <p className={styles.info}>{data.installer.reason}</p>
      )}
      <form onSubmit={preview} className={styles.editor}>
        <h3>Add a package</h3>
        <div className={styles.grid}>
          <div>
            <label htmlFor="package-name">Package name</label>
            <Input
              id="package-name"
              required
              value={name}
              disabled={running}
              placeholder="e.g. httpx"
              onChange={(event) => setName(event.target.value)}
            />
          </div>
          <div>
            <label htmlFor="package-version">Version (optional)</label>
            <Input
              id="package-version"
              value={version}
              disabled={running}
              placeholder="e.g. 0.28.1"
              onChange={(event) => setVersion(event.target.value)}
            />
          </div>
        </div>
        <div className={styles.actions}>
          <p>Review resolves a plan; it does not install packages.</p>
          <Button
            disabled={running || !data?.installer.available}
            type="submit"
          >
            {running ? 'Working…' : 'Review package'}
          </Button>
        </div>
      </form>
      {job && (
        <div className={styles.job}>
          <div className={styles.listHeading}>
            <h3>{job.requirement}</h3>
            <span className={styles.badge}>{job.status}</span>
          </div>
          <p role="status">{job.message}</p>
          {job.packages && (
            <ul>
              {job.packages.map((item) => (
                <li key={item.name}>
                  <code>{item.name}</code>
                  <span>
                    {item.version} · {item.action || 'install'}
                  </span>
                </li>
              ))}
            </ul>
          )}
          {job.status === 'review' && (
            <>
              <label className={styles.confirm}>
                <input
                  type="checkbox"
                  checked={confirmed}
                  onChange={(event) => setConfirmed(event.target.checked)}
                />
                I reviewed this plan and want to install it into the shared
                environment.
              </label>
              <Button
                disabled={!confirmed || running}
                onClick={() => void install()}
              >
                Install reviewed packages
              </Button>
            </>
          )}
          {job.log && (
            <details>
              <summary>Operation log</summary>
              <pre>{job.log}</pre>
            </details>
          )}
        </div>
      )}
      <div className={styles.listHeading}>
        <h3>
          Installed packages {data && `(${Object.keys(data.installed).length})`}
        </h3>
        <button
          className={styles.ghost}
          disabled={running}
          onClick={() => setAttempt((count) => count + 1)}
        >
          <RefreshCw size={14} />
          Refresh
        </button>
      </div>
      <Input
        aria-label="Search installed packages"
        type="search"
        placeholder="Search installed packages…"
        value={search}
        onChange={(event) => setSearch(event.target.value)}
      />
      <div className={styles.inventory}>
        {installed.map(([packageName, packageVersion]) => (
          <div key={packageName}>
            <code>{packageName}</code>
            <span>{packageVersion}</span>
          </div>
        ))}
      </div>
      {data && <p className={styles.caption}>Environment: {data.prefix}</p>}
      {data?.scan && (
        <details className={styles.scan}>
          <summary>
            Import inspection · {data.scan.issues.length} issues
          </summary>
          <pre>{JSON.stringify(data.scan, null, 2)}</pre>
        </details>
      )}
    </section>
  );
}
