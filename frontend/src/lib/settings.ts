export type ReasoningMode =
  'default' | 'off' | 'minimal' | 'low' | 'medium' | 'high' | 'xhigh' | 'max';

export type ChatSettings = {
  provider: 'groq' | 'openai' | 'ollama' | 'compatible';
  model: string;
  baseUrl: string;
  reasoningMode: ReasoningMode;
  allowEdits: boolean;
  allowExecution: boolean;
  sendWithEnter: boolean;
  maxTokens: number;
  totalTokenLimit: number;
  toolCallLimit: number;
  modelCallLimit: number;
  perTurnModelCallLimit: number;
  timeoutSeconds: number;
};
export const defaultSettings: ChatSettings = {
  provider: 'groq',
  model: '',
  baseUrl: '',
  reasoningMode: 'off',
  allowEdits: true,
  allowExecution: false,
  sendWithEnter: true,
  maxTokens: 8192,
  totalTokenLimit: 100000,
  toolCallLimit: 64,
  modelCallLimit: 24,
  perTurnModelCallLimit: 16,
  timeoutSeconds: 300,
};
const key = (projectId?: string) =>
  projectId
    ? `reqlica.settings.project.${projectId}`
    : 'reqlica.settings.workspace';

export function validateSettings(value: ChatSettings): string | null {
  if (!['groq', 'openai', 'ollama', 'compatible'].includes(value.provider))
    return 'Choose a supported provider.';
  if (typeof value.model !== 'string' || value.model.trim().length > 200)
    return 'Model ID must be at most 200 characters.';
  if (typeof value.baseUrl !== 'string' || value.baseUrl.length > 2048)
    return 'Enter a valid provider URL.';
  if (
    ![
      'default',
      'off',
      'minimal',
      'low',
      'medium',
      'high',
      'xhigh',
      'max',
    ].includes(value.reasoningMode)
  )
    return 'Choose a valid reasoning level.';
  if (value.provider === 'compatible' && !value.baseUrl.trim())
    return 'Enter a base URL for the compatible provider.';
  if (value.baseUrl.trim()) {
    try {
      const url = new URL(value.baseUrl.trim());
      if (
        url.username ||
        url.password ||
        url.search ||
        url.hash ||
        (url.protocol !== 'https:' &&
          !(
            url.protocol === 'http:' &&
            ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname)
          ))
      )
        return 'Use HTTPS or local HTTP, without credentials, query parameters, or fragments.';
    } catch {
      return 'Enter a complete provider URL.';
    }
  }
  if (
    !Number.isInteger(value.maxTokens) ||
    value.maxTokens < 256 ||
    value.maxTokens > 65536
  )
    return 'Response limit must be between 256 and 65,536 tokens.';
  if (
    !Number.isInteger(value.totalTokenLimit) ||
    value.totalTokenLimit < 1000 ||
    value.totalTokenLimit > 1000000
  )
    return 'Cumulative token limit must be between 1,000 and 1,000,000 tokens.';
  if (
    !Number.isInteger(value.toolCallLimit) ||
    value.toolCallLimit < 1 ||
    value.toolCallLimit > 1000
  )
    return 'Tool-call limit must be between 1 and 1,000.';
  if (
    !Number.isInteger(value.modelCallLimit) ||
    value.modelCallLimit < 1 ||
    value.modelCallLimit > 1000
  )
    return 'Model-call limit must be between 1 and 1,000.';
  if (
    !Number.isInteger(value.perTurnModelCallLimit) ||
    value.perTurnModelCallLimit < 1 ||
    value.perTurnModelCallLimit > 1000
  )
    return 'Per-request model-call limit must be between 1 and 1,000.';
  if (value.perTurnModelCallLimit > value.modelCallLimit)
    return 'Per-request model calls cannot exceed the per-chat model-call limit.';
  if (
    !Number.isInteger(value.timeoutSeconds) ||
    value.timeoutSeconds < 30 ||
    value.timeoutSeconds > 600
  )
    return 'Request timeout must be between 30 and 600 seconds.';
  if (
    [value.allowEdits, value.allowExecution, value.sendWithEnter].some(
      (item) => typeof item !== 'boolean',
    )
  )
    return 'Invalid preference value.';
  return null;
}
function saved(projectId?: string): ChatSettings | null {
  const raw = localStorage.getItem(key(projectId));
  if (!raw) return null;
  try {
    const data = JSON.parse(raw);
    // Pick only known fields. Credentials never belong in saved preferences.
    const value: ChatSettings = Object.fromEntries(
      Object.keys(defaultSettings).map((name) => [
        name,
        data[name] ?? defaultSettings[name as keyof ChatSettings],
      ]),
    ) as ChatSettings;
    return validateSettings(value) ? null : value;
  } catch {
    return null;
  }
}
export function readSettings(projectId?: string): ChatSettings {
  const preferences = (projectId && saved(projectId)) ||
    saved() || { ...defaultSettings };
  try {
    const raw = localStorage.getItem('reqlica.settings.connection.v1');
    if (!raw) return preferences;
    const connection = JSON.parse(raw);
    const shared = {
      provider: connection.provider,
      model: connection.model,
      baseUrl: connection.baseUrl,
      reasoningMode: connection.reasoningMode ?? defaultSettings.reasoningMode,
    };
    const value = { ...preferences, ...shared };
    return validateSettings(value) ? preferences : value;
  } catch {
    return preferences;
  }
}
export function hasProjectSettings(projectId: string) {
  return !!saved(projectId);
}
export function saveSettings(value: ChatSettings, projectId?: string) {
  const error = validateSettings(value);
  if (error) throw new Error(error);
  const safe = Object.fromEntries(
    Object.keys(defaultSettings).map((name) => [
      name,
      value[name as keyof ChatSettings],
    ]),
  );
  localStorage.setItem(key(projectId), JSON.stringify(safe));
  localStorage.setItem(
    'reqlica.settings.connection.v1',
    JSON.stringify({
      provider: value.provider,
      model: value.model,
      baseUrl: value.baseUrl,
      reasoningMode: value.reasoningMode,
    }),
  );
}
export function clearProjectSettings(projectId: string) {
  localStorage.removeItem(key(projectId));
}
