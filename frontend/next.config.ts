import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import type { NextConfig } from 'next';

// Restore dashboard connection settings before API routes start.
try {
  const saved = JSON.parse(
    readFileSync(join(process.cwd(), '.reqlica-connections.json'), 'utf8'),
  );
  for (const [field, variable] of [
    ['console', 'REQLICA_CONSOLE_URL'],
    ['runtime', 'REQLICA_RUNTIME_URL'],
  ]) {
    if (
      typeof saved[field] === 'string' &&
      /^https?:\/\/(localhost|127(?:\.\d{1,3}){3}|\[::1\])(?::\d+)?$/.test(
        saved[field],
      )
    )
      process.env[variable] = saved[field];
  }
} catch {
  /* First launch uses environment configuration. */
}

const nextConfig: NextConfig = {
  /* config options here */
  reactStrictMode: true,
};

export default nextConfig;
