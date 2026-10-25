export const providerKeysEvent = 'reqlica:provider-keys';
export const providerKeysStorage = 'reqlica.provider-keys.changed';
export async function aiSettingsRequest<T>(
  path: string,
  method: string,
  body: unknown,
  signal?: AbortSignal,
): Promise<T> {
  const response = await fetch(`/api/ui/ai/${path}`, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  });
  const data = await response.json();
  if (!response.ok)
    throw new Error(data.error || 'Cannot access app API settings.');
  return data;
}
export function notifyProviderKeysChanged() {
  window.dispatchEvent(new Event(providerKeysEvent));
  try {
    localStorage.setItem(providerKeysStorage, `${Date.now()}-${Math.random()}`);
  } catch {
    /* Status still refreshes on window focus. */
  }
}
