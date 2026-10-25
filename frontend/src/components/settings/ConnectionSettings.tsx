import { useEffect, useState } from 'react';
import Button from '@/components/ui/Button/Button';
import s from '@/components/workspace/Workspace.module.css';
export default function ConnectionSettings({
  projectId,
}: {
  projectId?: string;
}) {
  const [consoleUrl, setConsoleUrl] = useState('');
  const [runtime, setRuntime] = useState('');
  const [health, setHealth] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [theme, setTheme] = useState('system');
  useEffect(() => {
    let active = true;
    Promise.resolve().then(() => {
      try {
        if (active) setTheme(localStorage.getItem('reqlica:theme') || 'system');
      } catch {}
    });
    fetch('/api/connection')
      .then((reply) => reply.json())
      .then((data) => {
        if (active) {
          setConsoleUrl(data.console);
          setRuntime(data.runtime);
        }
      })
      .catch(() => {
        if (active) setError('Cannot read connection settings.');
      });
    return () => {
      active = false;
    };
  }, []);
  async function request(save = false) {
    setBusy(true);
    setError('');
    try {
      const reply = await fetch(
        `/api/connection${save ? '' : '?health=1'}`,
        save
          ? {
              method: 'PUT',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ console: consoleUrl, runtime }),
            }
          : undefined,
      );
      const data = await reply.json();
      if (!reply.ok) throw new Error(data.error);
      setConsoleUrl(data.console);
      setRuntime(data.runtime);
      setHealth(data.health || {});
      if (save)
        setNotice('Connection settings saved for this dashboard server.');
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className={s.panel}>
      <h2>Connections and appearance</h2>
      <p>
        Configure the local console and mock runtime used by this dashboard.
      </p>
      <div className={s.grid}>
        <label>
          Console URL
          <input
            value={consoleUrl}
            disabled={busy}
            onChange={(e) => setConsoleUrl(e.target.value)}
          />
          {health.console || 'Not checked'}
        </label>
        <label>
          Runtime URL
          <input
            value={runtime}
            disabled={busy}
            onChange={(e) => setRuntime(e.target.value)}
          />
          {health.runtime || 'Not checked'}
        </label>
        <label>
          Theme
          <select
            value={theme}
            onChange={(e) => {
              const choice = e.target.value;
              try {
                localStorage.setItem('reqlica:theme', choice);
              } catch {
                setError(
                  'Theme applies now, but browser storage is unavailable.',
                );
              }
              setTheme(choice);
              document.documentElement.classList.toggle(
                'dark',
                choice === 'dark' ||
                  (choice === 'system' &&
                    matchMedia('(prefers-color-scheme: dark)').matches),
              );
              window.dispatchEvent(new Event('reqlica:theme'));
            }}
          >
            <option value="system">System</option>
            <option value="light">Light</option>
            <option value="dark">Dark</option>
          </select>
        </label>
      </div>
      <div className={s.row}>
        <Button
          disabled={busy || !consoleUrl || !runtime}
          onClick={() => void request(true)}
        >
          Save connections
        </Button>
        <Button
          variant="secondary"
          disabled={busy}
          onClick={() => void request()}
        >
          Check connection health
        </Button>
        {projectId && (
          <Button
            variant="secondary"
            onClick={() => {
              void navigator.clipboard
                .writeText(`${runtime}/mock/${projectId}`)
                .then(() => setNotice('Published API base URL copied.'))
                .catch(() => setError('Clipboard unavailable.'));
            }}
          >
            Copy API base URL
          </Button>
        )}
      </div>
      {error && <p role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}
    </section>
  );
}
