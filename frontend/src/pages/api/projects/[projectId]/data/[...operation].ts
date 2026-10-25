import type { NextApiRequest, NextApiResponse } from 'next';

const DIGEST = /^[a-f0-9]{64}$/;
const DATA_OPERATIONS = [
  'validate',
  'insert',
  'related/validate',
  'related/insert',
];
export const config = { api: { bodyParser: { sizeLimit: '256kb' } } };

function rowsValid(rows: unknown): rows is Record<string, unknown>[] {
  return (
    Array.isArray(rows) &&
    rows.length > 0 &&
    rows.length <= 100 &&
    rows.every(
      (row) =>
        row !== null &&
        typeof row === 'object' &&
        !Array.isArray(row) &&
        Object.keys(row).length > 0,
    )
  );
}

function hasUnsafeNumber(records: unknown) {
  const pending = [records];
  while (pending.length) {
    const value = pending.pop();
    if (
      typeof value === 'number' &&
      (!Number.isFinite(value) ||
        (Number.isInteger(value) && !Number.isSafeInteger(value)))
    )
      return true;
    if (value && typeof value === 'object') {
      for (const child of Object.values(value)) pending.push(child);
    }
  }
  return false;
}

export default async function handler(
  req: NextApiRequest,
  res: NextApiResponse,
) {
  res.setHeader('Cache-Control', 'no-store');
  const { projectId, operation } = req.query;
  if (
    typeof projectId !== 'string' ||
    !/^[a-f0-9]{32}$/.test(projectId) ||
    !Array.isArray(operation)
  )
    return res.status(400).json({ error: 'Invalid project data address.' });
  const route = operation.join('/');
  if (
    !(req.method === 'GET' && route === 'schema') &&
    !(req.method === 'POST' && DATA_OPERATIONS.includes(route))
  )
    return res
      .status(405)
      .json({ error: 'This data operation is not available.' });
  const insert = route === 'insert' || route === 'related/insert';
  const related = route.startsWith('related/');
  let payload: Record<string, unknown> | undefined;
  if (req.method === 'POST') {
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
    const body = req.body ?? {};
    if (
      insert &&
      (body.confirm !== true ||
        typeof body.expected_preview_digest !== 'string' ||
        !DIGEST.test(body.expected_preview_digest))
    )
      return res.status(400).json({
        error: 'Confirm insertion with the exact validated preview digest.',
      });
    const sqlite = related || body.target === 'sqlite';
    if (
      sqlite &&
      (typeof body.expected_schema !== 'string' ||
        !DIGEST.test(body.expected_schema))
    )
      return res
        .status(400)
        .json({ error: 'A current database schema fingerprint is required.' });
    let records: unknown;
    if (related) {
      records = body.records_by_table;
      if (!records || typeof records !== 'object' || Array.isArray(records))
        return res
          .status(400)
          .json({ error: 'Provide records grouped by table.' });
      const groups = Object.entries(records);
      if (
        groups.length < 1 ||
        groups.length > 10 ||
        groups.some(
          ([name, rows]) => !name || name.length > 128 || !rowsValid(rows),
        ) ||
        groups.reduce(
          (count, [, rows]) => count + (rows as unknown[]).length,
          0,
        ) > 100
      )
        return res
          .status(400)
          .json({ error: 'Use 1–10 tables and 1–100 total records.' });
      payload = {
        expected_schema: body.expected_schema,
        records_by_table: records,
      };
    } else {
      if (
        !['json', 'sqlite'].includes(body.target) ||
        (insert && body.target !== 'sqlite')
      )
        return res.status(400).json({
          error:
            'Only SQLite previews can be inserted; choose a valid data target.',
        });
      if (
        sqlite &&
        (typeof body.table !== 'string' ||
          !body.table ||
          body.table.length > 128)
      )
        return res
          .status(400)
          .json({ error: 'Choose an existing SQLite table.' });
      if (!sqlite && (body.table != null || body.expected_schema != null))
        return res
          .status(400)
          .json({ error: 'JSON fixtures do not select database tables.' });
      records = body.records;
      if (!rowsValid(records))
        return res
          .status(400)
          .json({ error: 'Provide 1–100 nonempty record objects.' });
      payload = sqlite
        ? {
            target: 'sqlite',
            table: body.table,
            expected_schema: body.expected_schema,
            records,
          }
        : { target: 'json', records };
    }
    if (hasUnsafeNumber(records))
      return res.status(400).json({
        error:
          'Record numbers must be finite and integer IDs must be safely representable without rounding.',
      });
    if (Buffer.byteLength(JSON.stringify(records), 'utf8') > 128 * 1024)
      return res
        .status(413)
        .json({ error: 'The record batch exceeds 128 KiB.' });
    if (insert)
      Object.assign(payload, {
        expected_preview_digest: body.expected_preview_digest,
        confirm: true,
      });
  }
  try {
    const response = await fetch(
      new URL(
        `/api/projects/${projectId}/data/${route}`,
        process.env.REQLICA_CONSOLE_URL || 'http://127.0.0.1:3001',
      ),
      {
        method: req.method,
        headers: { 'Content-Type': 'application/json' },
        body: payload ? JSON.stringify(payload) : undefined,
        signal: AbortSignal.timeout(60000),
        redirect: 'error',
      },
    );
    const data = await response.json();
    if (!response.ok)
      return res.status(response.status).json({
        error:
          typeof data.detail === 'string'
            ? data.detail
            : data.detail?.message ||
              'The data operation was rejected. Review the records and validate again.',
      });
    return res.status(response.status).json(data);
  } catch {
    return res.status(502).json({
      error: insert
        ? 'Insertion could not be confirmed. Records may already have been saved. Check live data before validating another batch; do not resend this insertion.'
        : 'Cannot confirm the data preview or schema response. Check that the Reqlica console is running. No live insertion was requested.',
    });
  }
}
