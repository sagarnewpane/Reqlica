import { useState } from 'react';
import Button from '@/components/ui/Button/Button';
import { useResource } from '@/components/workspace/use-resource';
import s from '@/components/workspace/Workspace.module.css';
type Rule = {
  method: string;
  path: string;
  delay_ms: number;
  response: string;
  status_code: number;
  body?: unknown;
  failure_count?: number | null;
  rate_limit?: {
    requests: number;
    window_seconds: number;
    scope: string;
  } | null;
};
type Scenarios = {
  revision: string;
  rules: Rule[];
  remaining_failures?: Record<string, number>;
  rate_buckets?: unknown;
};
export default function TrafficPanel({ projectId }: { projectId: string }) {
  const resource = useResource<Scenarios>(projectId, 'scenarios');
  const [rule, setRule] = useState<Rule>({
    method: 'GET',
    path: '/',
    delay_ms: 0,
    response: 'passthrough',
    status_code: 503,
  });
  const [body, setBody] = useState('{"detail":"simulated_failure"}');
  const [requests, setRequests] = useState(0);
  const [windowSeconds, setWindow] = useState(60);
  const [scope, setScope] = useState('route');
  const [notice, setNotice] = useState('');
  async function save(rules: Rule[]) {
    if (!resource.data) return;
    try {
      await resource.action('scenarios', 'PUT', {
        expected_revision: resource.data.revision,
        rules,
      });
      setNotice('Traffic rules saved.');
    } catch {}
  }
  return (
    <section className={s.panel}>
      <h2>Traffic scenarios</h2>
      <p>
        Match registered route patterns. Test-user limits use
        X-Reqlica-Test-User. Saving rules resets their counters.
      </p>
      <div className={s.row}>
        <Button variant="secondary" onClick={resource.refresh}>
          Refresh
        </Button>
      </div>
      <ul className={s.list}>
        {resource.data?.rules.map((item, index) => (
          <li className={s.item} key={`${item.method}:${item.path}`}>
            <strong>
              {item.method} {item.path}
            </strong>
            <p>
              {item.delay_ms} ms · {item.response}
              {item.rate_limit
                ? ` · ${item.rate_limit.requests} requests / ${item.rate_limit.window_seconds}s (${item.rate_limit.scope})`
                : ''}
            </p>
            <div className={s.row}>
              <Button
                variant="secondary"
                disabled={resource.busy}
                onClick={() => {
                  setRule(item);
                  setBody(JSON.stringify(item.body ?? {}));
                  setRequests(item.rate_limit?.requests || 0);
                  setWindow(item.rate_limit?.window_seconds || 60);
                  setScope(item.rate_limit?.scope || 'route');
                }}
              >
                Edit
              </Button>
              <Button
                variant="secondary"
                disabled={resource.busy}
                onClick={() =>
                  void save(resource.data!.rules.filter((_, i) => i !== index))
                }
              >
                Remove rule
              </Button>
            </div>
          </li>
        ))}
      </ul>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          try {
            const next = {
              ...rule,
              body: JSON.parse(body),
              failure_count:
                rule.response === 'failure' ? rule.failure_count || null : null,
              rate_limit: requests
                ? { requests, window_seconds: windowSeconds, scope }
                : null,
            };
            void save([
              ...(resource.data?.rules || []).filter(
                (item) =>
                  item.method !== next.method || item.path !== next.path,
              ),
              next,
            ]);
          } catch {
            resource.setError('Enter valid JSON for the failure body.');
          }
        }}
      >
        <div className={s.grid}>
          <label>
            Method
            <select
              value={rule.method}
              onChange={(e) => setRule({ ...rule, method: e.target.value })}
            >
              {['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS'].map(
                (v) => (
                  <option key={v}>{v}</option>
                ),
              )}
            </select>
          </label>
          <label>
            Route pattern
            <input
              value={rule.path}
              onChange={(e) => setRule({ ...rule, path: e.target.value })}
              required
            />
          </label>
          <label>
            Delay (ms)
            <input
              type="number"
              min={0}
              max={30000}
              value={rule.delay_ms}
              onChange={(e) =>
                setRule({ ...rule, delay_ms: Number(e.target.value) })
              }
            />
          </label>
          <label>
            Response
            <select
              value={rule.response}
              onChange={(e) => setRule({ ...rule, response: e.target.value })}
            >
              {['passthrough', 'failure', 'empty'].map((v) => (
                <option key={v}>{v}</option>
              ))}
            </select>
          </label>
          {rule.response === 'failure' && (
            <>
              <label>
                Failure status
                <input
                  type="number"
                  min={400}
                  max={599}
                  value={rule.status_code}
                  onChange={(e) =>
                    setRule({ ...rule, status_code: Number(e.target.value) })
                  }
                />
              </label>
              <label>
                Failure count (blank: always)
                <input
                  type="number"
                  min={1}
                  value={rule.failure_count || ''}
                  onChange={(e) =>
                    setRule({
                      ...rule,
                      failure_count: e.target.value
                        ? Number(e.target.value)
                        : null,
                    })
                  }
                />
              </label>
            </>
          )}
          <label>
            Request limit (0: disabled)
            <input
              type="number"
              min={0}
              max={1000000}
              value={requests}
              onChange={(e) => setRequests(Number(e.target.value))}
            />
          </label>
          {requests > 0 && (
            <>
              <label>
                Window (seconds)
                <input
                  type="number"
                  min={1}
                  max={86400}
                  value={windowSeconds}
                  onChange={(e) => setWindow(Number(e.target.value))}
                />
              </label>
              <label>
                Limit scope
                <select
                  value={scope}
                  onChange={(e) => setScope(e.target.value)}
                >
                  <option value="route">Route</option>
                  <option value="test_user">Test user</option>
                </select>
              </label>
            </>
          )}
        </div>
        {rule.response === 'failure' && (
          <label>
            Failure JSON body
            <textarea value={body} onChange={(e) => setBody(e.target.value)} />
          </label>
        )}
        <div className={s.row}>
          <Button type="submit" disabled={!resource.data || resource.busy}>
            Save route rule
          </Button>
          <Button
            type="button"
            variant="secondary"
            disabled={!resource.data || resource.busy}
            onClick={() => {
              if (resource.data)
                void resource
                  .action('scenarios/reset', 'POST', {
                    expected_revision: resource.data.revision,
                    confirm: true,
                  })
                  .then(() => setNotice('Scenario counters reset.'))
                  .catch(() => {});
            }}
          >
            Reset counters
          </Button>
        </div>
      </form>
      {resource.error && <p role="alert">{resource.error}</p>}
      {notice && <p role="status">{notice}</p>}
    </section>
  );
}
