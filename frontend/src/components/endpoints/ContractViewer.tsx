import { useEffect, useState } from 'react';
import Link from 'next/link';
import Button from '@/components/ui/Button/Button';
import Select from '@/components/ui/Select/Select';
import { workspaceRequest } from '@/lib/chat';
import styles from './ContractViewer.module.css';

type Schema = Record<string, unknown>;
type Operation = {
  summary?: string;
  description?: string;
  parameters?: unknown[];
  requestBody?: {
    content?: Record<string, { schema?: Schema; example?: unknown }>;
  };
  responses?: Record<
    string,
    {
      description?: string;
      content?: Record<string, { schema?: Schema; example?: unknown }>;
    }
  >;
};
type Document = {
  'x-reqlica-revision'?: string;
  'x-reqlica-contract-warnings'?: {
    method?: string;
    path?: string;
    reason?: string;
  }[];
  info?: { title?: string; version?: string };
  paths?: Record<string, Record<string, Operation>>;
  components?: { schemas?: Record<string, Schema> };
};

export function sample(
  schema: Schema | undefined,
  document: Document,
  depth = 0,
): unknown {
  if (!schema || depth > 4) return null;
  if ('example' in schema) return schema.example;
  if ('default' in schema) return schema.default;
  const ref = schema.$ref;
  if (typeof ref === 'string' && ref.startsWith('#/components/schemas/'))
    return sample(
      document.components?.schemas?.[ref.slice(21)],
      document,
      depth + 1,
    );
  if (Array.isArray(schema.enum)) return schema.enum[0];
  if (schema.type === 'object' || schema.properties) {
    return Object.fromEntries(
      Object.entries((schema.properties || {}) as Record<string, Schema>).map(
        ([name, field]) => [name, sample(field, document, depth + 1)],
      ),
    );
  }
  if (schema.type === 'array')
    return [sample(schema.items as Schema, document, depth + 1)];
  if (schema.type === 'integer' || schema.type === 'number') return 0;
  if (schema.type === 'boolean') return false;
  return 'string';
}

export default function ContractViewer({
  projectId,
  draftId,
  revision,
}: {
  projectId: string;
  draftId?: string;
  revision?: string;
}) {
  const [document, setDocument] = useState<Document | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState('');
  const [attempt, setAttempt] = useState(0);
  const root = draftId ? `drafts/${draftId}/` : '';
  const schemaPath = `${root}openapi.json${revision ? `?revision=${revision}` : ''}`;
  const docsUrl = `/api/projects/${projectId}/${root}docs`;
  useEffect(() => {
    let active = true;
    Promise.resolve().then(async () => {
      if (!active) return;
      setLoading(true);
      setDocument(null);
      try {
        const data = await workspaceRequest<Document>(projectId, schemaPath);
        if (active) {
          setDocument(data);
          setError('');
          setSelected('');
        }
      } catch (err) {
        if (active) setError((err as Error).message);
      } finally {
        if (active) setLoading(false);
      }
    });
    return () => {
      active = false;
    };
  }, [projectId, schemaPath, attempt]);
  const operations = Object.entries(document?.paths || {}).flatMap(
    ([path, methods]) =>
      Object.entries(methods)
        .filter(([method]) =>
          ['get', 'post', 'put', 'patch', 'delete', 'options', 'head'].includes(
            method,
          ),
        )
        .map(([method, operation]) => ({
          key: `${method.toUpperCase()} ${path}`,
          method,
          path,
          operation,
        })),
  );
  const current =
    operations.find((item) => item.key === selected) || operations[0];
  const request = current?.operation.requestBody?.content;
  const requestMedia =
    request?.['application/json'] || Object.values(request || {})[0];
  return (
    <section
      className={styles.panel}
      aria-label={draftId ? 'Draft API contract' : 'Published API contract'}
    >
      <div className={styles.heading}>
        <div>
          <h2>{draftId ? 'Draft contract' : 'Published contract'}</h2>
          <p>
            Generated OpenAPI contract for this{' '}
            {draftId ? 'draft revision' : 'published API'}.
          </p>
        </div>
        <div className={styles.actions}>
          {document && (
            <>
              <a href={docsUrl} target="_blank" rel="noopener noreferrer">
                Open Swagger ↗
              </a>
              <a
                href={`/api/projects/${projectId}/${schemaPath}`}
                target="_blank"
                rel="noopener noreferrer"
              >
                Open JSON ↗
              </a>
              {(['json', 'yaml'] as const).map((format) => (
                <a
                  key={format}
                  href={`/api/projects/${projectId}/schema-export?${new URLSearchParams(
                    {
                      revision:
                        document['x-reqlica-revision'] || revision || '',
                      format,
                      ...(draftId ? { draft_id: draftId } : {}),
                    },
                  )}`}
                >
                  Download {format.toUpperCase()}
                </a>
              ))}
            </>
          )}
          <Button
            type="button"
            variant="secondary"
            disabled={loading}
            onClick={() => setAttempt((count) => count + 1)}
          >
            Refresh
          </Button>
        </div>
      </div>
      {document && (
        <p>
          Revision: <code>{document['x-reqlica-revision']?.slice(0, 12)}</code>
        </p>
      )}
      {!!document?.['x-reqlica-contract-warnings']?.length && (
        <p role="status">
          Incomplete contract: {document['x-reqlica-contract-warnings'].length}{' '}
          operation warning(s). Downloads retain these warnings; review the
          contract before relying on it.
        </p>
      )}
      {loading && <p role="status">Loading contract…</p>}
      {error && (
        <p role="alert">
          {error}{' '}
          {draftId
            ? 'Validate this draft to generate its current contract.'
            : 'Activate a validated draft to publish a contract.'}
        </p>
      )}
      {document && (
        <>
          <p className={styles.meta}>
            {document.info?.title || 'API'} ·{' '}
            {document.info?.version || 'Unversioned'} · {operations.length}{' '}
            operations
          </p>
          {operations.length ? (
            <>
              <label
                htmlFor={
                  draftId
                    ? 'draft-contract-operation'
                    : 'published-contract-operation'
                }
              >
                Inspect operation
              </label>
              <Select
                id={
                  draftId
                    ? 'draft-contract-operation'
                    : 'published-contract-operation'
                }
                value={current?.key || ''}
                onValueChange={setSelected}
              >
                {operations.map((item) => (
                  <option key={item.key} value={item.key}>
                    {item.key}
                  </option>
                ))}
              </Select>
              {current && (
                <div className={styles.details}>
                  <h3>{current.operation.summary || current.key}</h3>
                  {current.operation.description && (
                    <p>{current.operation.description}</p>
                  )}
                  <h4>Parameters</h4>
                  <pre>
                    {JSON.stringify(
                      current.operation.parameters || [],
                      null,
                      2,
                    )}
                  </pre>
                  <h4>Request body schema</h4>
                  <pre>
                    {JSON.stringify(requestMedia?.schema || null, null, 2)}
                  </pre>
                  {requestMedia?.schema && (
                    <>
                      <h4>Example request body</h4>
                      <pre>
                        {JSON.stringify(
                          requestMedia.example ??
                            sample(requestMedia.schema, document),
                          null,
                          2,
                        )}
                      </pre>
                    </>
                  )}
                  <h4>Responses</h4>
                  {Object.entries(current.operation.responses || {}).map(
                    ([code, response]) => (
                      <div key={code}>
                        <strong>
                          {code} {response.description}
                        </strong>
                        <pre>
                          {JSON.stringify(response.content || {}, null, 2)}
                        </pre>
                      </div>
                    ),
                  )}
                  <Link
                    href={{
                      pathname: '/request-tester',
                      query: {
                        projectId,
                        method: current.method.toUpperCase(),
                        path: current.path,
                        ...(draftId ? { draftId } : {}),
                      },
                    }}
                  >
                    Test this operation →
                  </Link>
                </div>
              )}
            </>
          ) : (
            <p>No operations in this contract.</p>
          )}
        </>
      )}
    </section>
  );
}
