import { useEffect, useState, type FormEvent } from 'react';
import { Trash2, RefreshCw, LockKeyhole } from 'lucide-react';
import Input from '@/components/ui/Input/Input';
import Button from '@/components/ui/Button/Button';
import { workspaceRequest } from '@/lib/chat';
import styles from './RuntimePanels.module.css';

type Environment = { revision: string; names: string[] };

export default function EnvironmentPanel({ projectId }: { projectId: string }) {
  const [data, setData] = useState<Environment | null>(null);
  const [name, setName] = useState('');
  const [value, setValue] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let active = true;
    workspaceRequest<Environment>(projectId, 'environment')
      .then((result) => {
        if (active) {
          setData(result);
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
  async function write(event: FormEvent) {
    event.preventDefault();
    if (!data || busy) return;
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const result = await workspaceRequest<Environment>(
        projectId,
        `environment/${encodeURIComponent(name.trim())}`,
        'PUT',
        { expected_revision: data.revision, value },
      );
      setData(result);
      setValue('');
      setName('');
      setNotice(
        'Variable saved. Restart the project worker to use updated values.',
      );
    } catch (err) {
      setValue('');
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function remove(variable: string) {
    if (
      !data ||
      busy ||
      !window.confirm(`Remove ${variable} from this project's environment?`)
    )
      return;
    setBusy(true);
    setError('');
    try {
      setData(
        await workspaceRequest<Environment>(
          projectId,
          `environment/${variable}`,
          'DELETE',
          { expected_revision: data.revision },
        ),
      );
      setNotice('Variable removed. Restart the project worker to apply.');
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className={styles.panel}>
      <header className={styles.heading}>
        <div>
          <h2>Environment variables</h2>
        </div>
        <LockKeyhole size={20} />
      </header>
      <div className={styles.info}>
        Values are hidden after saving. Restart the API to apply changes.
      </div>
      {error && (
        <p className={styles.error} role="alert">
          {error}
        </p>
      )}
      {notice && (
        <p className={styles.success} role="status">
          {notice}
        </p>
      )}
      <div className={styles.listHeading}>
        <h3>
          {data
            ? `${data.names.length} ${data.names.length === 1 ? 'variable' : 'variables'}`
            : 'Loading variables…'}
        </h3>
        <button
          type="button"
          className={styles.ghost}
          disabled={busy}
          onClick={() => setAttempt((count) => count + 1)}
        >
          <RefreshCw size={14} />
          Refresh
        </button>
      </div>
      {data?.names.length === 0 && (
        <p className={styles.empty}>
          No variables yet. Add service URLs, feature flags, or secrets below.
        </p>
      )}
      {data?.names.map((variable) => (
        <div key={variable} className={styles.variable}>
          <code>{variable}</code>
          <span>••••••••</span>
          <Button
            type="button"
            variant="ghost"
            disabled={busy}
            onClick={() => {
              setName(variable);
              setValue('');
              document.getElementById('env-value')?.focus();
            }}
          >
            Replace
          </Button>
          <button
            type="button"
            disabled={busy}
            className={styles.icon}
            aria-label={`Remove ${variable}`}
            onClick={() => void remove(variable)}
          >
            <Trash2 size={16} />
          </button>
        </div>
      ))}
      <div className={styles.actions}>
        <Button
          type="button"
          variant="secondary"
          disabled={busy || !data || !/^[A-Za-z_][A-Za-z0-9_]*$/.test(name)}
          onClick={() => {
            if (!data) return;
            setBusy(true);
            setError('');
            void workspaceRequest<Environment>(
              projectId,
              `environment/${encodeURIComponent(name)}/generate`,
              'POST',
              { expected_revision: data.revision },
            )
              .then((result) => {
                setData(result);
                setValue('');
                setNotice(
                  'Secret generated and stored. Restart worker to use it.',
                );
              })
              .catch((reason) => setError(reason.message))
              .finally(() => setBusy(false));
          }}
        >
          Generate and save secret for entered name
        </Button>
      </div>
      <form className={styles.editor} onSubmit={write}>
        <h3>Add or replace a variable</h3>
        <div className={styles.grid}>
          <div>
            <label htmlFor="env-name">Name</label>
            <Input
              id="env-name"
              value={name}
              disabled={busy}
              required
              maxLength={64}
              pattern="[A-Za-z_][A-Za-z0-9_]*"
              onChange={(event) => setName(event.target.value)}
              placeholder="SERVICE_API_KEY"
            />
          </div>
          <div>
            <label htmlFor="env-value">New value</label>
            <Input
              id="env-value"
              type="password"
              autoComplete="off"
              disabled={busy}
              required
              value={value}
              onChange={(event) => setValue(event.target.value)}
              placeholder="Enter a new value"
            />
          </div>
        </div>
        <div className={styles.actions}>
          <p>Existing names are replaced.</p>
          <Button
            disabled={busy || !data || !name.trim() || !value}
            type="submit"
          >
            {busy ? 'Saving…' : 'Save variable'}
          </Button>
        </div>
      </form>
    </section>
  );
}
