import type { NextApiRequest, NextApiResponse } from 'next';
import { validateHeaders, type RequestHeader } from '@/lib/request-headers';
export default async function handler(
  req: NextApiRequest,
  res: NextApiResponse,
) {
  res.setHeader('Cache-Control', 'no-store');
  const id = req.query.projectId;
  if (req.method !== 'POST')
    return res.status(405).json({ error: 'Use POST.' });
  if (typeof id !== 'string' || !/^[a-f0-9]{32}$/.test(id))
    return res.status(400).json({ error: 'Invalid project.' });
  try {
    if (
      req.headers['sec-fetch-site'] === 'cross-site' ||
      (req.headers.origin &&
        new URL(req.headers.origin).host !== req.headers.host)
    )
      return res.status(403).json({ error: 'Cross-origin request denied.' });
    if (!req.headers['content-type']?.startsWith('application/json'))
      return res.status(415).json({ error: 'Expected JSON.' });
    const { method, path, headers: rows, body } = req.body ?? {};
    if (
      !['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS', 'HEAD'].includes(
        method,
      ) ||
      typeof path !== 'string' ||
      !path.startsWith('/') ||
      path.includes('\\') ||
      /%2f|%5c/i.test(path) ||
      !Array.isArray(rows) ||
      rows.length > 100 ||
      rows.some(
        (row) =>
          typeof row?.name !== 'string' ||
          typeof row?.value !== 'string' ||
          typeof row?.enabled !== 'boolean',
      )
    )
      return res.status(400).json({ error: 'Invalid request parameters.' });
    const invalid = validateHeaders(rows);
    if (invalid) return res.status(400).json({ error: invalid });
    const url = new URL(
      `/mock/${id}${path}`,
      process.env.REQLICA_RUNTIME_URL || 'http://127.0.0.1:4000',
    );
    if (!url.pathname.startsWith(`/mock/${id}/`))
      return res
        .status(400)
        .json({ error: 'Request path must stay inside this project.' });
    const headers: Record<string, string> = {};
    (rows as RequestHeader[])
      .filter((row) => row.enabled && row.name.trim())
      .forEach((row) => {
        headers[row.name.trim()] = row.value;
      });
    const start = Date.now();
    const response = await fetch(url, {
      method,
      headers,
      body: ['GET', 'HEAD'].includes(method) ? undefined : String(body || ''),
      redirect: 'manual',
      signal: AbortSignal.timeout(30000),
    });
    const reader = response.body?.getReader();
    const chunks: Uint8Array[] = [];
    let bytes = 0;
    if (reader) {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        bytes += value.byteLength;
        if (bytes > 1024 * 1024) {
          await reader.cancel();
          return res
            .status(502)
            .json({ error: 'Response exceeds the 1 MiB preview limit.' });
        }
        chunks.push(value);
      }
    }
    const text = Buffer.concat(chunks).toString('utf8');
    return res.status(200).json({
      status: response.status,
      duration: Date.now() - start,
      headers: Object.fromEntries(response.headers),
      body: text,
    });
  } catch {
    return res.status(502).json({
      error:
        'No response confirmed. The request may have run. Check the runtime before resending.',
    });
  }
}
