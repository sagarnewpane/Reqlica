import { useEffect, useRef, useState } from 'react';
import {
  ArrowUp,
  Plus,
  RotateCw,
  SlidersHorizontal,
  X,
  CheckCircle2,
  CircleAlert,
  ChevronRight,
} from 'lucide-react';
import ModelPicker from '@/components/ui/ModelPicker/ModelPicker';
import ReasoningSelector from '@/components/settings/ReasoningSelector';
import Link from 'next/link';
import { useRouter } from 'next/router';
import ChatDataPreview from './ChatDataPreview';
import Markdown from 'react-markdown';
import Button from '@/components/ui/Button/Button';
import Input from '@/components/ui/Input/Input';
import Select from '@/components/ui/Select/Select';
import { conversationMessages, workspaceRequest } from '@/lib/chat';
import { draftLabel, orderedDrafts, type Draft } from '@/lib/drafts';
import { useConversation } from './use-conversation';
import ChatContext from './ChatContext';
import ConversationBudgets from './ConversationBudgets';
import SpecificationAttachment, {
  type Attachment,
} from './SpecificationAttachment';
import GenerationStatus from './GenerationStatus';
import styles from './ProjectChat.module.css';
import {
  readModelProfiles,
  subscribeModelProfiles,
  type ModelProfile,
} from '@/lib/model-profiles';
import {
  readSettings,
  defaultSettings,
  type ReasoningMode,
} from '@/lib/settings';

const toolLabels: Record<string, string> = {
  get_project_context: 'Read project context',
  create_draft: 'Create draft',
  write_draft_file: 'Save file',
  upsert_endpoint: 'Register endpoint',
  remove_endpoint: 'Remove endpoint',
  validate_draft: 'Validate API',
  test_draft: 'Run tests',
  call_draft_endpoint: 'Try endpoint',
  discover_tools: 'Load tools',
  finish_task: 'Finish changes',
  get_draft_changes: 'Read draft changes',
};

export default function ProjectChat({
  projectId,
  projectName,
}: {
  projectId: string;
  projectName: string;
}) {
  const router = useRouter();
  const prefilled = useRef('');
  const [attachment, setAttachment] = useState<Attachment>({ ready: true });
  const [provider, setProvider] = useState('groq');
  const [model, setModel] = useState('');
  const [baseUrl, setBaseUrl] = useState('');
  const [reasoningMode, setReasoningMode] = useState<ReasoningMode>('off');
  const [edits, setEdits] = useState(true);
  const [execution, setExecution] = useState(false);
  const [preferences, setPreferences] = useState(defaultSettings);
  const [profiles, setProfiles] = useState<ModelProfile[]>([]);
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [selectedDraftId, setSelectedDraftId] = useState('');
  const textarea = useRef<HTMLTextAreaElement>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const {
    runs,
    run,
    setRun,
    startNew,
    loading,
    busy,
    hasPending,
    pausing,
    error,
    setError,
    message,
    setMessage,
    outgoing,
    activity,
    followLatest: followLatestRef,
    send,
    resume,
    reload,
    pause,
    resetReasoning,
    changeReasoning,
    changeLimits,
  } = useConversation({
    projectId,
    provider,
    model,
    baseUrl,
    reasoningMode,
    edits,
    execution,
    preferences,
    attachment,
    draft: drafts.find((item) => item.id === selectedDraftId) || null,
  });

  useEffect(() => {
    let active = true;
    Promise.resolve().then(() => {
      if (!active) return;
      try {
        const defaults = readSettings(projectId);
        setProfiles(readModelProfiles());
        setPreferences(defaults);
        setProvider(defaults.provider);
        setModel(defaults.model);
        setBaseUrl(defaults.baseUrl);
        setReasoningMode(defaults.reasoningMode);
        setEdits(defaults.allowEdits);
        setExecution(defaults.allowExecution);
      } catch {
        /* Chat remains available when browser storage is blocked. */
      }
    });
    return () => {
      active = false;
    };
  }, [projectId]);

  useEffect(
    () =>
      subscribeModelProfiles(() => {
        try {
          setProfiles(readModelProfiles());
        } catch {
          /* Keep the currently loaded profile list. */
        }
      }),
    [],
  );

  useEffect(() => {
    if (
      !router.isReady ||
      loading ||
      busy ||
      hasPending ||
      prefilled.current === router.asPath
    )
      return;
    const draftId = router.query.draftId;
    const prompt = router.query.prompt;
    if (
      draftId !== undefined &&
      (typeof draftId !== 'string' || !/^[a-f0-9]{32}$/.test(draftId))
    )
      return;
    if (draftId === undefined && typeof prompt !== 'string') return;
    let active = true;
    Promise.resolve().then(() => {
      if (!active) return;
      prefilled.current = router.asPath;
      setRun(null);
      setSelectedDraftId(typeof draftId === 'string' ? draftId : '');
      if (typeof prompt === 'string') setMessage(prompt.slice(0, 32768));
    });
    return () => {
      active = false;
    };
  }, [
    router.isReady,
    router.asPath,
    router.query.draftId,
    router.query.prompt,
    loading,
    busy,
    hasPending,
    setRun,
    setMessage,
  ]);

  useEffect(() => {
    let active = true;
    workspaceRequest<{ drafts: Draft[] }>(projectId, 'drafts')
      .then((data) => {
        if (active) setDrafts(data.drafts);
      })
      .catch(() => {
        /* New chats can still start from the published API. */
      });
    return () => {
      active = false;
    };
  }, [projectId]);

  useEffect(() => {
    const transcript = bottom.current?.parentElement;
    if (transcript && followLatestRef.current)
      transcript.scrollTop = run ? transcript.scrollHeight : 0;
  }, [run, outgoing, busy, followLatestRef]);

  useEffect(() => {
    const input = textarea.current;
    if (input) {
      input.style.height = 'auto';
      input.style.height = `${Math.min(input.scrollHeight, 180)}px`;
    }
  }, [message]);

  const messages = run ? conversationMessages(run) : [];
  const status = busy
    ? pausing
      ? 'Pausing after this step…'
      : 'Working…'
    : run?.status === 'ready'
      ? 'Paused'
      : run?.status === 'completed'
        ? 'Ready'
        : run?.status || 'New conversation';
  const frozen = busy || loading;

  return (
    <section className={styles.chat} aria-label="Project chat">
      <div className={styles.chatToolbar}>
        <div className={styles.conversationField}>
          <label htmlFor="conversation">Conversation</label>
          <Select
            id="conversation"
            disabled={frozen || hasPending}
            value={run?.id || ''}
            onValueChange={(nextValue) => {
              if (!nextValue) {
                setAttachment({ ready: true });
                setSelectedDraftId('');
                startNew();
                return;
              }
              setRun(runs.find((item) => item.id === nextValue) || null);
              setMessage('');
              setError('');
            }}
          >
            <option value="">New conversation</option>
            {runs.map((item) => (
              <option key={item.id} value={item.id}>
                {item.message.slice(0, 70)}
              </option>
            ))}
          </Select>
        </div>
        <div className={styles.chatSpacer} aria-hidden="true" />
        {!settingsOpen && !loading && (run || model) && (
          <ChatContext
            key={run?.id || 'new'}
            projectId={projectId}
            run={run}
            model={model}
            provider={provider}
          />
        )}
        <button
          className={styles.secondary}
          aria-label="New chat"
          title="New chat"
          disabled={frozen}
          onClick={() => {
            setAttachment({ ready: true });
            setSelectedDraftId('');
            startNew();
            textarea.current?.focus();
          }}
        >
          <Plus size={18} aria-hidden="true" />
        </button>
        <button
          className={styles.secondary}
          aria-label="Refresh conversation"
          title="Refresh conversation"
          disabled={frozen}
          onClick={() => void reload()}
        >
          <RotateCw size={16} aria-hidden="true" />
        </button>
        <button
          className={styles.secondary}
          aria-label="Model and permissions"
          title="Model and permissions"
          aria-expanded={settingsOpen}
          aria-controls="chat-settings"
          onClick={() => setSettingsOpen((open) => !open)}
        >
          <SlidersHorizontal size={16} aria-hidden="true" />
        </button>
      </div>
      <div
        id="chat-settings"
        className={styles.settings}
        hidden={!settingsOpen}
        role="region"
        aria-labelledby="chat-settings-title"
      >
        <div className={styles.settingsHeader}>
          <div>
            <h2 id="chat-settings-title">Model & permissions</h2>
            {run && (
              <p>
                Model and permissions are fixed. Reasoning and conversation
                budgets can change while idle.
              </p>
            )}
          </div>
          <button
            type="button"
            className={styles.secondary}
            aria-label="Close model and permissions"
            onClick={() => {
              setSettingsOpen(false);
              document
                .querySelector<HTMLButtonElement>(
                  '[aria-controls="chat-settings"]',
                )
                ?.focus();
            }}
          >
            <X size={18} aria-hidden="true" />
          </button>
        </div>
        <div className={styles.settingsGrid}>
          {!run && (
            <div className={styles.profileField}>
              <label htmlFor="chat-draft">Starting point</label>
              <Select
                id="chat-draft"
                value={selectedDraftId}
                disabled={frozen || hasPending}
                onValueChange={setSelectedDraftId}
              >
                <option value="">
                  Published API · create a new draft if needed
                </option>
                {orderedDrafts(drafts)
                  .filter((item) => !item.error)
                  .map((item) => (
                    <option key={item.id} value={item.id}>
                      {draftLabel(item)}
                    </option>
                  ))}
              </Select>
            </div>
          )}
          {!run && (
            <div className={styles.profileField}>
              <label htmlFor="chat-profile">Model profile</label>
              <Select
                id="chat-profile"
                disabled={frozen || hasPending}
                value={
                  profiles.find(
                    (profile) =>
                      profile.provider === provider &&
                      profile.model === model &&
                      profile.baseUrl === baseUrl &&
                      profile.reasoningMode === reasoningMode,
                  )?.id || ''
                }
                onFocus={() => {
                  try {
                    setProfiles(readModelProfiles());
                  } catch {
                    /* Keep available profiles. */
                  }
                }}
                onValueChange={(nextValue) => {
                  const profile = profiles.find(
                    (item) => item.id === nextValue,
                  );
                  if (!profile) return;
                  setProvider(profile.provider);
                  setModel(profile.model);
                  setBaseUrl(profile.baseUrl);
                  setReasoningMode(profile.reasoningMode);
                }}
              >
                <option value="">Custom settings</option>
                {profiles.map((profile) => (
                  <option key={profile.id} value={profile.id}>
                    {profile.name} · {profile.model}
                  </option>
                ))}
              </Select>
            </div>
          )}
          <div>
            <label htmlFor="chat-provider">Provider</label>
            <Select
              id="chat-provider"
              value={run?.provider.provider || provider}
              disabled={frozen || !!run || hasPending}
              onValueChange={(nextValue) => {
                setProvider(nextValue);
                setBaseUrl('');
                setModel('');
                setReasoningMode('default');
              }}
            >
              <option value="groq">Groq</option>
              <option value="openai">OpenAI</option>
              <option value="ollama">Ollama</option>
              <option value="compatible">OpenAI-compatible</option>
            </Select>
          </div>
          {['ollama', 'compatible'].includes(
            run?.provider.provider || provider,
          ) && (
            <div className={styles.wideField}>
              <label htmlFor="chat-url">Provider URL</label>
              <Input
                id="chat-url"
                value={run?.provider.base_url || baseUrl}
                disabled={frozen || !!run || hasPending}
                placeholder={
                  provider === 'ollama'
                    ? 'http://localhost:11434'
                    : 'https://provider.example/v1'
                }
                onChange={(event) => {
                  setBaseUrl(event.target.value);
                  setReasoningMode('default');
                }}
              />
            </div>
          )}
          <div>
            <label htmlFor="chat-model">Model</label>
            <ModelPicker
              key={`${run?.provider.provider || provider}:${run?.provider.base_url || baseUrl}`}
              id="chat-model"
              provider={run?.provider.provider || provider}
              baseUrl={run?.provider.base_url || baseUrl}
              projectId={projectId}
              value={run?.provider.model || model}
              disabled={frozen || !!run || hasPending}
              onChange={(next) => {
                setModel(next);
                setReasoningMode('default');
              }}
            />
          </div>
          <div>
            <label htmlFor="chat-reasoning">Reasoning level</label>
            <ReasoningSelector
              id="chat-reasoning"
              provider={run?.provider.provider || provider}
              model={run?.provider.model || model}
              baseUrl={run?.provider.base_url || baseUrl}
              value={run?.provider.reasoning_mode || reasoningMode}
              onChange={(next) => {
                if (run) void changeReasoning(next);
                else setReasoningMode(next);
              }}
              disabled={
                frozen || (!!run && !run.can_change_reasoning) || hasPending
              }
            />
            {run?.can_reset_reasoning && (
              <button
                type="button"
                className={styles.secondary}
                disabled={frozen}
                onClick={() => void resetReasoning()}
              >
                Use provider default for this conversation
              </button>
            )}
          </div>
          <Link href={{ pathname: '/settings', query: { projectId } }}>
            Manage response limits and API keys in Settings →
          </Link>
        </div>
        <ConversationBudgets
          key={
            run
              ? `${run.id}:${run.limits?.total_tokens}:${run.limits?.tool_calls}:${run.limits?.model_calls}:${run.limits?.model_calls_per_turn}`
              : 'new'
          }
          run={run}
          preferences={preferences}
          disabled={frozen || hasPending}
          onChange={(budgets) =>
            setPreferences((current) => ({ ...current, ...budgets }))
          }
          onApply={changeLimits}
        />
        <fieldset className={styles.permissions}>
          <legend>Permissions</legend>
          <label>
            <input
              type="checkbox"
              checked={run?.allow_edits ?? edits}
              disabled={frozen || !!run || hasPending}
              onChange={(event) => setEdits(event.target.checked)}
            />
            <span>
              <strong>Draft edits</strong>
              <small>Let Reqlica make changes in unpublished drafts.</small>
            </span>
          </label>
          <label>
            <input
              type="checkbox"
              checked={run?.allow_execution ?? execution}
              disabled={frozen || !!run || hasPending}
              onChange={(event) => setExecution(event.target.checked)}
            />
            <span>
              <strong>Validation & tests</strong>
              <small>Run project code on your runtime machine.</small>
            </span>
          </label>
        </fieldset>
        <p className={styles.help}>
          Your model provider receives messages and relevant project context.
        </p>
      </div>
      <div
        className={styles.transcript}
        role="log"
        aria-label="Conversation"
        hidden={settingsOpen}
        onScroll={(event) => {
          const panel = event.currentTarget;
          followLatestRef.current =
            panel.scrollHeight - panel.scrollTop - panel.clientHeight < 100;
        }}
      >
        {loading && (
          <p role="status" className={styles.help}>
            Loading conversations…
          </p>
        )}
        {!loading && !run && (
          <div className={styles.welcome}>
            <span className={styles.eyebrow}>YOUR PROJECT ASSISTANT</span>
            <h2>What’s on your mind?</h2>
            <p>
              Explore {projectName}, describe a change, or work through a
              problem.
            </p>
            <div className={styles.suggestions}>
              {[
                'Walk me through this API',
                'Let’s build an endpoint',
                'Help me find edge cases',
              ].map((text) => (
                <button
                  key={text}
                  onClick={() => {
                    setMessage(text);
                    textarea.current?.focus();
                  }}
                >
                  {text}
                  <span aria-hidden="true">↗</span>
                </button>
              ))}
            </div>
          </div>
        )}
        {messages.map((item, index) =>
          item.kind === 'tool' ? (
            <details
              key={`${run?.id}-${index}`}
              className={styles.toolEvent}
              data-status={item.status}
              aria-label={`${item.name?.replaceAll('_', ' ')}: ${item.status === 'done' ? 'Completed' : 'Needs attention'}`}
            >
              <summary>
                {item.status === 'done' ? (
                  <CheckCircle2 size={15} aria-hidden="true" />
                ) : (
                  <CircleAlert size={15} aria-hidden="true" />
                )}
                <span className={styles.toolLabel}>
                  {toolLabels[item.name || ''] ||
                    item.name?.replaceAll('_', ' ')}
                </span>
                {(item.path || item.route) && (
                  <span className={styles.toolTarget}>
                    {item.path || item.route}
                  </span>
                )}
                <span className={styles.toolStatus}>
                  {item.status === 'done' ? 'Completed' : 'Needs attention'}
                </span>
                <ChevronRight
                  size={14}
                  className={styles.toolChevron}
                  aria-hidden="true"
                />
              </summary>
              <div className={styles.toolDetail}>
                <code>{item.name}</code>
                {item.detail && <p>{item.detail}</p>}
              </div>
            </details>
          ) : (
            <article
              key={`${run?.id}-${index}`}
              className={
                item.kind === 'user'
                  ? styles.userMessage
                  : styles.assistantMessage
              }
            >
              <h3>
                {item.kind === 'assistant' && (
                  <span className={styles.assistantAvatar} aria-hidden="true">
                    R
                  </span>
                )}
                {item.kind === 'user' ? 'You' : 'Reqlica'}
              </h3>
              <div
                className={
                  item.kind === 'user' ? styles.messageText : styles.markdown
                }
              >
                {item.kind === 'user' ? (
                  item.text
                ) : (
                  <Markdown
                    skipHtml
                    components={{
                      img: ({ alt }) => <span>{alt || '[Image]'}</span>,
                      a: ({ href, children }) => (
                        <a
                          href={href}
                          target="_blank"
                          rel="noopener noreferrer"
                        >
                          {children}
                        </a>
                      ),
                    }}
                  >
                    {item.text || ''}
                  </Markdown>
                )}
              </div>
            </article>
          ),
        )}
        {outgoing && (
          <article className={styles.userMessage}>
            <h3>You</h3>
            <div className={styles.messageText}>{outgoing}</div>
            <span className={styles.sending}>Sending…</span>
          </article>
        )}
        {busy && !settingsOpen && (
          <div className={styles.activeStep}>
            <GenerationStatus
              key={activity?.startedAt || 'pending'}
              activity={activity}
              pausing={pausing}
            />
          </div>
        )}
        {!busy && run?.saved_work && !run.answer && (
          <article className={styles.assistantMessage} aria-label="Saved work">
            <h3>Saved work</h3>
            <div className={styles.markdown}>
              <Markdown skipHtml>{run.saved_work}</Markdown>
            </div>
          </article>
        )}
        {run?.draft_id && (
          <p className={styles.draft}>
            <span>Unpublished draft</span>
            <Link
              href={{
                pathname: '/draft-review',
                query: { projectId, draftId: run.draft_id },
              }}
            >
              Review draft →
            </Link>
          </p>
        )}
        {run?.notice && <p className={styles.help}>{run.notice}</p>}
        {run?.error && (
          <p className={styles.error} role="alert">
            {run.error.message || 'The conversation could not finish.'}
          </p>
        )}
        {error && (
          <div className={styles.error} role="alert">
            {error}
            <button onClick={() => void reload()} disabled={frozen}>
              Refresh saved state
            </button>
          </div>
        )}
        <div ref={bottom} />
      </div>
      <form className={styles.composer} onSubmit={send} hidden={settingsOpen}>
        {run && !run.can_reply && !run.can_resume && !busy && (
          <div className={styles.help}>
            {run.status === 'running'
              ? 'A step is still running or its response is unconfirmed. Refresh before continuing.'
              : run.status === 'limit' && run.can_change_limits
                ? 'A conversation limit was reached. Review usage and adjust budgets, or start a new chat from the saved draft.'
                : 'This conversation cannot continue. Start a new chat to keep working.'}
            {run.can_change_limits && (
              <Button
                type="button"
                variant="secondary"
                onClick={() => setSettingsOpen(true)}
              >
                Adjust conversation budgets
              </Button>
            )}
            {run.draft_id && (
              <Button
                type="button"
                variant="secondary"
                onClick={() => {
                  void workspaceRequest<Draft>(
                    projectId,
                    `drafts/${run.draft_id}`,
                  )
                    .then((saved) => {
                      setDrafts((current) => [
                        saved,
                        ...current.filter((item) => item.id !== saved.id),
                      ]);
                      setSelectedDraftId(saved.id);
                      setAttachment({ ready: true });
                      startNew();
                      textarea.current?.focus();
                    })
                    .catch((err) => setError((err as Error).message));
                }}
              >
                New chat from saved draft
              </Button>
            )}
          </div>
        )}
        {run?.status === 'running' && !busy && (
          <p className={styles.help}>
            This step may still be finishing. Check and continue refreshes its
            saved state. If the model request was interrupted, retrying may use
            more tokens.
          </p>
        )}
        {!run && (
          <SpecificationAttachment
            key={projectId}
            projectId={projectId}
            disabled={loading || busy || hasPending || !edits}
            onChange={setAttachment}
          />
        )}
        {run?.specification && (
          <p className={styles.help}>
            Attached: {run.specification.filename} · {run.specification.kind}
          </p>
        )}
        {run?.data_preview && (
          <ChatDataPreview projectId={projectId} preview={run.data_preview} />
        )}
        <label htmlFor="chat-message" className={styles.composerLabel}>
          Message Reqlica
        </label>
        <textarea
          ref={textarea}
          id="chat-message"
          rows={1}
          maxLength={32768}
          value={message}
          disabled={loading || hasPending || (!!run && !run.can_reply && !busy)}
          hidden={!!run && !run.can_reply && !busy}
          placeholder="Message Reqlica…"
          onChange={(event) => setMessage(event.target.value)}
          onKeyDown={(event) => {
            if (
              event.key === 'Enter' &&
              !event.shiftKey &&
              (preferences.sendWithEnter || event.metaKey || event.ctrlKey) &&
              !event.nativeEvent.isComposing
            ) {
              event.preventDefault();
              void send();
            }
          }}
        />
        <div className={styles.composerFooter}>
          <span role="status">
            {busy || (run && !run.can_reply)
              ? run?.can_resume && !busy
                ? 'This turn is unfinished'
                : status
              : preferences.sendWithEnter
                ? 'Shift + Enter for a new line'
                : '⌘ / Ctrl + Enter to send'}
          </span>
          {!run && !model.trim() && !busy && !hasPending ? (
            <Button
              type="button"
              onClick={() => {
                setSettingsOpen(true);
                requestAnimationFrame(() =>
                  document.getElementById('chat-model')?.focus(),
                );
              }}
            >
              Choose model
            </Button>
          ) : busy ? (
            <Button type="button" disabled={pausing} onClick={pause}>
              Pause
            </Button>
          ) : hasPending ? (
            <Button
              type="button"
              disabled={loading}
              onClick={() => void send()}
            >
              Retry sending
            </Button>
          ) : run?.can_resume ? (
            <Button
              type="button"
              disabled={loading}
              onClick={() => void resume()}
            >
              {run.status === 'running' ? 'Check and continue' : 'Continue'}
            </Button>
          ) : (
            <Button
              type="submit"
              disabled={
                loading ||
                hasPending ||
                !message.trim() ||
                (!run &&
                  (!attachment.ready ||
                    (!!attachment.specification && !edits))) ||
                (!run &&
                  (!model.trim() ||
                    (provider === 'compatible' && !baseUrl.trim()))) ||
                (!!run && !run.can_reply)
              }
              aria-label="Send message"
            >
              <ArrowUp size={18} aria-hidden="true" />
            </Button>
          )}
        </div>
      </form>
    </section>
  );
}
