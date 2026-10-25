import { workspaceRequest } from './chat';
import {
  getRequestHeaders,
  validateHeaders,
  type RequestHeader,
} from './request-headers';
export type RequestConfig = {
  method: string;
  path: string;
  body: string;
  headerText?: string;
  draftId?: string;
};
export type RequestResult = {
  status: number;
  duration: number;
  body: string;
  headers: Record<string, string>;
};
export function requestHeaders(projectId: string, text = ''): RequestHeader[] {
  const shared = getRequestHeaders(projectId);
  const overrides: RequestHeader[] = text
    .split('\n')
    .filter((line) => line.trim())
    .map((line) => {
      const split = line.indexOf(':');
      if (split < 1) throw new Error('Enter one Header: value per line.');
      return {
        name: line.slice(0, split).trim(),
        value: line.slice(split + 1).trim(),
        enabled: true,
      };
    });
  const error = validateHeaders(overrides);
  if (error) throw new Error(error);
  const names = new Set(overrides.map((h) => h.name.toLowerCase()));
  const headers = [
    ...shared.filter((h) => !names.has(h.name.toLowerCase())),
    ...overrides,
  ];
  const combined = validateHeaders(headers);
  if (combined) throw new Error(combined);
  return headers;
}
export async function executeRequest(
  projectId: string,
  config: RequestConfig,
): Promise<RequestResult> {
  const headers = requestHeaders(projectId, config.headerText);
  if (!config.draftId)
    return workspaceRequest<RequestResult>(projectId, 'request', 'POST', {
      method: config.method,
      path: config.path,
      body: config.body,
      headers,
    });
  const draft = await workspaceRequest<{ id: string; revision: string }>(
    projectId,
    `drafts/${config.draftId}`,
  );
  const start = performance.now();
  const response = await workspaceRequest<{
    status: string;
    status_code?: number;
    body?: unknown;
    headers?: Record<string, string>;
    output?: string;
  }>(projectId, `drafts/${draft.id}/call`, 'POST', {
    expected_revision: draft.revision,
    method: config.method,
    path: config.path,
    json_body: config.body.trim() ? JSON.parse(config.body) : null,
    headers: Object.fromEntries(
      headers.filter((h) => h.enabled).map((h) => [h.name, h.value]),
    ),
  });
  if (response.status !== 'passed')
    throw new Error(response.output || `Draft check ${response.status}`);
  return {
    status: response.status_code || 0,
    duration: Math.round(performance.now() - start),
    body:
      typeof response.body === 'string'
        ? response.body
        : JSON.stringify(response.body ?? null),
    headers: response.headers || {},
  };
}
export function requestCurl(
  projectId: string,
  config: RequestConfig,
  baseUrl: string,
) {
  const quote = (value: string) => "'" + value.replaceAll("'", "'\\''") + "'";
  return `curl -X ${config.method} ${quote(baseUrl.replace(/\/$/, '') + config.path)}${requestHeaders(
    projectId,
    config.headerText,
  )
    .filter((h) => h.enabled)
    .map((h) => ` -H ${quote(`${h.name}: ${h.value}`)}`)
    .join('')}${config.body ? ` --data-raw ${quote(config.body)}` : ''}`;
}
