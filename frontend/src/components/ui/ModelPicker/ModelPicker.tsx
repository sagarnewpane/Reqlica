import { useEffect, useState } from 'react';
import * as Popover from '@radix-ui/react-popover';
import { Command } from 'cmdk';
import { Check, ChevronsUpDown, RefreshCw, Search } from 'lucide-react';
import styles from './ModelPicker.module.css';
type Model = { id: string; name: string };
type Props = {
  id: string;
  provider: string;
  baseUrl?: string;
  projectId?: string;
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
  refreshToken?: number;
};
export default function ModelPicker({
  id,
  provider,
  baseUrl = '',
  value,
  onChange,
  disabled,
  refreshToken = 0,
}: Props) {
  const [models, setModels] = useState<Model[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [note, setNote] = useState('');
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    if (disabled || (!open && refresh === 0 && refreshToken === 0)) return;
    const controller = new AbortController();
    const timer = setTimeout(async () => {
      setModels([]);
      setError('');
      setNote('');
      if (provider === 'compatible' && !baseUrl.trim()) {
        setNote('Enter the provider URL to load models.');
        return;
      }
      setLoading(true);
      try {
        const response = await fetch('/api/ui/ai/models', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({
            provider,
            base_url: baseUrl.trim() || null,
          }),
          signal: controller.signal,
        });
        const data = await response.json();
        if (controller.signal.aborted) return;
        if (!response.ok)
          throw new Error(
            response.status === 401 || response.status === 403
              ? 'Check your API key, then reload models.'
              : data.error,
          );
        setModels(Array.isArray(data.models) ? data.models : []);
        setNote(data.message);
      } catch (err) {
        if (!controller.signal.aborted)
          setError((err as Error).message || 'Could not load models.');
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }, 400);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [provider, baseUrl, disabled, refresh, refreshToken, open]);
  return (
    <div className={styles.root}>
      <div className={styles.row}>
        <Popover.Root
          open={open}
          onOpenChange={(next) => {
            setOpen(next);
            if (!next) setQuery('');
          }}
        >
          <Popover.Trigger asChild>
            <button
              id={id}
              type="button"
              role="combobox"
              aria-expanded={open}
              aria-controls={`${id}-models`}
              disabled={disabled}
              className={styles.trigger}
            >
              <span>
                {value || (loading ? 'Loading models…' : 'Select a model')}
              </span>
              <ChevronsUpDown size={16} />
            </button>
          </Popover.Trigger>
          <Popover.Portal>
            <Popover.Content
              className={styles.content}
              sideOffset={6}
              align="start"
            >
              <Command>
                <div className={styles.search}>
                  <Search size={16} />
                  <Command.Input
                    autoFocus
                    placeholder="Search or enter a model ID…"
                    aria-label="Search or enter a model ID"
                    value={query}
                    onValueChange={setQuery}
                  />
                </div>
                <Command.List id={`${id}-models`} className={styles.list}>
                  <Command.Empty>
                    {loading
                      ? 'Loading models…'
                      : 'No matching models. Enter an exact ID below.'}
                  </Command.Empty>
                  {models.map((model) => (
                    <Command.Item
                      key={model.id}
                      value={model.id}
                      keywords={[model.name]}
                      onSelect={() => {
                        onChange(model.id);
                        setOpen(false);
                      }}
                    >
                      <span>
                        <strong>{model.name}</strong>
                        {model.name !== model.id && <small>{model.id}</small>}
                      </span>
                      {value === model.id && <Check size={16} />}
                    </Command.Item>
                  ))}
                </Command.List>
                {error && (
                  <p className={styles.popoverError} role="status">
                    {error} Enter an exact ID below.
                  </p>
                )}
                <div className={styles.manualAction}>
                  <span>
                    {loading
                      ? 'Loading catalog…'
                      : error
                        ? 'Catalog unavailable'
                        : `${models.length} models`}
                  </span>
                  <button
                    type="button"
                    disabled={!query.trim() || query.trim().length > 200}
                    onClick={() => {
                      onChange(query.trim());
                      setOpen(false);
                      setQuery('');
                    }}
                  >
                    Use exact ID
                  </button>
                </div>
              </Command>
            </Popover.Content>
          </Popover.Portal>
        </Popover.Root>
        <button
          type="button"
          aria-label="Reload models"
          title="Reload models"
          disabled={disabled || loading}
          className={styles.refresh}
          onClick={() => setRefresh((count) => count + 1)}
        >
          <RefreshCw size={16} className={loading ? styles.spin : undefined} />
        </button>
      </div>
      {!disabled && (
        <>{!error && note && <p className={styles.note}>{note}</p>}</>
      )}
    </div>
  );
}
