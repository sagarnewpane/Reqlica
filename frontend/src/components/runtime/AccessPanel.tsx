import { useState } from 'react';
import Button from '@/components/ui/Button/Button';
import { useResource } from '@/components/workspace/use-resource';
import s from '@/components/workspace/Workspace.module.css';
type Auth = {
  revision: string;
  mode: string;
  header_name: string;
  credentials: { name: string; expires_at?: string }[];
};
type Cors = {
  revision: string;
  settings?: CorsSettings;
} & Partial<CorsSettings>;
type CorsSettings = {
  enabled: boolean;
  allow_origins: string[];
  allow_methods: string[];
  allow_headers: string[];
  expose_headers: string[];
  allow_credentials: boolean;
  max_age: number;
};
export default function AccessPanel({ projectId }: { projectId: string }) {
  const auth = useResource<Auth>(projectId, 'mock-auth');
  const cors = useResource<Cors>(projectId, 'cors');
  const [mode, setMode] = useState('bearer');
  const [header, setHeader] = useState('X-API-Key');
  const [credentials, setCredentials] = useState([
    { name: 'developer', value: '', expires_at: '' },
  ]);
  const [replace, setReplace] = useState(false);
  const [notice, setNotice] = useState('');
  const [editing, setEditing] = useState<CorsSettings | null>(null);
  const current =
    editing ||
    cors.data?.settings ||
    (cors.data?.enabled !== undefined ? (cors.data as CorsSettings) : null);
  return (
    <div className={s.stack}>
      <section className={s.panel}>
        <h2>Mock API authentication</h2>
        <p>
          Current mode: {auth.data?.mode || 'loading'}. Saved credentials:{' '}
          {auth.data?.credentials.map((c) => c.name).join(', ') || 'none'}.
          Credential values are never returned.
        </p>
        <form
          onSubmit={(event) => {
            event.preventDefault();
            if (!auth.data) return;
            void auth
              .action('mock-auth', 'PUT', {
                expected_revision: auth.data.revision,
                mode,
                header_name: header,
                credentials:
                  mode === 'none'
                    ? []
                    : credentials.map((c) => ({
                        name: c.name,
                        value: c.value,
                        ...(c.expires_at
                          ? { expires_at: new Date(c.expires_at).toISOString() }
                          : {}),
                      })),
              })
              .then(() => {
                setCredentials([
                  { name: 'developer', value: '', expires_at: '' },
                ]);
                setReplace(false);
                setNotice('Authentication saved.');
              })
              .catch(() => {});
          }}
        >
          <div className={s.grid}>
            <label>
              New mode
              <select value={mode} onChange={(e) => setMode(e.target.value)}>
                <option value="none">None</option>
                <option value="bearer">Bearer token</option>
                <option value="api_key">API key header</option>
              </select>
            </label>
            {mode === 'api_key' && (
              <label>
                Header name
                <input
                  value={header}
                  onChange={(e) => setHeader(e.target.value)}
                  required
                />
              </label>
            )}
          </div>
          {mode !== 'none' && (
            <>
              {credentials.map((credential, index) => (
                <div className={s.grid} key={index}>
                  <label>
                    Credential name
                    <input
                      value={credential.name}
                      required
                      onChange={(e) =>
                        setCredentials(
                          credentials.map((c, i) =>
                            i === index ? { ...c, name: e.target.value } : c,
                          ),
                        )
                      }
                    />
                  </label>
                  <label>
                    New token
                    <input
                      type="password"
                      autoComplete="new-password"
                      value={credential.value}
                      required
                      onChange={(e) =>
                        setCredentials(
                          credentials.map((c, i) =>
                            i === index ? { ...c, value: e.target.value } : c,
                          ),
                        )
                      }
                    />
                  </label>
                  <label>
                    Expires (optional)
                    <input
                      type="datetime-local"
                      value={credential.expires_at}
                      onChange={(e) =>
                        setCredentials(
                          credentials.map((c, i) =>
                            i === index
                              ? { ...c, expires_at: e.target.value }
                              : c,
                          ),
                        )
                      }
                    />
                  </label>
                  {credentials.length > 1 && (
                    <Button
                      type="button"
                      variant="secondary"
                      onClick={() =>
                        setCredentials(
                          credentials.filter((_, i) => i !== index),
                        )
                      }
                    >
                      Remove credential
                    </Button>
                  )}
                </div>
              ))}
              <Button
                type="button"
                variant="secondary"
                onClick={() =>
                  setCredentials([
                    ...credentials,
                    { name: '', value: '', expires_at: '' },
                  ])
                }
              >
                Add credential
              </Button>
            </>
          )}
          <label className={s.check}>
            <input
              type="checkbox"
              checked={replace}
              onChange={(e) => setReplace(e.target.checked)}
            />
            Replace all existing mock credentials with these settings
          </label>
          <div className={s.row}>
            <Button
              disabled={!auth.data || auth.busy || !replace}
              type="submit"
            >
              Save authentication
            </Button>
            <Button type="button" variant="secondary" onClick={auth.refresh}>
              Refresh
            </Button>
          </div>
        </form>
        {auth.error && <p role="alert">{auth.error}</p>}
      </section>
      <section className={s.panel}>
        <h2>Browser access / CORS</h2>
        <p>Control which browser origins may call the published mock API.</p>
        {current && (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              if (cors.data)
                void cors
                  .action('cors', 'PUT', {
                    ...current,
                    expected_revision: cors.data.revision,
                  })
                  .then(() => {
                    setEditing(null);
                    setNotice('CORS saved.');
                  })
                  .catch(() => {});
            }}
          >
            <label className={s.check}>
              <input
                type="checkbox"
                checked={current.enabled}
                onChange={(e) =>
                  setEditing({ ...current, enabled: e.target.checked })
                }
              />
              Enable CORS
            </label>
            <div className={s.grid}>
              {(
                [
                  'allow_origins',
                  'allow_methods',
                  'allow_headers',
                  'expose_headers',
                ] as const
              ).map((key) => (
                <label key={key}>
                  {key.replaceAll('_', ' ')}
                  <textarea
                    value={current[key].join('\n')}
                    onChange={(e) =>
                      setEditing({
                        ...current,
                        [key]: e.target.value.split('\n').filter(Boolean),
                      })
                    }
                  />
                </label>
              ))}
            </div>
            <label className={s.check}>
              <input
                type="checkbox"
                checked={current.allow_credentials}
                onChange={(e) =>
                  setEditing({
                    ...current,
                    allow_credentials: e.target.checked,
                  })
                }
              />
              Allow browser credentials
            </label>
            <label>
              Preflight cache (seconds)
              <input
                type="number"
                min={0}
                max={86400}
                value={current.max_age}
                onChange={(e) =>
                  setEditing({ ...current, max_age: Number(e.target.value) })
                }
              />
            </label>
            <div className={s.row}>
              <Button type="submit" disabled={cors.busy}>
                Save CORS
              </Button>
              <Button
                type="button"
                variant="secondary"
                onClick={() => {
                  setEditing(null);
                  cors.refresh();
                }}
              >
                Reload saved CORS
              </Button>
            </div>
          </form>
        )}
        {cors.error && <p role="alert">{cors.error}</p>}
      </section>
      {notice && <p role="status">{notice}</p>}
    </div>
  );
}
