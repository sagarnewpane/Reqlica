import type { AgentRun, GenerationActivity, TokenUsage } from './chat';

const stages: Record<string, string> = {
  received: 'Preparing request',
  provider_request: 'Waiting for the model',
  provider_accepted: 'Receiving model response',
  provider_rejected: 'Provider rejected the request',
  provider_response: 'Model response received',
  checking: 'Processing model response',
  completed: 'Saving result',
  failed: 'Request failed',
};

export function activityLabel(activity: GenerationActivity | null, pausing: boolean) {
  if (pausing) return 'Pausing after this step';
  if (!activity) return 'Working';
  if (activity.phase === 'saving') return 'Saving your message';
  if (activity.phase === 'refreshing') return 'Loading saved state';
  if (activity.phase === 'tool') return activity.tool ? `Running ${activity.tool.replaceAll('_', ' ')}` : 'Running a tool';
  const last = activity.events.at(-1);
  return (last && stages[last.stage]) || (activity.phase === 'model' ? 'Waiting for the model' : 'Processing next step');
}

export function totalUsage(run: Pick<AgentRun, 'usage' | 'last_model_response'>): TokenUsage | null {
  const entries = [...(run.usage || [])];
  // Failed responses can carry usage even when older checkpoints omit it from usage[].
  if (!entries.length && run.last_model_response?.usage) entries.push(run.last_model_response.usage);
  const total: TokenUsage = {};
  for (const usage of entries) {
    if (!usage) continue;
    for (const key of ['prompt_tokens', 'completion_tokens', 'reasoning_tokens', 'cached_tokens'] as const) {
      const value = usage[key];
      if (typeof value === 'number' && Number.isFinite(value) && value >= 0) total[key] = (total[key] || 0) + value;
    }
    const tokens = usage.total_tokens ?? (
      usage.prompt_tokens !== undefined && usage.completion_tokens !== undefined
        ? usage.prompt_tokens + usage.completion_tokens : undefined
    );
    if (typeof tokens === 'number' && Number.isFinite(tokens) && tokens >= 0)
      total.total_tokens = (total.total_tokens || 0) + tokens;
  }
  return Object.keys(total).length ? total : null;
}

export function formatBytes(bytes: number) {
  return bytes < 1024 ? `${bytes} B` : `${(bytes / 1024).toFixed(1)} KiB`;
}

export function formatElapsed(seconds: number) {
  return seconds < 60 ? `${seconds}s` : `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}
