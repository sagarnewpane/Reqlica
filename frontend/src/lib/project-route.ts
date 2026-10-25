const projectPattern = /^[a-f0-9]{32}$/;
export function isProjectId(value: unknown): value is string {
  return typeof value === 'string' && projectPattern.test(value);
}
export function projectFromRoute(
  queryId: unknown,
  asPath: string,
): string | null {
  // asPath retains the concrete project address during Pages Router transitions.
  const path = asPath.split('#')[0];
  const [pathname, search = ''] = path.split('?');
  if (pathname === '/') return null;
  const segment = pathname.match(/^\/projects\/([a-f0-9]{32})\/?$/)?.[1];
  if (segment) return segment;
  const id = new URLSearchParams(search).get('projectId');
  if (isProjectId(id)) return id;
  return isProjectId(queryId) ? queryId : null;
}
