import { useEffect, useRef, useState } from 'react';
import { Eye, EyeOff } from 'lucide-react';
import Button from '@/components/ui/Button/Button';
import Input from '@/components/ui/Input/Input';
import {
  AlertDialog,
  AlertDialogTrigger,
  AlertDialogContent,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogCancel,
  AlertDialogAction,
} from '@/components/ui/shadcn/alert-dialog';
import {
  aiSettingsRequest,
  notifyProviderKeysChanged,
  providerKeysEvent,
  providerKeysStorage,
} from '@/lib/ai-settings';
import styles from './Settings.module.css';

type KeyStatus = { configured: boolean; migration_conflict?: boolean };
type KeyReveal = { api_key: string };

export default function ProviderKeySettings({
  provider,
  baseUrl,
  onChanged,
  onPendingChange,
  onBusyChange,
}: {
  provider: string;
  baseUrl: string;
  onChanged: (configured: boolean) => void;
  onPendingChange: (pending: boolean) => void;
  onBusyChange: (busy: boolean) => void;
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
  const [editing, setEditing] = useState(false);
  const [operation, setOperation] = useState<'save' | 'remove'>('save');
  useEffect(() => {
    onPendingChange(!!value.trim());
    return () => onPendingChange(false);
  }, [value, onPendingChange]);
  useEffect(() => {
    onBusyChange(busy);
    return () => onBusyChange(false);
  }, [busy, onBusyChange]);
  const endpoint = { provider, base_url: baseUrl.trim() || null };
  const scope = JSON.stringify([provider, baseUrl.trim()]);
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
      revealRequest.current?.abort();
    };
  }, [scope]);

  useEffect(() => {
    let active = true;
    const timer = setTimeout(
      () =>
        aiSettingsRequest<KeyStatus>('key/status', 'POST', {
          provider,
          base_url: baseUrl.trim() || null,
        })
          .then((data) => {
            if (active) {
              setConfigured(data.configured);
              if (!data.configured) setRevealed(null);
              setStatusScope(JSON.stringify([provider, baseUrl.trim()]));
              setError(
                data.migration_conflict
                  ? 'Existing projects have different API keys for this provider. Save one app-wide key to choose which all projects will use.'
                  : '',
              );
            }
          })
          .catch((err) => {
            if (active) {
              setConfigured(false);
              setStatusScope(JSON.stringify([provider, baseUrl.trim()]));
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
  }, [provider, baseUrl, attempt]);

  useEffect(() => {
    const refresh = () => {
      revealRequest.current?.abort();
      setRevealed(null);
      setAttempt((value) => value + 1);
    };
    const storage = (event: StorageEvent) => {
      if (event.key === providerKeysStorage || event.key === null) refresh();
    };
    window.addEventListener(providerKeysEvent, refresh);
    window.addEventListener('storage', storage);
    window.addEventListener('focus', refresh);
    return () => {
      window.removeEventListener(providerKeysEvent, refresh);
      window.removeEventListener('storage', storage);
      window.removeEventListener('focus', refresh);
    };
  }, []);

  async function update(remove = false) {
    if (busy || checking || revealing) return;
    setBusy(true);
    setOperation(remove ? 'remove' : 'save');
    setError('');
    setNotice('');
    try {
      const data = await aiSettingsRequest<KeyStatus>(
        'key',
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
      setEditing(false);
      setNotice(
        remove
          ? 'API key removed from the app.'
          : 'API key saved for all projects.',
      );
      notifyProviderKeysChanged();
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
      const data = await aiSettingsRequest<KeyReveal>(
        'key/reveal',
        'POST',
        endpoint,
        controller.signal,
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
      <div className={styles.savedKeyHeading}>
        <strong>App API key</strong>
        <span className={styles.keyAvailable} role="status">
          {checking
            ? 'Checking…'
            : error && statusScope === scope
              ? 'Check connection'
              : configured
                ? 'Key saved'
                : provider === 'ollama'
                  ? 'Optional for local Ollama'
                  : 'No saved key'}
        </span>
      </div>
      <p>
        Shared by every project on this app, scoped to the provider
        {baseUrl.trim() ? ' URL' : ''}. Never included in model profiles. Saving
        a key does not verify it with the provider.
      </p>
      {configured && !checking && (
        <div className={styles.savedKeyPanel}>
          <div className={styles.savedKeyHeading}>
            <strong>Saved API key</strong>
            <span className={styles.keyAvailable}>
              Used for model discovery and chat
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
            <Button
              type="button"
              variant="secondary"
              disabled={busy || revealing}
              onClick={() => void toggleReveal()}
            >
              {visibleKey ? (
                <EyeOff size={15} aria-hidden="true" />
              ) : (
                <Eye size={15} aria-hidden="true" />
              )}
              {revealing ? 'Loading…' : visibleKey ? 'Hide key' : 'Show key'}
            </Button>
          </div>
        </div>
      )}
      {(!configured || editing) && (
        <>
          <label htmlFor="provider-api-key">
            {configured && !checking ? 'Replace API key' : 'API key'}
          </label>
          <Input
            id="provider-api-key"
            type="password"
            autoComplete="off"
            aria-describedby="key-help"
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
            <p id="key-help">
              {checking
                ? 'Checking saved key for this provider…'
                : configured
                  ? 'The saved key remains available until you replace or remove it.'
                  : provider === 'ollama'
                    ? 'Optional for local Ollama. Save a key if this server requires one.'
                    : 'Save a key to load models and use chat.'}
            </p>
            <div className={styles.actions}>
              <Button
                type="button"
                variant="secondary"
                disabled={busy || checking || revealing}
                onClick={() => {
                  setValue('');
                  setEditing(false);
                  setError('');
                }}
              >
                Cancel
              </Button>
              <Button
                type="button"
                disabled={busy || checking || revealing || !value.trim()}
                onClick={() => void update()}
              >
                {busy
                  ? 'Saving…'
                  : configured
                    ? 'Replace saved key'
                    : 'Save app key'}
              </Button>
            </div>
          </div>
        </>
      )}
      {configured && !editing && !checking && (
        <div className={styles.keyActions}>
          <Button
            type="button"
            variant="secondary"
            disabled={busy || revealing}
            onClick={() => {
              setEditing(true);
              setRevealed(null);
              setNotice('');
            }}
          >
            Replace key
          </Button>
          <AlertDialog>
            <AlertDialogTrigger asChild>
              <Button
                type="button"
                variant="ghost"
                disabled={busy || revealing}
              >
                {busy && operation === 'remove' ? 'Removing…' : 'Remove key'}
              </Button>
            </AlertDialogTrigger>
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>Remove this app’s API key?</AlertDialogTitle>
                <AlertDialogDescription>
                  Models and chat in every project using this provider may stop
                  working until you save another key. Model profiles and
                  defaults will not be deleted.
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel>Keep key</AlertDialogCancel>
                <AlertDialogAction
                  variant="danger"
                  onClick={() => void update(true)}
                >
                  Remove key
                </AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
        </div>
      )}
      {error && (
        <p className={styles.error} role="alert">
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
      {notice && (
        <p className={styles.notice} role="status">
          {notice}
        </p>
      )}
    </div>
  );
}
