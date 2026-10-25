import type { NextApiRequest, NextApiResponse } from 'next';
export default async function handler(
  req: NextApiRequest,
  res: NextApiResponse,
) {
  res.setHeader('Cache-Control', 'no-store');
  if (req.method !== 'POST')
    return res.status(405).json({ error: 'Use POST.' });
  try {
    if (
      req.headers['sec-fetch-site'] === 'cross-site' ||
      (req.headers.origin &&
        new URL(req.headers.origin).host !== req.headers.host)
    )
      return res.status(403).json({ error: 'Cross-origin request denied.' });
    if (!req.headers['content-type']?.startsWith('application/json'))
      return res.status(415).json({ error: 'Expected JSON.' });
    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
    };
    const key = req.headers['x-reqlica-provider-key'];
    if (typeof key === 'string') headers['X-Reqlica-Provider-Key'] = key;
    const response = await fetch(
      new URL(
        '/api/ai/models',
        process.env.REQLICA_CONSOLE_URL || 'http://127.0.0.1:3001',
      ),
      {
        method: 'POST',
        headers,
        body: JSON.stringify({
          provider: req.body?.provider,
          base_url: req.body?.base_url || null,
        }),
        signal: AbortSignal.timeout(15000),
        redirect: 'error',
      },
    );
    const data = await response.json();
    return res.status(response.status).json(
      response.ok
        ? data
        : {
            error:
              typeof data.detail === 'string'
                ? data.detail
                : data.detail?.message || 'Check the provider URL and API key.',
          },
    );
  } catch {
    return res.status(502).json({
      error:
        'Could not load models. Check that the console is running, then retry.',
    });
  }
}
