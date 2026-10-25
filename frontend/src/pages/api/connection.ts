import type { NextApiRequest, NextApiResponse } from 'next';
import {
  connectionOrigins,
  saveConnections,
  type Connections,
} from '@/lib/server-connections';
export default async function handler(
  req: NextApiRequest,
  res: NextApiResponse,
) {
  res.setHeader('Cache-Control', 'no-store');
  if (!['GET', 'PUT'].includes(req.method || ''))
    return res.status(405).json({ error: 'Use GET or PUT.' });
  try {
    if (req.method === 'PUT') {
      if (
        req.headers['sec-fetch-site'] === 'cross-site' ||
        (req.headers.origin &&
          new URL(req.headers.origin).host !== req.headers.host)
      )
        return res
          .status(403)
          .json({ error: 'Cross-origin request rejected.' });
      if (!req.headers['content-type']?.startsWith('application/json'))
        return res.status(415).json({ error: 'Expected JSON.' });
      return res.status(200).json(saveConnections(req.body as Connections));
    }
    const origins = connectionOrigins();
    const health: Record<string, string> = {};
    if (req.query.health === '1')
      await Promise.all(
        (['console', 'runtime'] as const).map(async (key) => {
          try {
            const reply = await fetch(`${origins[key]}/api/health`, {
              signal: AbortSignal.timeout(3000),
              redirect: 'error',
            });
            health[key] = reply.ok ? 'Connected' : `HTTP ${reply.status}`;
          } catch {
            health[key] = 'Unreachable';
          }
        }),
      );
    return res.status(200).json({ ...origins, health });
  } catch (reason) {
    return res.status(400).json({ error: (reason as Error).message });
  }
}
