export type RequestHeader = { name: string; value: string; enabled: boolean };
const headersByProject = new Map<string, RequestHeader[]>();
export function getRequestHeaders(projectId: string): RequestHeader[] {
  return (headersByProject.get(projectId) || []).map((row) => ({ ...row }));
}
export function validateHeaders(rows: RequestHeader[]) {
  const seen = new Set<string>();
  for (const row of rows.filter((item) => item.enabled && item.name.trim())) {
    const name = row.name.trim().toLowerCase();
    if (!/^[!#$%&'*+.^_`|~0-9a-z-]+$/.test(name) || /[\r\n]/.test(row.value))
      return 'Use valid header names and single-line values.';
    if (
      [
        'host',
        'cookie',
        'content-length',
        'connection',
        'origin',
        'referer',
        'transfer-encoding',
      ].includes(name) ||
      name.startsWith('sec-') ||
      name.startsWith('proxy-')
    )
      return `The ${name} header is managed by the connection.`;
    if (seen.has(name)) return `Remove the duplicate ${name} header.`;
    seen.add(name);
  }
  return null;
}
export function setRequestHeaders(projectId: string, rows: RequestHeader[]) {
  const error = validateHeaders(rows);
  if (error) throw new Error(error);
  headersByProject.set(
    projectId,
    rows
      .filter((row) => row.name.trim())
      .map((row) => ({ ...row, name: row.name.trim() })),
  );
}
