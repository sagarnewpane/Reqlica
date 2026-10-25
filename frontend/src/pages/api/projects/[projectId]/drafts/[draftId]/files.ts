import type { NextApiRequest, NextApiResponse } from 'next';

export const config = { api: { bodyParser: { sizeLimit: '2mb' } } };
const ID = /^[a-f0-9]{32}$/;
const REVISION = /^[a-f0-9]{64}$/;
const MAX_BYTES = 1024 * 1024;

export default async function handler(
  req: NextApiRequest,
  res: NextApiResponse,
) {
  res.setHeader('Cache-Control', 'no-store');
  if (!['GET', 'PUT', 'DELETE'].includes(req.method || ''))
    return res.status(405).json({
      error:
        'Use GET to inspect source or PUT to save an exact draft revision.',
    });
  const { projectId, draftId, path } = req.query;
  if (
    typeof projectId !== 'string' ||
    !ID.test(projectId) ||
    typeof draftId !== 'string' ||
    !ID.test(draftId)
  )
    return res.status(400).json({ error: 'Invalid project or draft address.' });
  if (
    typeof path !== 'string' ||
    !path ||
    path.length > 1024 ||
    path.startsWith('/') ||
    /^[a-z]:/i.test(path) ||
    /[\\\x00-\x1f\x7f]/.test(path) ||
    path
      .split('/')
      .some(
        (part) =>
          !part ||
          part === '.' ||
          part === '..' ||
          [
            '.reqlica',
            '.git',
            'drafts',
            'versions',
            '__pycache__',
            'project.json',
          ].includes(part) ||
          /\.(sqlite|sqlite3|db|pyc)$|(-wal|-shm)$/.test(part),
      )
  )
    return res
      .status(400)
      .json({ error: 'Use a safe, editable project-relative source path.' });
  if (req.method !== 'GET') {
    try {
      if (
        req.headers['sec-fetch-site'] === 'cross-site' ||
        (req.headers.origin &&
          new URL(req.headers.origin).host !== req.headers.host)
      )
        return res
          .status(403)
          .json({ error: 'Cross-origin requests are not allowed.' });
    } catch {
      return res.status(403).json({ error: 'Invalid request origin.' });
    }
    if (!req.headers['content-type']?.startsWith('application/json'))
      return res.status(415).json({ error: 'Expected JSON.' });
    if (
      (req.method === 'PUT' && typeof req.body?.content !== 'string') ||
      typeof req.body?.expected_revision !== 'string' ||
      !REVISION.test(req.body.expected_revision)
    )
      return res.status(400).json({
        error: 'File content and the exact inspected revision are required.',
      });
    if (
      req.method === 'PUT' &&
      Buffer.byteLength(req.body.content, 'utf8') > MAX_BYTES
    )
      return res
        .status(413)
        .json({ error: 'The draft source editor supports files up to 1 MiB.' });
  }
  try {
    const url = new URL(
      `/api/projects/${projectId}/drafts/${draftId}/files`,
      process.env.REQLICA_CONSOLE_URL || 'http://127.0.0.1:3001',
    );
    url.searchParams.set('path', path);
    const upstream = await fetch(url, {
      method: req.method,
      headers: { 'Content-Type': 'application/json' },
      body:
        req.method !== 'GET'
          ? JSON.stringify({
              ...(req.method === 'PUT' ? { content: req.body.content } : {}),
              expected_revision: req.body.expected_revision,
            })
          : undefined,
      signal: AbortSignal.timeout(15000),
      redirect: 'error',
    });
    const data = await upstream.json();
    if (!upstream.ok)
      return res.status(upstream.status).json({
        error:
          typeof data.detail === 'string'
            ? data.detail
            : data.detail?.message ||
              'The draft source request was rejected. Keep local edits and inspect the saved revision.',
      });
    if (
      req.method === 'GET' &&
      (typeof data.content !== 'string' ||
        Buffer.byteLength(data.content, 'utf8') > MAX_BYTES)
    )
      return res.status(413).json({
        error:
          'This file cannot be shown by the 1 MiB UTF-8 source editor. Use local editing tools.',
      });
    return res.status(upstream.status).json(data);
  } catch {
    return res.status(502).json({
      error:
        req.method !== 'GET'
          ? 'The file save could not be confirmed. Keep or download your local text, then refresh the saved draft before another write. Do not automatically resend.'
          : 'Cannot read the draft source. Check the console and use local tools for binary or non-UTF-8 files.',
    });
  }
}
