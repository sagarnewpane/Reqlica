import { useEffect, useState, type FormEvent } from 'react';
import Link from 'next/link';
import Button from '@/components/ui/Button/Button';
import Input from '@/components/ui/Input/Input';
import Select from '@/components/ui/Select/Select';
import { workspaceRequest } from '@/lib/chat';
import {
  requestHeaders,
  requestCurl,
  type RequestConfig,
} from '@/lib/request-execution';
import SavedItems from '@/components/workspace/SavedItems';
import RequestChains from './RequestChains';
import { sample } from '@/components/endpoints/ContractViewer';
import type { Endpoint, Project } from '@/lib/projects';
import { draftLabel, orderedDrafts, type Draft } from '@/lib/drafts';
import { REQUEST_METHODS, resolveTesterEndpoint } from '@/lib/endpoints';
import styles from '@/components/runtime/RuntimePanels.module.css';
import testerStyles from './RequestTester.module.css';

function formatResponseBody(body: string) {
  try {
    return JSON.stringify(JSON.parse(body), null, 2);
  } catch {
    return body || '(empty response)';
  }
}

type ResponseData = {
  status: number;
  duration: number;
  body: string;
  headers: Record<string, string>;
};
export default function RequestTester({
  projectId,
  initialMethod,
  initialPath,
  initialDraftId,
}: {
  projectId: string;
  initialMethod?: string;
  initialPath?: string;
  initialDraftId?: string;
}) {
  const [project, setProject] = useState<Project | null>(null);
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [draftId, setDraftId] = useState(initialDraftId || '');
  const [draft, setDraft] = useState<Draft | null>(null);
  const [draftEndpoints, setDraftEndpoints] = useState<Endpoint[]>([]);
  const [method, setMethod] = useState('GET');
  const [path, setPath] = useState('/');
  const [body, setBody] = useState('');
  const [headerText, setHeaderText] = useState('');
  const [notice, setNotice] = useState('');
  const [result, setResult] = useState<ResponseData | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [loadingTarget, setLoadingTarget] = useState(false);
  useEffect(() => {
    let active = true;
    Promise.allSettled([
      workspaceRequest<Project>(projectId, 'endpoints'),
      workspaceRequest<{ drafts: Draft[] }>(projectId, 'drafts'),
    ]).then(([published, listed]) => {
      if (!active) return;
      if (listed.status === 'fulfilled') setDrafts(listed.value.drafts);
      if (published.status === 'fulfilled') {
        const value = published.value;
        setProject(value);
        if (!initialDraftId) {
          const first = resolveTesterEndpoint(
            value.endpoints,
            initialMethod,
            initialPath,
          );
          if (first) {
            setMethod(first.method);
            setPath(first.path);
          } else if (initialMethod || initialPath)
            setError(
              'The linked endpoint is unavailable. Choose a route or enter a supported request manually.',
            );
        }
      } else if (!initialDraftId) setError((published.reason as Error).message);
      if (initialDraftId && initialMethod && initialPath) {
        setMethod(initialMethod);
        setPath(initialPath);
      }
      if (listed.status === 'rejected' && initialDraftId)
        setError((listed.reason as Error).message);
    });
    return () => {
      active = false;
    };
  }, [projectId, initialDraftId, initialMethod, initialPath]);
  useEffect(() => {
    let active = true;
    if (!draftId) return;
    Promise.resolve().then(async () => {
      if (!active) return;
      setLoadingTarget(true);
      setDraft(null);
      setDraftEndpoints([]);
      try {
        const current = await workspaceRequest<Draft>(
          projectId,
          `drafts/${draftId}`,
        );
        if (!active) return;
        setDraft(current);
        try {
          const contract = await workspaceRequest<{
            paths?: Record<string, Record<string, unknown>>;
          }>(
            projectId,
            `drafts/${draftId}/openapi.json?revision=${current.revision}`,
          );
          if (!active) return;
          const endpoints = Object.entries(contract.paths || {}).flatMap(
            ([route, methods]) =>
              Object.keys(methods)
                .map((verb) => ({ method: verb.toUpperCase(), path: route }))
                .filter((endpoint) =>
                  REQUEST_METHODS.includes(endpoint.method),
                ),
          );
          setDraftEndpoints(endpoints);
          if (!(initialDraftId === draftId && initialMethod && initialPath)) {
            const first = resolveTesterEndpoint(endpoints);
            if (first) {
              setMethod(first.method);
              setPath(first.path);
            }
          }
        } catch {
          // A draft can be called before it has a generated OpenAPI contract.
        }
        setError('');
      } catch (err) {
        if (active) setError((err as Error).message);
      } finally {
        if (active) setLoadingTarget(false);
      }
    });
    return () => {
      active = false;
    };
  }, [projectId, draftId, initialDraftId, initialMethod, initialPath]);
  async function send(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError('');
    setResult(null);
    try {
      if (draftId) {
        if (!draft) throw new Error('Draft is still loading. Try again.');
        let jsonBody: unknown = null;
        if (body.trim()) {
          try {
            jsonBody = JSON.parse(body);
          } catch {
            throw new Error('Draft request body must be valid JSON.');
          }
        }
        const headers = Object.fromEntries(
          requestHeaders(projectId, headerText)
            .filter((row) => row.enabled && row.name.trim())
            .map((row) => [row.name.trim(), row.value]),
        );
        const started = performance.now();
        const response = await workspaceRequest<{
          status: string;
          status_code?: number;
          body?: unknown;
          headers?: Record<string, string>;
          output?: string;
        }>(projectId, `drafts/${draft.id}/call`, 'POST', {
          expected_revision: draft.revision,
          method,
          path,
          json_body: jsonBody,
          headers,
        });
        if (response.status !== 'passed')
          throw new Error(response.output || `Draft check ${response.status}.`);
        setResult({
          status: response.status_code ?? 0,
          duration: Math.round(performance.now() - started),
          body:
            typeof response.body === 'string'
              ? response.body
              : JSON.stringify(response.body ?? null),
          headers: response.headers || {},
        });
      } else {
        setResult(
          await workspaceRequest<ResponseData>(projectId, 'request', 'POST', {
            method,
            path,
            body,
            headers: requestHeaders(projectId, headerText),
          }),
        );
      }
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const endpoints = draftId ? draftEndpoints : project?.endpoints || [];
  const selectedEndpointIndex = endpoints.findIndex(
    (endpoint) => endpoint.method === method && endpoint.path === path,
  );

  function selectEndpoint(value: string) {
    const endpoint = endpoints[Number(value)];
    if (!endpoint) return;
    setMethod(endpoint.method);
    setPath(endpoint.path);
  }

  const current: RequestConfig = { method, path, body, headerText, draftId };
  async function copyRequest() {
    try {
      const reply = await fetch('/api/connection');
      const connection = await reply.json();
      if (!reply.ok) throw new Error(connection.error);
      if (draftId)
        throw new Error(
          'cURL export targets the published API. Select Published API first.',
        );
      await navigator.clipboard.writeText(
        requestCurl(
          projectId,
          current,
          `${connection.runtime}/mock/${projectId}`,
        ),
      );
      setNotice('cURL copied. Includes enabled headers.');
    } catch (reason) {
      setError((reason as Error).message);
    }
  }
  async function insertExample() {
    try {
      const doc = await workspaceRequest<{
        paths?: Record<string, Record<string, unknown>>;
      }>(
        projectId,
        draftId
          ? `drafts/${draftId}/openapi.json${draft ? `?revision=${draft.revision}` : ''}`
          : 'openapi.json',
      );
      const endpoint = endpoints[selectedEndpointIndex];
      const pattern = (endpoint?.path || path).replace(
        /:([A-Za-z_][A-Za-z0-9_]*)}/g,
        '}',
      );
      const operation = doc.paths?.[pattern]?.[method.toLowerCase()] as
        | {
            requestBody?: {
              content?: Record<
                string,
                {
                  example?: unknown;
                  examples?: Record<string, { value?: unknown }>;
                  schema?: Record<string, unknown>;
                }
              >;
            };
          }
        | undefined;
      const media = operation?.requestBody?.content?.['application/json'];
      if (!media)
        throw new Error(
          'This operation has no JSON request-body example or schema.',
        );
      const example =
        media.example ??
        Object.values(media.examples || {})[0]?.value ??
        sample(media.schema, doc as Parameters<typeof sample>[1]);
      setBody(JSON.stringify(example, null, 2));
      if (
        !requestHeaders(projectId, headerText).some(
          (h) => h.enabled && h.name.toLowerCase() === 'content-type',
        )
      )
        setHeaderText(
          (value) =>
            `${value}${value ? '\n' : ''}Content-Type: application/json`,
        );
      setNotice('Example inserted. Review values before sending.');
    } catch (reason) {
      setError((reason as Error).message);
    }
  }
  return (
    <>
      <div className={styles.panel}>
        <h2>Send a request</h2>
        <label htmlFor="request-target">Target</label>
        <Select
          id="request-target"
          value={draftId}
          disabled={busy}
          onValueChange={(next) => {
            setDraftId(next);
            setDraft(null);
            setDraftEndpoints([]);
            setLoadingTarget(false);
            setResult(null);
            setError('');
            if (!next) {
              const first = resolveTesterEndpoint(project?.endpoints || []);
              if (first) {
                setMethod(first.method);
                setPath(first.path);
              }
            }
          }}
        >
          <option value="">Published API · live data</option>
          {orderedDrafts(drafts).map((item) => (
            <option key={item.id} value={item.id}>
              {draftLabel(item)} · disposable data
            </option>
          ))}
          {draftId && !drafts.some((item) => item.id === draftId) && (
            <option value={draftId}>Selected draft · loading</option>
          )}
        </Select>
        <p>
          {draftId
            ? 'Draft requests run project code against a disposable database copy. Writes are discarded.'
            : 'Requests run against this project’s live API and may change its data.'}
        </p>
        {draftId && draft && (
          <p>
            Revision: <code>{draft.revision.slice(0, 12)}</code>.{' '}
            {draftEndpoints.length
              ? `${draftEndpoints.length} routes from the contract.`
              : 'Enter a method and path manually; validate the draft to list routes.'}
          </p>
        )}
        <Link
          className={styles.link}
          href={{ pathname: '/runtime', query: { projectId, tab: 'headers' } }}
        >
          Request headers →
        </Link>
        <SavedItems
          projectId={projectId}
          kind="request"
          disabled={busy}
          data={{ method, path, body, draftId }}
          onLoad={(value) => {
            const saved = value as RequestConfig;
            if (
              ![
                'GET',
                'POST',
                'PUT',
                'PATCH',
                'DELETE',
                'HEAD',
                'OPTIONS',
              ].includes(saved.method) ||
              typeof saved.path !== 'string' ||
              !saved.path.startsWith('/') ||
              typeof saved.body !== 'string'
            )
              throw new Error('Invalid saved request.');
            setDraftId(saved.draftId || '');
            setMethod(saved.method);
            setPath(saved.path);
            setBody(saved.body);
            setHeaderText('');
          }}
        />
        <form onSubmit={send}>
          <label htmlFor="saved-endpoint">Choose an endpoint</label>
          <Select
            id="saved-endpoint"
            value={
              selectedEndpointIndex < 0 ? '' : String(selectedEndpointIndex)
            }
            disabled={busy}
            onValueChange={selectEndpoint}
          >
            <option value="" disabled>
              Choose a configured route…
            </option>
            {endpoints.map(
              (endpoint, index) =>
                REQUEST_METHODS.includes(endpoint.method) && (
                  <option key={index} value={index}>
                    {endpoint.method} {endpoint.path}
                  </option>
                ),
            )}
          </Select>
          <div className={testerStyles.requestLine}>
            <div>
              <label htmlFor="method">Method</label>
              <Select
                id="method"
                value={method}
                disabled={busy}
                onValueChange={(nextValue) => setMethod(nextValue)}
              >
                {REQUEST_METHODS.map((item) => (
                  <option key={item}>{item}</option>
                ))}
              </Select>
            </div>
            <div>
              <label htmlFor="request-path">Path and query</label>
              <Input
                id="request-path"
                required
                value={path}
                disabled={busy}
                onChange={(event) => setPath(event.target.value)}
                placeholder="/hello"
              />
            </div>
          </div>
          {/\{[^}]+\}/.test(path) && (
            <p role="status">
              Replace path parameters with real values before sending this
              request.
            </p>
          )}
          {!['GET', 'HEAD'].includes(method) && (
            <div className={testerStyles.requestFields}>
              <label htmlFor="request-body">Request body</label>
              <textarea
                id="request-body"
                rows={6}
                disabled={busy}
                value={body}
                onChange={(event) => setBody(event.target.value)}
                className={testerStyles.requestBody}
              />
              <p>
                Add Content-Type in request headers when sending JSON. Replace
                path parameters with actual values.
              </p>
            </div>
          )}
          <label htmlFor="inline-headers">
            Request header overrides (one Header: value per line)
          </label>
          <textarea
            id="inline-headers"
            rows={3}
            value={headerText}
            disabled={busy}
            onChange={(event) => setHeaderText(event.target.value)}
            placeholder="Content-Type: application/json"
          />
          <p>
            Overrides apply to this request. Saved requests omit header values.
          </p>
          <div className={styles.actions}>
            <Button
              type="button"
              variant="secondary"
              disabled={busy || loadingTarget}
              onClick={() => void insertExample()}
            >
              Insert contract example
            </Button>
            <Button
              type="button"
              variant="secondary"
              disabled={busy || !!draftId}
              onClick={() => void copyRequest()}
            >
              Copy cURL
            </Button>
          </div>
          <div className={styles.actions}>
            <p>Redirects are not followed.</p>
            <Button
              disabled={busy || loadingTarget || (draftId ? !draft : !project)}
              type="submit"
            >
              {busy ? 'Sending…' : 'Send request'}
            </Button>
          </div>
        </form>
      </div>
      {error && (
        <p role="alert" className={styles.error}>
          {error}
        </p>
      )}
      {notice && <p role="status">{notice}</p>}
      {result && (
        <section className={`${styles.panel} ${testerStyles.response}`}>
          <div className={styles.heading}>
            <h2>Response · {result.status}</h2>
            <span>{result.duration} ms</span>
          </div>
          <Button
            variant="secondary"
            onClick={() => {
              void navigator.clipboard
                .writeText(result.body)
                .then(() => setNotice('Response copied.'))
                .catch(() =>
                  setError(
                    'Clipboard unavailable. Select and copy the response.',
                  ),
                );
            }}
          >
            Copy response
          </Button>
          <pre>{formatResponseBody(result.body)}</pre>
          <details>
            <summary>Response headers</summary>
            <pre>{JSON.stringify(result.headers, null, 2)}</pre>
          </details>
        </section>
      )}
      <RequestChains projectId={projectId} current={current} />
    </>
  );
}
