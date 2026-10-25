import { useEffect, useRef, useState } from 'react';
import { Eye, EyeOff } from 'lucide-react';
import Button from '@/components/ui/Button/Button';
import Input from '@/components/ui/Input/Input';
import { workspaceRequest } from '@/lib/chat';
import styles from './Settings.module.css';

type KeyStatus = { configured: boolean };
type KeyReveal = { api_key: string };

export default function ProviderKeySettings({
  projectId,
  provider,
  baseUrl,
  onChanged,
}: {
  projectId: string;
  provider: string;
  baseUrl: string;
  onChanged: (configured: boolean) => void;
}) {
  const [configured, setConfigured] = useState(false);
  const [value, setValue] = useState('');
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [revealing, setRevealing] = useState(false);
  const [revealed, setRevealed] = useState<{
    scope: string;
    key: string;
  } | null>(null);
  const revealRequest = useRef<AbortController | null>(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [attempt, setAttempt] = useState(0);
  const endpoint = { provider, base_url: baseUrl.trim() || null };
  const scope = JSON.stringify([projectId, provider, baseUrl.trim()]);
  const [statusScope, setStatusScope] = useState('');
  const checking = loading || statusScope !== scope;
  const visibleKey = revealed?.scope === scope ? revealed.key : null;

  useEffect(() => {
    revealRequest.current?.abort();
    let active = true;
    Promise.resolve().then(() => {
      if (active) setRevealed(null);
    });
    return () => {
      active = false;
    };
  }, [scope]);

  useEffect(() => {
    let active = true;
    const timer = setTimeout(
      () =>
        workspaceRequest<KeyStatus>(projectId, 'ai/key/status', 'POST', {
          provider,
          base_url: baseUrl.trim() || null,
        })
          .then((data) => {
            if (active) {
              setConfigured(data.configured);
              if (!data.configured) setRevealed(null);
              setStatusScope(
                JSON.stringify([projectId, provider, baseUrl.trim()]),
              );
              setError('');
            }
          })
          .catch((err) => {
            if (active) {
              setConfigured(false);
              setStatusScope(
                JSON.stringify([projectId, provider, baseUrl.trim()]),
              );
              setError((err as Error).message);
            }
          })
          .finally(() => {
            if (active) setLoading(false);
          }),
      350,
    );
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [projectId, provider, baseUrl, attempt]);

  async function update(remove = false) {
    if (busy || checking || revealing) return;
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const data = await workspaceRequest<KeyStatus>(
        projectId,
        'ai/key',
        remove ? 'DELETE' : 'PUT',
        {
          ...endpoint,
          ...(!remove ? { api_key: value.trim() } : {}),
        },
      );
      if (!remove && !data.configured)
        throw new Error('The key was not saved. Try again.');
      setConfigured(data.configured);
      setValue('');
      setRevealed(null);
      setNotice(
        remove ? 'API key removed.' : 'API key saved for this project.',
      );
      onChanged(data.configured);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function toggleReveal() {
    if (visibleKey) {
      setRevealed(null);
      return;
    }
    if (!configured || checking || busy || revealing) return;
    const controller = new AbortController();
    revealRequest.current = controller;
    const requestedScope = scope;
    setRevealing(true);
    setError('');
    try {
      const data = await workspaceRequest<KeyReveal>(
        projectId,
        'ai/key/reveal',
        'POST',
        endpoint,
        undefined,
        { signal: controller.signal },
      );
      if (!controller.signal.aborted && data.api_key) {
        setRevealed({ scope: requestedScope, key: data.api_key });
      }
    } catch (err) {
      if (!controller.signal.aborted) setError((err as Error).message);
    } finally {
      if (revealRequest.current === controller) {
        revealRequest.current = null;
        setRevealing(false);
      }
    }
  }

  return (
    <div className={styles.catalogKey}>
      {configured && !checking && (
        <div className={styles.savedKeyPanel}>
          <div className={styles.savedKeyHeading}>
            <strong>Saved API key</strong>
            <span className={styles.keyAvailable}>
              Available for models and chat
            </span>
          </div>
          <div className={styles.savedKeyRow}>
            <code
              className={styles.savedKeyText}
              aria-label={
                visibleKey ? 'Saved API key visible' : 'Saved API key hidden'
              }
            >
              {visibleKey || '•••• •••• •••• ••••'}
            </code>
            <button
              type="button"
              className={styles.secondary}
              disabled={busy || revealing}
              onClick={() => void toggleReveal()}
            >
              {visibleKey ? (
                <EyeOff size={15} aria-hidden="true" />
              ) : (
                <Eye size={15} aria-hidden="true" />
              )}
              {revealing ? 'Loading…' : visibleKey ? 'Hide key' : 'Show key'}
            </button>
          </div>
        </div>
      )}
      <label htmlFor="provider-api-key">
        {configured && !checking ? 'Replace API key' : 'API key'}
      </label>
      <Input
        id="provider-api-key"
        type="password"
        autoComplete="off"
        value={value}
        disabled={checking || busy || revealing}
        maxLength={8192}
        onChange={(event) => {
          setValue(event.target.value);
          setNotice('');
        }}
        onKeyDown={(event) => {
          if (event.key === 'Enter') {
            event.preventDefault();
            if (value.trim()) void update();
          }
        }}
        placeholder={checking ? 'Loading…' : 'Enter a new API key'}
      />
      <div className={styles.keyActions}>
        <p>
          {checking
            ? 'Checking saved key for this provider…'
            : configured
              ? 'The saved key remains available until you replace or remove it.'
              : provider === 'ollama'
                ? 'Optional for local Ollama. Save a key if this server requires one.'
                : 'Save a key to load models and use chat.'}
        </p>
        <div className={styles.actions}>
          {configured && (
            <button
              type="button"
              className={styles.secondary}
              disabled={busy || checking || revealing}
              onClick={() => void update(true)}
            >
              Remove key
            </button>
          )}
          <Button
            type="button"
            disabled={busy || checking || revealing || !value.trim()}
            onClick={() => void update()}
          >
            {busy ? 'Saving…' : 'Save key'}
          </Button>
        </div>
      </div>
      {error && (
        <p role="alert">
          {error}{' '}
          <button
            type="button"
            className={styles.secondary}
            disabled={busy || checking}
            onClick={() => {
              setLoading(true);
              setAttempt((count) => count + 1);
            }}
          >
            Retry
          </button>
        </p>
      )}
      {notice && <p role="status">{notice}</p>}
    </div>
  );
}
