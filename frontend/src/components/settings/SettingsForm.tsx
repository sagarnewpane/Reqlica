import { useCallback, useEffect, useState, type FormEvent } from 'react';
import ModelProfiles from '@/components/settings/ModelProfiles';
import ProviderKeySettings from './ProviderKeySettings';
import ModelPicker from '@/components/ui/ModelPicker/ModelPicker';
import ReasoningSelector from './ReasoningSelector';
import Switch from '@/components/ui/Switch/Switch';
import Card from '@/components/ui/Card/Card';
import PageHeader from '@/components/ui/PageHeader/PageHeader';
import Button from '@/components/ui/Button/Button';
import Input from '@/components/ui/Input/Input';
import Select from '@/components/ui/Select/Select';
import {
  defaultSettings,
  readSettings,
  saveSettings,
  validateSettings,
  clearProjectSettings,
  hasProjectSettings,
  type ChatSettings,
} from '@/lib/settings';
import styles from './Settings.module.css';

export default function SettingsForm({ projectId }: { projectId?: string }) {
  const [value, setValue] = useState<ChatSettings>(defaultSettings);
  const [baseline, setBaseline] = useState<ChatSettings>(defaultSettings);
  const [loaded, setLoaded] = useState(false);
  const [overridden, setOverridden] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [keyRevision, setKeyRevision] = useState(0);
  const [keyPending, setKeyPending] = useState(false);
  const [keyBusy, setKeyBusy] = useState(false);
  const keyChanged = useCallback(
    () => setKeyRevision((count) => count + 1),
    [],
  );
  useEffect(() => {
    let active = true;
    Promise.resolve().then(() => {
      if (!active) return;
      try {
        const preferences = readSettings(projectId);
        setValue(preferences);
        setBaseline(preferences);
        setOverridden(projectId ? hasProjectSettings(projectId) : false);
      } catch {
        setError(
          'Browser storage is unavailable. Settings cannot be saved in this browser.',
        );
      }
      setLoaded(true);
    });
    return () => {
      active = false;
    };
  }, [projectId]);
  const dirty = JSON.stringify(value) !== JSON.stringify(baseline);
  function update<K extends keyof ChatSettings>(
    field: K,
    next: ChatSettings[K],
  ) {
    setValue((current) => ({ ...current, [field]: next }));
    setNotice('');
    setError('');
  }
  function save(event: FormEvent) {
    event.preventDefault();
    if (keyPending || keyBusy) {
      setError('Save or cancel the API key entry before saving defaults.');
      return;
    }
    const invalid = validateSettings(value);
    if (invalid) {
      setError(invalid);
      return;
    }
    try {
      const clean = {
        ...value,
        model: value.model.trim(),
        baseUrl: value.baseUrl.trim(),
      };
      saveSettings(clean, projectId);
      setValue(clean);
      setBaseline(clean);
      setOverridden(!!projectId);
      setError('');
      setNotice(
        'API connection defaults saved for all projects. Chat behavior applies to new conversations.',
      );
    } catch {
      setError(
        'Could not save settings. Check browser storage permissions and try again.',
      );
    }
  }
  function inherit() {
    if (!projectId) return;
    try {
      clearProjectSettings(projectId);
      const inherited = readSettings();
      setValue(inherited);
      setBaseline(inherited);
      setOverridden(false);
      setError('');
      setNotice('This project now uses workspace defaults.');
    } catch {
      setError('Could not restore workspace defaults.');
    }
  }
  return (
    <form onSubmit={save} className={styles.form}>
      <PageHeader
        title="AI settings"
        metadata={
          <span className={styles.scope}>
            App-wide API ·{' '}
            {projectId
              ? 'Project chat preferences'
              : 'Workspace chat preferences'}
          </span>
        }
      />
      {error && (
        <p className={styles.error} role="alert">
          {error}
        </p>
      )}
      {notice && (
        <p className={styles.notice} role="status">
          {notice}
        </p>
      )}
      <fieldset disabled={!loaded} className={styles.fields}>
        <Card
          role="region"
          className={styles.section}
          aria-labelledby="ai-heading"
        >
          <div className={styles.sectionHeading}>
            <div>
              <h2 id="ai-heading">AI connection</h2>
              <p>
                Choose a provider, save its app key, then select a model. Save
                defaults below to use this connection for new chats.
              </p>
            </div>
          </div>
          <div className={styles.grid}>
            <div>
              <label htmlFor="provider">Provider</label>
              <Select
                id="provider"
                disabled={keyBusy || keyPending}
                value={value.provider}
                onValueChange={(nextValue) => {
                  setValue((current) => ({
                    ...current,
                    provider: nextValue as ChatSettings['provider'],
                    model: '',
                    baseUrl: '',
                    reasoningMode: 'off',
                  }));
                  setNotice('');
                }}
              >
                <option value="groq">Groq</option>
                <option value="openai">OpenAI</option>
                <option value="ollama">Ollama</option>
                <option value="compatible">OpenAI-compatible</option>
              </Select>
            </div>
            {['ollama', 'compatible'].includes(value.provider) && (
              <div>
                <label htmlFor="base-url">Provider base URL</label>
                <Input
                  id="base-url"
                  disabled={keyBusy || keyPending}
                  type="url"
                  required={value.provider === 'compatible'}
                  value={value.baseUrl}
                  onChange={(event) => {
                    setValue((current) => ({
                      ...current,
                      baseUrl: event.target.value,
                      reasoningMode: 'off',
                    }));
                    setNotice('');
                  }}
                  placeholder={
                    value.provider === 'ollama'
                      ? 'http://localhost:11434'
                      : 'https://provider.example/v1'
                  }
                />
              </div>
            )}
          </div>
          <ProviderKeySettings
            key={`${value.provider}:${value.baseUrl.trim()}`}
            provider={value.provider}
            baseUrl={value.baseUrl}
            onChanged={keyChanged}
            onPendingChange={setKeyPending}
            onBusyChange={setKeyBusy}
          />
          <p className={styles.caption}>
            API keys, connection defaults and model profiles are app-wide. Chat
            behavior and token limits can still be configured per project.
          </p>
          <div className={styles.modelSetup}>
            <div>
              <label htmlFor="model">Default model</label>
              <ModelPicker
                key={`${value.provider}:${value.baseUrl}`}
                id="model"
                provider={value.provider}
                baseUrl={value.baseUrl}
                projectId={projectId}
                value={value.model}
                onChange={(next) => {
                  setValue((current) => ({
                    ...current,
                    model: next,
                    reasoningMode:
                      next === current.model ? current.reasoningMode : 'off',
                  }));
                  setNotice('');
                }}
                disabled={!loaded}
                refreshToken={keyRevision}
              />
            </div>
            <div>
              <label htmlFor="reasoning">Reasoning level</label>
              <ReasoningSelector
                id="reasoning"
                provider={value.provider}
                model={value.model}
                baseUrl={value.baseUrl}
                value={value.reasoningMode}
                onChange={(next) => update('reasoningMode', next)}
                disabled={!loaded}
              />
            </div>
          </div>
          <div className={styles.profiles}>
            <ModelProfiles
              value={value}
              disabled={!loaded || keyBusy || keyPending}
              onApply={(profile) => {
                setValue((current) => ({ ...current, ...profile }));
                setNotice('');
              }}
            />
          </div>
        </Card>
        <Card
          role="region"
          className={styles.section}
          aria-labelledby="limits-heading"
        >
          <div className={styles.sectionHeading}>
            <div>
              <h2 id="limits-heading">
                Conversation budgets & response limits
              </h2>
              <p>
                Defaults for new chats
                {projectId ? ' in this project' : ' in this workspace'}.
                Existing chats show their saved budgets under Model &
                permissions.
              </p>
            </div>
          </div>
          <div className={styles.grid}>
            <div>
              <label htmlFor="total-token-limit">
                Cumulative token limit per chat
              </label>
              <Input
                id="total-token-limit"
                type="number"
                inputMode="numeric"
                min={1000}
                max={1000000}
                step={1}
                required
                value={value.totalTokenLimit}
                onChange={(event) =>
                  update('totalTokenLimit', Number(event.target.value))
                }
              />
              <p>
                Total provider-reported input + output across all turns. Applies
                to new chats; existing chats keep their saved budget. An
                in-flight response can exceed the remaining budget.
              </p>
            </div>
            <div>
              <label htmlFor="tool-call-limit">Tool calls per chat</label>
              <Input
                id="tool-call-limit"
                type="number"
                inputMode="numeric"
                min={1}
                max={1000}
                step={1}
                required
                value={value.toolCallLimit}
                onChange={(event) =>
                  update('toolCallLimit', Number(event.target.value))
                }
              />
              <p>
                Cumulative across all turns, including failed attempts. Applies
                to new chats; use Model & permissions to update an existing
                chat.
              </p>
            </div>
            <div>
              <label htmlFor="max-tokens">Maximum output tokens</label>
              <Input
                id="max-tokens"
                type="number"
                inputMode="numeric"
                min={256}
                max={65536}
                step={256}
                required
                value={value.maxTokens}
                onChange={(event) =>
                  update('maxTokens', Number(event.target.value))
                }
              />
              <p>The provider may impose a lower limit.</p>
            </div>
            <div>
              <label htmlFor="timeout">Request timeout (seconds)</label>
              <Input
                id="timeout"
                type="number"
                inputMode="numeric"
                min={30}
                max={600}
                step={1}
                required
                value={value.timeoutSeconds}
                onChange={(event) =>
                  update('timeoutSeconds', Number(event.target.value))
                }
              />
              <p>Maximum wait for each model request.</p>
            </div>
          </div>
        </Card>
        <Card
          role="region"
          className={styles.section}
          aria-labelledby="permissions-heading"
        >
          <div className={styles.sectionHeading}>
            <div>
              <h2 id="permissions-heading">Assistant permissions</h2>
            </div>
          </div>
          <label className={styles.toggle}>
            <div>
              <strong>Allow draft edits</strong>
              <p>Prepare unpublished changes. Off makes chats read-only.</p>
            </div>
            <Switch
              aria-label="Allow draft edits"
              checked={value.allowEdits}
              onCheckedChange={(next) => update('allowEdits', next)}
            />
          </label>
          <label className={styles.toggle}>
            <div>
              <strong>Allow validation & tests</strong>
              <p>
                Allow project Python code to run against disposable data on the
                runtime machine.
              </p>
            </div>
            <Switch
              aria-label="Allow validation and tests"
              checked={value.allowExecution}
              onCheckedChange={(next) => update('allowExecution', next)}
            />
          </label>
        </Card>
        <Card
          role="region"
          className={styles.section}
          aria-labelledby="chat-heading"
        >
          <div className={styles.sectionHeading}>
            <div>
              <h2 id="chat-heading">Chat experience</h2>
            </div>
          </div>
          <label className={styles.toggle}>
            <div>
              <strong>Enter to send</strong>
              <p>Shift + Enter for a new line. Off: ⌘ / Ctrl + Enter sends.</p>
            </div>
            <Switch
              aria-label="Enter to send"
              checked={value.sendWithEnter}
              onCheckedChange={(next) => update('sendWithEnter', next)}
            />
          </label>
        </Card>
      </fieldset>
      <footer className={styles.footer}>
        <div>
          <span role="status">
            {keyBusy
              ? 'Saving API key…'
              : keyPending
                ? 'API key not saved — save or cancel above'
                : dirty
                  ? 'Unsaved defaults'
                  : 'Defaults are saved'}
          </span>
          {projectId && overridden ? (
            <Button
              type="button"
              variant="ghost"
              disabled={keyBusy || keyPending}
              onClick={inherit}
            >
              Use workspace defaults
            </Button>
          ) : null}
        </div>
        <div className={styles.actions}>
          <Button
            variant="secondary"
            disabled={!dirty || keyBusy || keyPending}
            type="button"
            onClick={() => {
              setValue(baseline);
              setNotice('');
              setError('');
            }}
          >
            Discard
          </Button>
          <Button
            type="submit"
            disabled={!loaded || !dirty || keyBusy || keyPending}
          >
            Save defaults
          </Button>
        </div>
      </footer>
    </form>
  );
}
