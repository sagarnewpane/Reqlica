import type { Endpoint } from './projects';

// Keep aligned with the bounded request proxy; TRACE is intentionally excluded.
export const REQUEST_METHODS = [
  'GET',
  'POST',
  'PUT',
  'PATCH',
  'DELETE',
  'OPTIONS',
  'HEAD',
];

export function endpointKey(endpoint: Endpoint) {
  return `${endpoint.method} ${endpoint.path}`;
}

export function filterEndpoints(
  endpoints: Endpoint[],
  query: string,
  method = 'all',
) {
  const terms = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  return endpoints.filter((endpoint) => {
    const text =
      `${endpoint.method} ${endpoint.path} ${endpoint.handler || ''}`.toLowerCase();
    return (
      (method === 'all' || endpoint.method === method) &&
      terms.every((term) => text.includes(term))
    );
  });
}

/** Only resolve a requested route if it still exists and the proxy supports it. */
export function resolveTesterEndpoint(
  endpoints: Endpoint[],
  method?: string,
  path?: string,
) {
  const supported = endpoints.filter((endpoint) =>
    REQUEST_METHODS.includes(endpoint.method),
  );
  if (method !== undefined || path !== undefined) {
    return supported.find(
      (endpoint) => endpoint.method === method && endpoint.path === path,
    );
  }
  return (
    supported.find((endpoint) => endpoint.method === 'GET') || supported[0]
  );
}
