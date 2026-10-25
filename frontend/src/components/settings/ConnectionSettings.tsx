import { useEffect, useState } from 'react';
import Button from '@/components/ui/Button/Button';
import Input from '@/components/ui/Input/Input';
import Select from '@/components/ui/Select/Select';
import Card from '@/components/ui/Card/Card';
import s from './Settings.module.css';
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
    fetch('/api/ui/connection')
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
  async function request() {
    setBusy(true);
    setError('');
    try {
      const reply = await fetch('/api/ui/connection?health=1');
      const data = await reply.json();
      if (!reply.ok) throw new Error(data.error);
      setConsoleUrl(data.console);
      setRuntime(data.runtime);
      setHealth(data.health || {});
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className={s.form}>
      <Card className={s.section}>
        <div className={s.sectionHeading}>
          <div>
            <h2>Connections and appearance</h2>
            <p>
              Service addresses come from the running Reqlica configuration.
              Restart Reqlica after changing its host or port settings.
            </p>
          </div>
        </div>
        <div className={s.grid}>
          <div>
            <label htmlFor="console-url">Console URL</label>
            <Input id="console-url" type="url" value={consoleUrl} readOnly />
            <p>{health.console || 'Not checked'}</p>
          </div>
          <div>
            <label htmlFor="runtime-url">Runtime URL</label>
            <Input id="runtime-url" type="url" value={runtime} readOnly />
            <p>{health.runtime || 'Not checked'}</p>
          </div>
          <div>
            <label htmlFor="theme">Theme</label>
            <Select
              id="theme"
              value={theme}
              onValueChange={(choice) => {
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
            </Select>
            <p>Applies immediately in this browser.</p>
          </div>
        </div>
        <div className={s.connectionActions}>
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
        {error && (
          <p className={s.error} role="alert">
            {error}
          </p>
        )}
        {notice && (
          <p className={s.notice} role="status">
            {notice}
          </p>
        )}
      </Card>
    </div>
  );
}
