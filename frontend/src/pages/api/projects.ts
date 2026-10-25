import type { NextApiRequest, NextApiResponse } from 'next';

export default async function handler(
  req: NextApiRequest,
  res: NextApiResponse,
) {
  res.setHeader('Cache-Control', 'no-store');
  if (req.method !== 'GET' && req.method !== 'POST') {
    res.setHeader('Allow', 'GET, POST');
    return res.status(405).json({ error: 'Method not allowed.' });
  }
  if (req.method === 'POST') {
    let originHost: string | undefined;
    try {
      originHost = req.headers.origin
        ? new URL(req.headers.origin).host
        : undefined;
    } catch {
      return res.status(403).json({ error: 'Invalid request origin.' });
    }
    if (
      req.headers['sec-fetch-site'] === 'cross-site' ||
      (originHost && originHost !== req.headers.host)
    ) {
      return res
        .status(403)
        .json({ error: 'Cross-origin requests are not allowed.' });
    }
    if (!req.headers['content-type']?.startsWith('application/json')) {
      return res.status(415).json({ error: 'Expected a JSON request.' });
    }
    const { name, directory } = req.body ?? {};
    if (
      typeof name !== 'string' ||
      !name.trim() ||
      typeof directory !== 'string' ||
      !/^(\/|~\/)/.test(directory.trim())
    ) {
      return res.status(400).json({
        error: 'Enter a name and an absolute directory (or ~/ path).',
      });
    }
  }
  try {
    const origin = process.env.REQLICA_RUNTIME_URL || 'http://127.0.0.1:4000';
    const response = await fetch(new URL('/api/projects', origin), {
      method: req.method,
      headers: { 'Content-Type': 'application/json' },
      body:
        req.method === 'POST'
          ? JSON.stringify({
              name: req.body.name.trim(),
              directory: req.body.directory.trim(),
            })
          : undefined,
      signal: AbortSignal.timeout(15000),
    });
    const data = await response.json();
    if (!response.ok) {
      const detail =
        typeof data.detail === 'string'
          ? data.detail
          : 'The runtime could not complete this request. Check the project name and directory.';
      return res.status(response.status).json({ error: detail });
    }
    return res.status(response.status).json(data);
  } catch {
    return res.status(502).json({
      error:
        req.method === 'POST'
          ? 'Creation could not be confirmed. Refresh the project list before retrying, then check that the Reqlica runtime is running.'
          : 'Cannot reach the Reqlica runtime. Start it and try again.',
    });
  }
}
