export type AgentEvent = {
  kind: string;
  text?: string;
  name?: string;
  status?: string;
  detail?: string;
  path?: string;
  route?: string;
};
export type TokenUsage = {
  prompt_tokens?: number;
  completion_tokens?: number;
  total_tokens?: number;
  reasoning_tokens?: number;
  cached_tokens?: number;
};
export type ContextSummary = {
  bytes: number;
  messages_bytes: number;
  tools_bytes: number;
  message_count: number;
  tool_count: number;
  max_output_tokens: number;
  model_call: number;
};
export type ActivityEvent = { stage: string; elapsed_ms: number };
export type GenerationActivity = {
  phase: 'saving' | 'refreshing' | 'step' | 'model' | 'tool';
  tool?: string;
  startedAt: number;
  events: ActivityEvent[];
};
export type ModelContext = {
  source: 'captured' | 'reconstructed';
  messages: { role: string; content?: unknown; [key: string]: unknown }[];
  tools: unknown[];
  summary: ContextSummary;
  last_model_response?: {
    usage?: TokenUsage | null;
    status?: string;
    finish_reason?: string;
  } | null;
};
export type AgentRun = {
  data_preview?: import('./sample-data').DataPreview | null;
  specification?: { filename: string; kind: string } | null;
  id: string;
  message: string;
  status: string;
  answer: string | null;
  saved_work?: string | null;
  revision: string;
  turn: number;
  last_reply_id?: string;
  draft_id: string | null;
  can_reply: boolean;
  can_resume: boolean;
  model_calls: number;
  tool_calls: number;
  events: AgentEvent[];
  error: { message?: string } | null;
  notice?: string;
  provider: {
    provider: string;
    model: string;
    base_url?: string;
    reasoning_mode?: ReasoningMode;
  };
  can_reset_reasoning?: boolean;
  allow_edits: boolean;
  allow_execution: boolean;
  usage?: (TokenUsage | null)[];
  context?: ContextSummary | null;
  last_model_response?: {
    usage?: TokenUsage | null;
    status?: string;
    finish_reason?: string;
  } | null;
  next_action?: { kind: 'model' | 'tool'; name?: string } | null;
  limits?: {
    model_calls: number;
    model_calls_per_turn?: number;
    tool_calls: number;
    total_tokens?: number;
  };
  reported_tokens?: number;
  turn_model_calls?: number;
};

export class WorkspaceError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}

export async function workspaceRequest<T>(
  projectId: string,
  path: string,
  method = 'GET',
  body?: unknown,
  key?: string,
  options?: { activityId?: string; signal?: AbortSignal },
): Promise<T> {
  const response = await fetch(`/api/projects/${projectId}/${path}`, {
    method,
    headers: {
      'Content-Type': 'application/json',
      ...(key ? { 'X-Reqlica-Groq-Key': key } : {}),
      ...(options?.activityId
        ? { 'X-Reqlica-Activity-ID': options.activityId }
        : {}),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal: options?.signal,
  });
  const data = await response.json();
  if (!response.ok)
    throw new WorkspaceError(
      data.error || 'Unable to complete the request.',
      response.status,
    );
  return data;
}

export function conversationMessages(run: AgentRun): AgentEvent[] {
  return [
    { kind: 'user', text: run.message },
    ...run.events.filter(
      (event) =>
        event.kind === 'tool' ||
        ((event.kind === 'user' || event.kind === 'assistant') && !!event.text),
    ),
    ...(run.answer ? [{ kind: 'assistant', text: run.answer }] : []),
  ];
}
import type { ReasoningMode } from './settings';
