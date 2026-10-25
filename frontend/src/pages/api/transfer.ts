import type { NextApiRequest, NextApiResponse } from 'next';
export const config = { api: { bodyParser: false, responseLimit: '34mb' } };
export default async function handler(
  req: NextApiRequest,
  res: NextApiResponse,
) {
  res.setHeader('Cache-Control', 'no-store');
  const action = req.query.action;
  const id = req.query.projectId;
  if (
    !['export', 'workspace-export', 'import', 'workspace-import'].includes(
      String(action),
    )
  )
    return res.status(400).json({ error: 'Invalid transfer action.' });
  if (
    (action === 'export' && req.method !== 'GET') ||
    (action !== 'export' && req.method !== 'POST')
  )
    return res.status(405).json({ error: 'Method not allowed.' });
  try {
    if (
      req.method !== 'GET' &&
      (req.headers['sec-fetch-site'] === 'cross-site' ||
        (req.headers.origin &&
          new URL(req.headers.origin).host !== req.headers.host))
    )
      return res
        .status(403)
        .json({ error: 'Cross-origin requests are not allowed.' });
    const importing = action === 'import' || action === 'workspace-import';
    if (!importing && (typeof id !== 'string' || !/^[a-f0-9]{32}$/.test(id)))
      return res.status(400).json({ error: 'Select a project.' });
    const url = new URL(
      `/api/projects/${importing ? action : `${id}/${action}`}`,
      process.env.REQLICA_CONSOLE_URL || 'http://127.0.0.1:3001',
    );
    if (importing) {
      if (
        typeof req.query.directory !== 'string' ||
        !/^(\/|~\/)/.test(req.query.directory)
      )
        return res
          .status(400)
          .json({ error: 'Enter an absolute destination directory.' });
      url.searchParams.set('directory', req.query.directory);
      if (action === 'import') {
        url.searchParams.set(
          'clone',
          req.query.clone === 'true' ? 'true' : 'false',
        );
        if (typeof req.query.name === 'string' && req.query.name.trim())
          url.searchParams.set('name', req.query.name.trim());
      }
    }
    const chunks: Buffer[] = [];
    let bytes = 0;
    if (req.method === 'POST')
      for await (const chunk of req) {
        const data = Buffer.from(chunk);
        bytes += data.length;
        if (bytes > (importing ? 32 * 1024 * 1024 : 8192))
          return res
            .status(413)
            .json({ error: 'Transfer exceeds size limit.' });
        chunks.push(data);
      }
    const body = Buffer.concat(chunks);
    if (action === 'workspace-export') {
      const settings = JSON.parse(body.toString());
      if (
        settings.confirm !== true ||
        !/^[a-f0-9]{64}$/.test(settings.expected_revision || '') ||
        typeof settings.include_secrets !== 'boolean'
      )
        return res.status(400).json({
          error: 'Confirm the current revision and secret inclusion choice.',
        });
    }
    const upstream = await fetch(url, {
      method: req.method,
      redirect: 'error',
      headers: {
        'Content-Type': importing ? 'application/zip' : 'application/json',
      },
      body: req.method === 'POST' ? body : undefined,
      signal: AbortSignal.timeout(125000),
    });
    if (!upstream.ok) {
      const data = await upstream.json();
      return res.status(upstream.status).json({
        error:
          typeof data.detail === 'string'
            ? data.detail
            : 'Transfer rejected. Refresh projects before retrying.',
      });
    }
    if (importing)
      return res.status(upstream.status).json(await upstream.json());
    res.setHeader('Content-Type', 'application/zip');
    res.setHeader(
      'Content-Disposition',
      `attachment; filename="reqlica-${action}-${id}.zip"`,
    );
    return res.status(200).send(Buffer.from(await upstream.arrayBuffer()));
  } catch {
    return res.status(502).json({
      error:
        'Transfer could not be confirmed. Refresh projects and check the destination before retrying.',
    });
  }
}
