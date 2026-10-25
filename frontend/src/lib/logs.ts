export const LOG_KINDS = [
  'request',
  'runtime_error',
  'startup_error',
  'stdout',
  'stderr',
] as const;
export type LogKind = (typeof LOG_KINDS)[number];
export type LogFilters = { kind: LogKind | 'all'; requestId: string };
export type CapturedBody = {
  text?: string;
  truncated?: boolean;
  bytes?: number;
  omitted?: string;
};
export type LogEvent = {
  id: number;
  kind: LogKind;
  timestamp: string;
  request_id?: string | null;
  method?: string;
  path?: string;
  status_code?: number;
  duration_ms?: number;
  response_bytes?: number | null;
  outcome?: string;
  exception_type?: string;
  frames?: { file: string; line: number; function: string }[];
  text?: string;
  truncated?: boolean;
  request_body?: CapturedBody | null;
  response_body?: CapturedBody | null;
};
export type LogPage = {
  project_id: string;
  events: LogEvent[];
  next_before_id: number | null;
};
export type CaptureSettings = {
  revision: string;
  capture_payloads: boolean;
  capture_output: boolean;
  max_body_bytes: number;
};

export function isRequestId(value: string) {
  return /^[a-f0-9]{32}$/.test(value);
}

export function logListPath(filters: LogFilters, beforeId?: number) {
  const requestId = filters.requestId.trim();
  if (requestId && !isRequestId(requestId))
    throw new Error(
      'Request ID must contain exactly 32 lowercase hexadecimal characters.',
    );
  if (filters.kind !== 'all' && !LOG_KINDS.includes(filters.kind))
    throw new Error('Choose a valid event kind.');
  if (
    beforeId !== undefined &&
    (!Number.isSafeInteger(beforeId) || beforeId < 1)
  )
    throw new Error('Invalid history cursor.');
  const params = new URLSearchParams({ limit: '50' });
  if (filters.kind !== 'all') params.set('kind', filters.kind);
  if (requestId) params.set('request_id', requestId);
  if (beforeId !== undefined) params.set('before_id', String(beforeId));
  return `logs?${params}`;
}

export function logKindLabel(kind: LogKind) {
  return (
    {
      request: 'Request',
      runtime_error: 'Runtime error',
      startup_error: 'Startup error',
      stdout: 'Stdout',
      stderr: 'Stderr',
    }[kind] || kind
  );
}

export function logTimestamp(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : date.toISOString().replace('T', ' ').replace('Z', ' UTC');
}

export function validateCaptureSettings(settings: CaptureSettings) {
  if (!/^[a-f0-9]{64}$/.test(settings.revision))
    return 'Reload capture settings to obtain their current revision.';
  if (
    typeof settings.capture_payloads !== 'boolean' ||
    typeof settings.capture_output !== 'boolean'
  )
    return 'Choose explicit capture settings.';
  if (
    !Number.isInteger(settings.max_body_bytes) ||
    settings.max_body_bytes < 128 ||
    settings.max_body_bytes > 2048
  )
    return 'Body capture limit must be an integer between 128 and 2,048 bytes.';
  return null;
}
