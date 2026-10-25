// Server-only configuration; never import into browser components.
import { readFileSync, writeFileSync, renameSync } from 'node:fs';
import { join } from 'node:path';
import { randomUUID } from 'node:crypto';
const file = join(process.cwd(), '.reqlica-connections.json');
export type Connections = { console: string; runtime: string };
export function connectionOrigins(): Connections {
  const defaults = {
    console: process.env.REQLICA_CONSOLE_URL || 'http://127.0.0.1:3001',
    runtime: process.env.REQLICA_RUNTIME_URL || 'http://127.0.0.1:4000',
  };
  try {
    const raw = readFileSync(file, 'utf8');
    if (raw.length > 8192) return defaults;
    const value = JSON.parse(raw);
    return validateConnections(value);
  } catch {
    return defaults;
  }
}
export function validateConnections(value: Connections): Connections {
  const result = {} as Connections;
  for (const key of ['console', 'runtime'] as const) {
    if (typeof value[key] !== 'string')
      throw new Error('Enter both service URLs.');
    const url = new URL(value[key]);
    const hostname = url.hostname.replace(/^\[|\]$/g, '');
    if (
      !['http:', 'https:'].includes(url.protocol) ||
      !(
        ['localhost', '::1'].includes(hostname) ||
        /^127(?:\.\d{1,3}){3}$/.test(hostname)
      ) ||
      url.username ||
      url.password ||
      url.search ||
      url.hash ||
      url.pathname !== '/'
    )
      throw new Error(
        'Use a loopback HTTP(S) origin without credentials, paths, query or fragment.',
      );
    result[key] = url.origin;
  }
  if (result.console === result.runtime)
    throw new Error('Console and runtime must use distinct origins.');
  return result;
}
export function saveConnections(value: Connections) {
  const checked = validateConnections(value);
  const temporary = file + '.' + randomUUID() + '.tmp';
  writeFileSync(temporary, JSON.stringify(checked), { mode: 0o600 });
  renameSync(temporary, file);
  process.env.REQLICA_CONSOLE_URL = checked.console;
  process.env.REQLICA_RUNTIME_URL = checked.runtime;
  return checked;
}
