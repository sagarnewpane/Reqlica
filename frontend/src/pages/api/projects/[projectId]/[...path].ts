import type { NextApiRequest, NextApiResponse } from 'next';

const RESOURCE_ID = '[a-f0-9]{32}';
const LIBRARY_ROUTE = /^library\/[a-f0-9]{32}$/;
const ENDPOINT_ROUTE = /^drafts\/[a-f0-9]{32}\/endpoints$/;
const MIGRATION_ROUTE =
  /^database-migrations\/(preview|apply|rollback|recover)$/;
const GENERATE_SECRET = /^environment\/[A-Za-z_][A-Za-z0-9_]{0,63}\/generate$/;
const DRAFT_ROUTE = new RegExp(`^drafts/${RESOURCE_ID}$`);
const DRAFT_CHECK_ROUTE = new RegExp(`^drafts/${RESOURCE_ID}/(validate|test)$`);
const DRAFT_CALL_ROUTE = new RegExp(`^drafts/${RESOURCE_ID}/call$`);
const DRAFT_DATABASE_ROUTE = new RegExp(
  `^drafts/${RESOURCE_ID}/database/(preview|apply)$`,
);
const DRAFT_SCHEMA_ROUTE = new RegExp(
  `^drafts/${RESOURCE_ID}/(openapi\\.json|docs)$`,
);
const DRAFT_APPROVAL_ROUTE = new RegExp(`^drafts/${RESOURCE_ID}/approve$`);
const DRAFT_RECOVERY_ROUTE = new RegExp(`^drafts/${RESOURCE_ID}/recover$`);
const REVISION = /^[a-f0-9]{64}$/;
const DRAFT_ID = /^[a-f0-9]{32}$/;
const ENVIRONMENT_ROUTE = /^environment\/[A-Za-z_][A-Za-z0-9_]{0,63}$/;
const LOG_EVENT_ROUTE = /^logs\/[1-9]\d*$/;
const LOG_KINDS = [
  'request',
  'runtime_error',
  'startup_error',
  'stdout',
  'stderr',
];
const PACKAGE_JOB_ROUTE = new RegExp(`^packages/jobs/${RESOURCE_ID}$`);
const PACKAGE_INSTALL_ROUTE = new RegExp(
  `^packages/jobs/${RESOURCE_ID}/install$`,
);
const AGENT_RUN_ROUTE = new RegExp(`^agent-runs/${RESOURCE_ID}$`);
const AGENT_CONTEXT_ROUTE = new RegExp(`^agent-runs/${RESOURCE_ID}/context$`);
const AGENT_REASONING_ROUTE = new RegExp(
  `^agent-runs/${RESOURCE_ID}/reasoning-default$`,
);
const AGENT_RECOVER_ROUTE = new RegExp(`^agent-runs/${RESOURCE_ID}/recover$`);
const ACTIVITY_ROUTE = new RegExp(`^activity/${RESOURCE_ID}$`);
const AGENT_ACTION_ROUTE = new RegExp(
  `^agent-runs/${RESOURCE_ID}/(step|replies)$`,
);
const RUNTIME_ROUTES = new Set([
  'endpoints',
  'deletion',
  'deletion-files',
  'registration',
  'start',
  'stop',
  'activate',
]);

function isAllowedOperation(method: string | undefined, route: string) {
  switch (method) {
    case 'GET':
      return (
        [
          'packages/jobs',
          'library',
          'versions',
          'maintenance',
          'mock-auth',
          'cors',
          'scenarios',
          'data-baseline',
          'database-migrations',
          'schema-export',
          'endpoints',
          'deletion',
          'deletion-files',
          'drafts',
          'environment',
          'packages',
          'agent-runs',
          'logs',
          'logs/settings',
          'openapi.json',
          'docs',
        ].includes(route) ||
        DRAFT_ROUTE.test(route) ||
        LOG_EVENT_ROUTE.test(route) ||
        PACKAGE_JOB_ROUTE.test(route) ||
        AGENT_RUN_ROUTE.test(route) ||
        AGENT_CONTEXT_ROUTE.test(route) ||
        ACTIVITY_ROUTE.test(route) ||
        DRAFT_SCHEMA_ROUTE.test(route)
      );
    case 'PUT':
      return (
        LIBRARY_ROUTE.test(route) ||
        ENDPOINT_ROUTE.test(route) ||
        ['mock-auth', 'cors', 'scenarios', 'data-baseline'].includes(route) ||
        ENVIRONMENT_ROUTE.test(route) ||
        AGENT_RUN_ROUTE.test(route) ||
        route === 'ai/key' ||
        route === 'logs/settings'
      );
    case 'PATCH':
      return DRAFT_ROUTE.test(route);
    case 'DELETE':
      return (
        LIBRARY_ROUTE.test(route) ||
        ENDPOINT_ROUTE.test(route) ||
        ENVIRONMENT_ROUTE.test(route) ||
        DRAFT_ROUTE.test(route) ||
        route === 'ai/key' ||
        route === 'registration'
      );
    case 'POST':
      return (
        [
          'versions/restore',
          'relocate',
          'convert',
          'data-reset',
          'scenarios/reset',
          'specifications/preview',
          'drafts',
          'start',
          'stop',
          'activate',
          'packages/preview',
          'ai/models',
          'ai/key/status',
          'ai/key/reveal',
        ].includes(route) ||
        MIGRATION_ROUTE.test(route) ||
        GENERATE_SECRET.test(route) ||
        DRAFT_CHECK_ROUTE.test(route) ||
        DRAFT_CALL_ROUTE.test(route) ||
        DRAFT_DATABASE_ROUTE.test(route) ||
        DRAFT_APPROVAL_ROUTE.test(route) ||
        DRAFT_RECOVERY_ROUTE.test(route) ||
        PACKAGE_INSTALL_ROUTE.test(route) ||
        AGENT_ACTION_ROUTE.test(route) ||
        AGENT_RECOVER_ROUTE.test(route) ||
        AGENT_REASONING_ROUTE.test(route)
      );
    default:
      return false;
  }
}

// Bounded management routes; approval and activation require explicit confirmation.
export default async function handler(
  req: NextApiRequest,
  res: NextApiResponse,
) {
  res.setHeader('Cache-Control', 'no-store');
  const { projectId, path } = req.query;
  if (
    typeof projectId !== 'string' ||
    !/^[a-f0-9]{32}$/.test(projectId) ||
    !Array.isArray(path)
  ) {
    return res.status(400).json({ error: 'Invalid project address.' });
  }
  const route = path.join('/');
  const runtime =
    RUNTIME_ROUTES.has(route) || route === 'openapi.json' || route === 'docs';
  const draftCheck = DRAFT_CHECK_ROUTE.test(route);
  const draftDatabase = DRAFT_DATABASE_ROUTE.test(route);
  const draftCall = DRAFT_CALL_ROUTE.test(route);
  const schemaDocument =
    route === 'openapi.json' || DRAFT_SCHEMA_ROUTE.test(route);
  const swaggerDocument =
    route === 'docs' || /^drafts\/[a-f0-9]{32}\/docs$/.test(route);
  const publication = route === 'activate' || DRAFT_APPROVAL_ROUTE.test(route);
  const workerAction = route === 'start' || route === 'stop';
  const activityId = req.headers['x-reqlica-activity-id'];
  if (!isAllowedOperation(req.method, route))
    return res.status(405).json({ error: 'This operation is not available.' });
  if (
    route.endsWith('/step') &&
    activityId !== undefined &&
    (typeof activityId !== 'string' || !DRAFT_ID.test(activityId))
  )
    return res.status(400).json({ error: 'Invalid activity ID.' });
  if (LOG_EVENT_ROUTE.test(route) && !Number.isSafeInteger(Number(path[1]))) {
    return res.status(400).json({ error: 'Invalid event ID.' });
  }
  if (
    schemaDocument &&
    req.query.revision !== undefined &&
    (typeof req.query.revision !== 'string' ||
      !REVISION.test(req.query.revision))
  )
    return res.status(400).json({ error: 'Invalid schema revision.' });
  let logQuery: URLSearchParams | undefined;
  if (route === 'logs') {
    logQuery = new URLSearchParams();
    for (const name of ['limit', 'before_id', 'kind', 'request_id'] as const) {
      const value = req.query[name];
      if (value === undefined) continue;
      const valid =
        typeof value === 'string' &&
        (name === 'kind'
          ? LOG_KINDS.includes(value)
          : name === 'request_id'
            ? /^[a-f0-9]{32}$/.test(value)
            : /^[1-9]\d*$/.test(value) &&
              Number.isSafeInteger(Number(value)) &&
              (name !== 'limit' || Number(value) <= 100));
      if (!valid)
        return res
          .status(400)
          .json({ error: `Invalid history filter: ${name}.` });
      logQuery.set(name, value as string);
    }
  }
  if (req.method !== 'GET') {
    try {
      if (
        req.headers['sec-fetch-site'] === 'cross-site' ||
        (req.headers.origin &&
          new URL(req.headers.origin).host !== req.headers.host)
      ) {
        return res
          .status(403)
          .json({ error: 'Cross-origin requests are not allowed.' });
      }
    } catch {
      return res.status(403).json({ error: 'Invalid request origin.' });
    }
    if (!req.headers['content-type']?.startsWith('application/json')) {
      return res.status(415).json({ error: 'Expected JSON.' });
    }
  }
  if (workerAction && req.body?.confirm !== true) {
    return res.status(400).json({
      error:
        'Explicit confirmation is required to start or stop a project worker.',
    });
  }
  if (
    route === 'logs/settings' &&
    req.method === 'PUT' &&
    (req.body?.confirm !== true ||
      typeof req.body.expected_revision !== 'string' ||
      !REVISION.test(req.body.expected_revision) ||
      typeof req.body.capture_payloads !== 'boolean' ||
      typeof req.body.capture_output !== 'boolean' ||
      !Number.isInteger(req.body.max_body_bytes) ||
      req.body.max_body_bytes < 128 ||
      req.body.max_body_bytes > 2048)
  ) {
    return res.status(400).json({
      error:
        'Confirm capture settings with a current revision, explicit boolean choices, and a 128–2,048 byte body limit.',
    });
  }
  if (
    publication &&
    (req.body?.confirm !== true ||
      typeof req.body.expected_revision !== 'string' ||
      !REVISION.test(req.body.expected_revision) ||
      (route === 'activate' &&
        (typeof req.body.draft_id !== 'string' ||
          !DRAFT_ID.test(req.body.draft_id))))
  ) {
    return res.status(400).json({
      error:
        'Explicit confirmation, an exact draft revision, and a valid draft address are required for publication.',
    });
  }
  if (
    draftDatabase &&
    req.method === 'POST' &&
    (typeof req.body?.expected_revision !== 'string' ||
      !REVISION.test(req.body.expected_revision) ||
      (route.endsWith('/apply') &&
        (req.body.confirm !== true ||
          !REVISION.test(req.body.expected_schema || '') ||
          !REVISION.test(req.body.expected_preview_digest || ''))))
  )
    return res.status(400).json({
      error:
        'Preview the current draft and confirm the exact schema before applying.',
    });
  if (
    draftCall &&
    req.method === 'POST' &&
    (!REVISION.test(req.body?.expected_revision || '') ||
      !['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS', 'HEAD'].includes(
        req.body?.method,
      ) ||
      typeof req.body?.path !== 'string' ||
      !req.body.path.startsWith('/') ||
      req.body.path.includes('\\') ||
      /%2f|%5c/i.test(req.body.path) ||
      (req.body?.headers !== undefined &&
        (typeof req.body.headers !== 'object' ||
          Array.isArray(req.body.headers))))
  )
    return res.status(400).json({
      error:
        'Use a current draft revision and valid request method, path, and headers.',
    });
  if (
    route === 'registration' &&
    (req.body?.confirm !== true ||
      typeof req.body?.delete_files !== 'boolean' ||
      (req.body.delete_files &&
        typeof req.body.expected_directory !== 'string'))
  )
    return res
      .status(400)
      .json({ error: 'Explicit project removal confirmation is required.' });
  try {
    const origin = runtime
      ? process.env.REQLICA_RUNTIME_URL || 'http://127.0.0.1:4000'
      : process.env.REQLICA_CONSOLE_URL || 'http://127.0.0.1:3001';
    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
    };
    // Legacy request-only keys are forwarded only to model steps.
    if (req.method === 'POST' && route.startsWith('packages/'))
      headers['X-Reqlica-Package-Action'] = 'reviewed-ui';
    const key = req.headers['x-reqlica-groq-key'];
    if (typeof key === 'string' && route.endsWith('/step'))
      headers['X-Reqlica-Groq-Key'] = key;
    if (typeof activityId === 'string' && route.endsWith('/step'))
      headers['X-Reqlica-Activity-ID'] = activityId;
    const upstreamPath =
      route === 'registration'
        ? `/api/projects/${projectId}`
        : `/api/projects/${projectId}/${route === 'deletion-files' ? 'deletion?delete_files=true' : route}`;
    let payload: unknown = req.body ?? {};
    if (workerAction) {
      // Lifecycle endpoints take no parameters. Never forward keys or unrelated actions.
      payload = {};
    } else if (route === 'activate') {
      // Confirmation is enforced here; the runtime accepts only these two fields.
      payload = {
        draft_id: req.body.draft_id,
        expected_revision: req.body.expected_revision,
      };
    } else if (publication) {
      payload = {
        expected_revision: req.body.expected_revision,
        confirm: true,
      };
    } else if (route === 'registration') {
      payload = {
        delete_files: req.body.delete_files,
        expected_directory: req.body.expected_directory,
      };
    } else if (route === 'logs/settings' && req.method === 'PUT') {
      payload = {
        expected_revision: req.body.expected_revision,
        capture_payloads: req.body.capture_payloads,
        capture_output: req.body.capture_output,
        max_body_bytes: req.body.max_body_bytes,
      };
    } else if (draftDatabase) {
      payload = route.endsWith('/apply')
        ? {
            expected_revision: req.body.expected_revision,
            expected_schema: req.body.expected_schema,
            expected_preview_digest: req.body.expected_preview_digest,
            confirm: true,
          }
        : { expected_revision: req.body.expected_revision };
    } else if (draftCall) {
      payload = {
        expected_revision: req.body.expected_revision,
        method: req.body.method,
        path: req.body.path,
        json_body: req.body.json_body ?? null,
        headers: req.body.headers || {},
      };
    }
    const upstreamUrl = new URL(upstreamPath, origin);
    if (logQuery) upstreamUrl.search = logQuery.toString();
    if (route === 'packages/jobs')
      for (const key of ['offset', 'limit']) {
        const value = req.query[key];
        if (value !== undefined) {
          if (typeof value !== 'string' || !/^\d+$/.test(value))
            return res.status(400).json({ error: 'Invalid history page.' });
          upstreamUrl.searchParams.set(key, value);
        }
      }

    if (route === 'schema-export') {
      for (const key of ['revision', 'format', 'draft_id']) {
        const value = req.query[key];
        if (value !== undefined) {
          if (typeof value !== 'string')
            return res.status(400).json({ error: 'Invalid export parameter.' });
          upstreamUrl.searchParams.set(key, value);
        }
      }
    }
    if (schemaDocument && typeof req.query.revision === 'string')
      upstreamUrl.searchParams.set('revision', req.query.revision);
    const upstream = await fetch(upstreamUrl, {
      method: req.method,
      headers,
      body: req.method === 'GET' ? undefined : JSON.stringify(payload),
      signal: AbortSignal.timeout(
        route.endsWith('/step') ||
          draftCheck ||
          draftCall ||
          draftDatabase ||
          MIGRATION_ROUTE.test(route) ||
          route === 'data-reset' ||
          route === 'relocate'
          ? 620000
          : route === 'activate'
            ? 60000
            : workerAction
              ? 30000
              : 15000,
      ),
      redirect: 'error',
    });
    if (route === 'schema-export' && upstream.ok) {
      res.setHeader(
        'Content-Type',
        upstream.headers.get('content-type') || 'application/octet-stream',
      );
      res.setHeader(
        'Content-Disposition',
        upstream.headers.get('content-disposition') || 'attachment',
      );
      return res.status(200).send(await upstream.text());
    }
    if (swaggerDocument) {
      const html = await upstream.text();
      if (!upstream.ok) return res.status(upstream.status).send(html);
      const schemaPath = `/api/projects/${projectId}/${route.replace(/docs$/, 'openapi.json')}`;
      const rewritten = html.replace(/url: '([^']+)'/, (_, raw: string) => {
        const revision = new URL(raw).search;
        return `url: '${schemaPath}${revision}'`;
      });
      res.setHeader('Content-Type', 'text/html; charset=utf-8');
      return res.status(200).send(rewritten);
    }
    const data = await upstream.json();
    if (!upstream.ok) {
      const detail = data.detail;
      const validation = Array.isArray(detail)
        ? detail
            .slice(0, 4)
            .map(
              (issue: { loc?: unknown[]; msg?: unknown }) =>
                `${(issue.loc || []).join('.')}: ${String(issue.msg || 'Invalid value')}`,
            )
            .join('; ')
            .slice(0, 1200)
        : null;
      return res.status(upstream.status).json({
        error:
          typeof detail === 'string'
            ? detail
            : validation ||
              detail?.message ||
              (route === 'logs' || route.startsWith('logs/')
                ? 'The inspection request was rejected. Reload history or capture settings.'
                : workerAction
                  ? 'The worker operation was rejected. Refresh status and inspect project diagnostics.'
                  : 'The request was rejected. Check your settings and refresh the conversation.'),
      });
    }
    return res.status(upstream.status).json(data);
  } catch {
    return res.status(502).json({
      error:
        req.method === 'GET'
          ? `Cannot reach the Reqlica ${runtime ? 'runtime' : 'console'}. Check that it is running.`
          : route.startsWith('ai/')
            ? 'Could not confirm the AI settings request. Reload Settings before retrying.'
            : route === 'logs/settings'
              ? 'Capture settings could not be confirmed. Reload capture settings before saving again; the change may have been applied.'
              : workerAction
                ? 'The worker operation could not be confirmed. Refresh status before another action; the worker may already have started or stopped.'
                : publication ||
                    route === 'registration' ||
                    route.startsWith('drafts')
                  ? 'The operation could not be confirmed. Refresh the project or draft before retrying; it may still finish.'
                  : 'The response could not be confirmed. Refresh the conversation before retrying; the current step may still finish.',
    });
  }
}
