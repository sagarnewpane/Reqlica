import { useEffect, useState } from 'react';
import Select from '@/components/ui/Select/Select';
import type { ReasoningMode } from '@/lib/settings';
import styles from './ReasoningSelector.module.css';

const labels: Record<ReasoningMode, string> = {
  default: 'Provider default',
  off: 'Off',
  minimal: 'Minimal',
  low: 'Low',
  medium: 'Medium',
  high: 'High',
  xhigh: 'Extra high',
  max: 'Maximum',
};

export default function ReasoningSelector({
  id,
  provider,
  model,
  baseUrl,
  value,
  onChange,
  disabled,
}: {
  id: string;
  provider: string;
  model: string;
  baseUrl: string;
  value: ReasoningMode;
  onChange: (value: ReasoningMode) => void;
  disabled?: boolean;
}) {
  const [modes, setModes] = useState<ReasoningMode[]>(['default', 'off']);
  const [message, setMessage] = useState(
    'Choose a model to see its reasoning options.',
  );
  const [loading, setLoading] = useState(false);
  const [loadedScope, setLoadedScope] = useState('');
  const scope = JSON.stringify([provider, model.trim(), baseUrl.trim()]);
  const hasModel =
    !!model.trim() && (provider !== 'compatible' || !!baseUrl.trim());

  useEffect(() => {
    if (!model.trim() || (provider === 'compatible' && !baseUrl.trim())) {
      return;
    }
    const controller = new AbortController();
    const timer = setTimeout(async () => {
      setLoading(true);
      try {
        const response = await fetch('/api/ui/ai/reasoning-options', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            provider,
            model: model.trim(),
            base_url: baseUrl.trim() || null,
          }),
          signal: controller.signal,
        });
        const data = await response.json();
        if (!response.ok)
          throw new Error(data.error || 'Could not load reasoning options.');
        if (!controller.signal.aborted) {
          setModes(data.modes);
          setMessage(data.message);
          setLoadedScope(
            JSON.stringify([provider, model.trim(), baseUrl.trim()]),
          );
        }
      } catch (error) {
        if (!controller.signal.aborted) {
          setModes(['default']);
          setMessage((error as Error).message);
          setLoadedScope(
            JSON.stringify([provider, model.trim(), baseUrl.trim()]),
          );
        }
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }, 250);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [provider, model, baseUrl]);

  const checking = hasModel && (loading || loadedScope !== scope);
  const shownModes: ReasoningMode[] =
    hasModel && loadedScope === scope ? modes : ['default', 'off'];
  const available = shownModes.includes(value);
  return (
    <div className={styles.root}>
      <Select
        id={id}
        value={value}
        disabled={disabled || checking || !hasModel}
        onValueChange={(next) => onChange(next as ReasoningMode)}
      >
        {!available && (
          <option value={value}>{labels[value]} (unavailable)</option>
        )}
        {shownModes.map((mode) => (
          <option key={mode} value={mode}>
            {labels[mode]}
          </option>
        ))}
      </Select>
      <p className={styles.hint} role="status">
        {!hasModel
          ? 'Choose a model to see its reasoning options.'
          : checking
            ? 'Checking model options…'
            : !available
              ? 'This saved level is unavailable for the selected model. Choose Provider default.'
              : message}
      </p>
    </div>
  );
}
