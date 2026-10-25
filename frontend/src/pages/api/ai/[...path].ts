import type { NextApiRequest, NextApiResponse } from 'next';
export default async function handler(
  req: NextApiRequest,
  res: NextApiResponse,
) {
  res.setHeader('Cache-Control', 'no-store');
  const route = Array.isArray(req.query.path) ? req.query.path.join('/') : '';
  const allowed =
    route === 'key'
      ? ['PUT', 'DELETE']
      : ['key/status', 'key/reveal'].includes(route)
        ? ['POST']
        : [];
  if (!allowed.includes(req.method || ''))
    return res
      .status(405)
      .json({ error: 'This app AI operation is not available.' });
  try {
    if (
      req.headers['sec-fetch-site'] === 'cross-site' ||
      (req.headers.origin &&
        new URL(req.headers.origin).host !== req.headers.host)
    )
      return res.status(403).json({ error: 'Cross-origin request denied.' });
    if (!req.headers['content-type']?.startsWith('application/json'))
      return res.status(415).json({ error: 'Expected JSON.' });
    const body = {
      provider: req.body?.provider,
      base_url: req.body?.base_url || null,
      ...(req.method === 'PUT' ? { api_key: req.body?.api_key } : {}),
    };
    const reply = await fetch(
      new URL(
        `/api/ai/${route}`,
        process.env.REQLICA_CONSOLE_URL || 'http://127.0.0.1:3001',
      ),
      {
        method: req.method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
        signal: AbortSignal.timeout(15000),
        redirect: 'error',
      },
    );
    const data = await reply.json();
    return res
      .status(reply.status)
      .json(
        reply.ok
          ? data
          : {
              error:
                typeof data.detail === 'string'
                  ? data.detail
                  : 'App API settings were rejected. Reload before retrying.',
            },
      );
  } catch {
    return res
      .status(502)
      .json({
        error:
          'App API settings could not be confirmed. Reload their saved status before retrying.',
      });
  }
}
