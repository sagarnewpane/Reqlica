import {
  defaultSettings,
  validateSettings,
  type ChatSettings,
} from './settings';
export type ModelConnection = Pick<
  ChatSettings,
  'provider' | 'model' | 'baseUrl' | 'reasoningMode'
>;
export type ModelProfile = ModelConnection & { id: string; name: string };
const key = 'reqlica.model-profiles.v1';
export function modelConnection(value: ModelConnection): ModelConnection {
  return {
    provider: value.provider,
    model: value.model.trim(),
    baseUrl: value.baseUrl.trim(),
    reasoningMode: value.reasoningMode || 'default',
  };
}
function valid(profile: ModelProfile) {
  return (
    typeof profile?.id === 'string' &&
    /^[a-f0-9-]{36}$/.test(profile.id) &&
    typeof profile.name === 'string' &&
    profile.name.trim().length > 0 &&
    profile.name.length <= 80 &&
    typeof profile.model === 'string' &&
    !!profile.model.trim() &&
    !validateSettings({ ...defaultSettings, ...modelConnection(profile) })
  );
}
export function readModelProfiles(): ModelProfile[] {
  const raw = localStorage.getItem(key);
  if (!raw) return [];
  try {
    const value = JSON.parse(raw);
    if (!Array.isArray(value)) return [];
    return value
      .slice(0, 30)
      .filter((item) => {
        try {
          return valid(item);
        } catch {
          return false;
        }
      })
      .map((item) => ({
        id: item.id,
        name: item.name.trim(),
        ...modelConnection(item),
      }));
  } catch {
    return [];
  }
}
export function saveModelProfile(profile: ModelProfile): ModelProfile[] {
  if (!valid(profile))
    throw new Error(
      'Enter a profile name, a model ID, and valid connection settings.',
    );
  const profiles = readModelProfiles();
  if (
    profiles.some(
      (item) =>
        item.id !== profile.id &&
        item.name.toLowerCase() === profile.name.trim().toLowerCase(),
    )
  )
    throw new Error('A profile with this name already exists.');
  if (!profiles.some((item) => item.id === profile.id) && profiles.length >= 30)
    throw new Error('You can save up to 30 model profiles.');
  const clean = {
    id: profile.id,
    name: profile.name.trim(),
    ...modelConnection(profile),
  };
  const next = profiles.some((item) => item.id === profile.id)
    ? profiles.map((item) => (item.id === profile.id ? clean : item))
    : [...profiles, clean];
  localStorage.setItem(key, JSON.stringify(next));
  return next;
}
export function deleteModelProfile(id: string): ModelProfile[] {
  const next = readModelProfiles().filter((item) => item.id !== id);
  localStorage.setItem(key, JSON.stringify(next));
  return next;
}
