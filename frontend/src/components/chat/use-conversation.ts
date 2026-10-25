import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
} from 'react';
import {
  workspaceRequest,
  WorkspaceError,
  type AgentRun,
  type ActivityEvent,
  type GenerationActivity,
} from '@/lib/chat';
import type { Attachment } from './SpecificationAttachment';
import type { ChatSettings, ReasoningMode } from '@/lib/settings';
import { validateSettings } from '@/lib/settings';
import { newRequestId } from '@/lib/ids';
import {
  readConversationSession,
  saveConversationSession,
  type PendingSubmission,
  type ConversationSession,
} from '@/lib/conversation-session';

type ConversationOptions = {
  attachment?: Attachment;
  projectId: string;
  provider: string;
  model: string;
  baseUrl: string;
  reasoningMode: ReasoningMode;
  edits: boolean;
  execution: boolean;
  preferences: ChatSettings;
  draft: { id: string; revision: string } | null;
};

const MAX_STEPS_PER_CONTINUATION = 90;

/** Saved conversation lifecycle, including safe recovery of unconfirmed writes. */
export function useConversation({
  projectId,
  attachment,
  provider,
  model,
  baseUrl,
  reasoningMode,
  edits,
  execution,
  preferences,
  draft,
}: ConversationOptions) {
  const [runs, setRuns] = useState<AgentRun[]>([]);
  const [run, updateRun] = useState<AgentRun | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [hasPending, setHasPending] = useState(false);
  const [pausing, setPausing] = useState(false);
  const [error, setError] = useState('');
  const [message, updateMessage] = useState('');
  const [outgoing, setOutgoing] = useState('');
  const [activity, setActivity] = useState<GenerationActivity | null>(null);
  const alive = useRef(true);
  const epoch = useRef(0);
  const restored = useRef(false);
  const selectionSequence = useRef(0);
  const hasSavedSelection = useRef(false);
  const journal = useRef<ConversationSession>({
    selectedId: null,
    message: '',
    pending: null,
  });
  const stop = useRef(false);
  const locked = useRef(false);
  const followLatest = useRef(true);
  // Retain mutation identities across lost acknowledgements; retry never duplicates a turn.
  const pending = useRef<PendingSubmission | null>(null);
  const persist = useCallback(() => {
    journal.current.pending = pending.current;
    saveConversationSession(projectId, journal.current);
  }, [projectId]);
  const setMessage = useCallback(
    (value: string) => {
      journal.current.message = value;
      persist();
      updateMessage(value);
    },
    [persist],
  );
  const setRun = useCallback(
    (value: AgentRun | null) => {
      selectionSequence.current++;
      hasSavedSelection.current = true;
      journal.current.selectedId = value?.id || null;
      persist();
      updateRun(value);
    },
    [persist],
  );
  const currentRunId = run?.id;
  const currentStatus = run?.status;

  const accept = useCallback(
    (value: AgentRun) => {
      if (!alive.current) return;
      setRun(value);
      setRuns((current) => [
        value,
        ...current.filter((item) => item.id !== value.id),
      ]);
    },
    [setRun],
  );

  useEffect(() => {
    alive.current = true;
    stop.current = false;
    locked.current = false;
    const generation = ++epoch.current;
    const selection = selectionSequence.current;
    let active = true;
    if (!restored.current) {
      const saved = readConversationSession(projectId);
      if (saved) {
        hasSavedSelection.current = true;
        journal.current = saved;
        pending.current = saved.pending;
      }
      restored.current = true;
    }
    Promise.resolve().then(() => {
      if (!active) return;
      setBusy(false);
      setPausing(false);
      setOutgoing('');
      updateMessage(journal.current.message);
      setHasPending(!!pending.current);
    });
    workspaceRequest<{ runs: AgentRun[] }>(projectId, 'agent-runs')
      .then(async (data) => {
        const selectedId =
          pending.current?.runId ||
          journal.current.selectedId ||
          (!hasSavedSelection.current ? data.runs[0]?.id : null);
        let selected = data.runs.find((item) => item.id === selectedId) || null;
        if (selectedId && !selected) {
          selected = await workspaceRequest<AgentRun>(
            projectId,
            `agent-runs/${selectedId}`,
          );
        }
        if (
          active &&
          generation === epoch.current &&
          selection === selectionSequence.current &&
          !locked.current
        ) {
          setRuns(
            selected
              ? [
                  selected,
                  ...data.runs.filter((item) => item.id !== selected.id),
                ]
              : data.runs,
          );
          setRun(selected);
        }
      })
      .catch((err) => {
        if (active) setError(err.message);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
      alive.current = false;
      epoch.current = generation + 1;
      stop.current = true;
    };
  }, [projectId, setRun]);

  useEffect(() => {
    if (!currentRunId || currentStatus !== 'running' || busy) return;
    let active = true;
    let checking = false;
    const timer = setInterval(async () => {
      if (checking || !active) return;
      checking = true;
      try {
        const latest = await workspaceRequest<AgentRun>(
          projectId,
          `agent-runs/${currentRunId}`,
        );
        if (
          active &&
          !locked.current &&
          journal.current.selectedId === latest.id
        )
          accept(latest);
      } catch {
        // The saved step can be inspected again on the next poll or manually.
      } finally {
        checking = false;
      }
    }, 2000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [projectId, currentRunId, currentStatus, busy, accept]);

  async function drive(current: AgentRun, generation: number) {
    const currentLifecycle = () =>
      alive.current && generation === epoch.current;
    if (!currentLifecycle()) return;
    stop.current = false;
    setPausing(false);
    for (let step = 0; step < MAX_STEPS_PER_CONTINUATION; step++) {
      if (!currentLifecycle()) return;
      accept(current);
      if (
        !alive.current ||
        stop.current ||
        !current.can_resume ||
        (current.status === 'failed' && step > 0)
      )
        return;
      // Never replay an ambiguous in-flight step automatically.
      if (current.status === 'running') return;
      const activityId = newRequestId();
      const nextAction = current.next_action;
      setActivity((previous) => ({
        phase: nextAction?.kind || 'step',
        tool: nextAction?.name,
        startedAt: previous?.startedAt || Date.now(),
        events: [],
      }));
      let active = true;
      let timer: ReturnType<typeof setTimeout>;
      const controller = new AbortController();
      async function poll() {
        if (!active || !currentLifecycle()) return;
        try {
          const data = await workspaceRequest<{ events: ActivityEvent[] }>(
            projectId,
            `activity/${activityId}`,
            'GET',
            undefined,
            undefined,
            { signal: controller.signal },
          );
          if (active && currentLifecycle())
            setActivity((previous) =>
              previous ? { ...previous, events: data.events } : previous,
            );
        } catch {
          /* Progress is optional; never retry a model step to recover it. */
        }
        if (active && currentLifecycle())
          timer = setTimeout(() => void poll(), 1500);
      }
      timer = setTimeout(() => void poll(), 700);
      try {
        current = await workspaceRequest<AgentRun>(
          projectId,
          `agent-runs/${current.id}/step`,
          'POST',
          {},
          undefined,
          { activityId },
        );
      } finally {
        active = false;
        controller.abort();
        clearTimeout(timer);
      }
    }
    if (currentLifecycle()) accept(current);
  }

  async function recover(id: string | undefined, generation: number) {
    if (!id || !alive.current || generation !== epoch.current) return;
    try {
      const latest = await workspaceRequest<AgentRun>(
        projectId,
        `agent-runs/${id}`,
      );
      if (alive.current && generation === epoch.current) accept(latest);
    } catch {
      /* Keep the original error. */
    }
  }

  async function send(event?: FormEvent) {
    const generation = epoch.current;
    const currentLifecycle = () =>
      alive.current && generation === epoch.current;
    event?.preventDefault();
    if (
      locked.current ||
      loading ||
      (!pending.current &&
        (!message.trim() ||
          (!run &&
            attachment &&
            (!attachment.ready || (attachment.specification && !edits))) ||
          (!run && !model.trim()) ||
          (run && !run.can_reply)))
    )
      return;
    if (!run && !pending.current) {
      const invalid = validateSettings(preferences);
      if (invalid) {
        setError(invalid);
        return;
      }
    }
    locked.current = true;
    setBusy(true);
    setActivity({ phase: 'saving', startedAt: Date.now(), events: [] });
    followLatest.current = true;
    setOutgoing(message.trim());
    setError('');
    let id = run?.id;
    try {
      if (!pending.current) {
        id = run?.id || newRequestId();
        const selectedDraft =
          !run && draft
            ? await workspaceRequest<{ id: string; revision: string }>(
                projectId,
                `drafts/${draft.id}`,
              )
            : null;
        if (!currentLifecycle()) return;
        pending.current = run
          ? {
              path: `agent-runs/${id}/replies`,
              method: 'POST',
              runId: id,
              body: {
                message: message.trim(),
                reply_id: newRequestId(),
                expected_turn: run.turn,
                expected_revision: run.revision,
              },
            }
          : {
              path: `agent-runs/${id}`,
              method: 'PUT',
              runId: id,
              body: {
                message: message.trim(),
                ...(attachment?.specification
                  ? { specification: attachment.specification }
                  : {}),
                provider,
                model: model.trim(),
                ...(baseUrl.trim() ? { base_url: baseUrl.trim() } : {}),
                allow_edits: edits,
                allow_execution: execution,
                max_tokens: preferences.maxTokens,
                total_token_limit: preferences.totalTokenLimit,
                tool_call_limit: preferences.toolCallLimit,
                model_call_limit: preferences.modelCallLimit,
                per_turn_model_call_limit: preferences.perTurnModelCallLimit,
                timeout_seconds: preferences.timeoutSeconds,
                reasoning_mode: reasoningMode,
                ...(selectedDraft
                  ? {
                      draft_id: selectedDraft.id,
                      expected_revision: selectedDraft.revision,
                    }
                  : {}),
              },
            };
      }
      persist();
      if (!currentLifecycle()) return;
      setHasPending(true);
      const submission = pending.current;
      id = submission.runId;
      const current = await workspaceRequest<AgentRun>(
        projectId,
        submission.path,
        submission.method,
        submission.body,
      );
      if (!currentLifecycle()) return;
      pending.current = null;
      persist();
      if (currentLifecycle()) setHasPending(false);
      if (!currentLifecycle()) return;
      accept(current);
      if (
        journal.current.message.trim() ===
        (submission.body as { message: string }).message
      )
        setMessage('');
      setOutgoing('');
      await drive(current, generation);
    } catch (err) {
      if (
        err instanceof WorkspaceError &&
        err.status >= 400 &&
        err.status < 500 &&
        ![408, 409, 429].includes(err.status)
      ) {
        if (!currentLifecycle()) return;
        pending.current = null;
        persist();
        if (currentLifecycle()) setHasPending(false);
      }
      await recover(id, generation);
      if (currentLifecycle()) setError((err as Error).message);
    } finally {
      if (currentLifecycle()) locked.current = false;
      if (currentLifecycle()) {
        setBusy(false);
        setPausing(false);
        setOutgoing('');
      }
    }
  }

  async function resume() {
    const generation = epoch.current;
    const currentLifecycle = () =>
      alive.current && generation === epoch.current;
    if (!run || locked.current) return;
    locked.current = true;
    setBusy(true);
    setActivity({ phase: 'refreshing', startedAt: Date.now(), events: [] });
    setError('');
    try {
      let latest = await workspaceRequest<AgentRun>(
        projectId,
        `agent-runs/${run.id}`,
      );
      if (!currentLifecycle()) return;
      if (latest.status === 'running') {
        latest = await workspaceRequest<AgentRun>(
          projectId,
          `agent-runs/${run.id}/recover`,
          'POST',
          {},
        );
      }
      await drive(latest, generation);
    } catch (err) {
      await recover(run.id, generation);
      if (currentLifecycle()) setError((err as Error).message);
    } finally {
      if (currentLifecycle()) locked.current = false;
      if (currentLifecycle()) {
        setBusy(false);
        setPausing(false);
      }
    }
  }

  async function reload() {
    const generation = epoch.current;
    const currentLifecycle = () =>
      alive.current && generation === epoch.current;
    if (locked.current) return;
    locked.current = true;
    setLoading(true);
    setError('');
    try {
      const data = await workspaceRequest<{ runs: AgentRun[] }>(
        projectId,
        'agent-runs',
      );
      if (!currentLifecycle()) return;
      setRuns(data.runs);
      let selected = data.runs.find(
        (item) => item.id === (pending.current?.runId || run?.id),
      );
      const selectedId = pending.current?.runId || run?.id;
      if (!selected && selectedId)
        selected = await workspaceRequest<AgentRun>(
          projectId,
          `agent-runs/${selectedId}`,
        );
      if (!currentLifecycle()) return;
      setRun(selected || null);
      // A saved mutation can be inspected before any further model request.
      if (
        selected &&
        pending.current &&
        (pending.current.method === 'PUT' ||
          selected.last_reply_id ===
            (pending.current.body as { reply_id?: string }).reply_id)
      ) {
        const submittedMessage = (pending.current.body as { message: string })
          .message;
        pending.current = null;
        persist();
        setHasPending(false);
        if (journal.current.message.trim() === submittedMessage) setMessage('');
      }
    } catch (err) {
      if (currentLifecycle()) setError((err as Error).message);
    } finally {
      if (currentLifecycle()) locked.current = false;
      if (currentLifecycle()) setLoading(false);
    }
  }

  function pause() {
    stop.current = true;
    setPausing(true);
  }

  function startNew() {
    if (locked.current || loading) return;
    // Explicitly leaving an unconfirmed submission must not replay it in a new
    // chat. Any server checkpoint remains available in conversation history.
    pending.current = null;
    setHasPending(false);
    setRun(null);
    setMessage('');
    setError('');
    setOutgoing('');
    setActivity(null);
  }

  async function changeLimits(
    totalTokenLimit: number,
    toolCallLimit: number,
    modelCallLimit: number,
    perTurnModelCallLimit: number,
  ) {
    const generation = epoch.current;
    const currentLifecycle = () =>
      alive.current && generation === epoch.current;
    if (!run?.can_change_limits || locked.current || pending.current) return;
    locked.current = true;
    setBusy(true);
    setError('');
    try {
      const result = await workspaceRequest<AgentRun>(
        projectId,
        `agent-runs/${run.id}/limits`,
        'POST',
        {
          expected_revision: run.revision,
          expected_total_token_limit: run.limits?.total_tokens ?? 100000,
          expected_tool_call_limit: run.limits?.tool_calls ?? 64,
          expected_model_call_limit: run.limits?.model_calls ?? 24,
          expected_per_turn_model_call_limit:
            run.limits?.model_calls_per_turn ?? 16,
          total_token_limit: totalTokenLimit,
          tool_call_limit: toolCallLimit,
          model_call_limit: modelCallLimit,
          per_turn_model_call_limit: perTurnModelCallLimit,
        },
      );
      if (currentLifecycle()) accept(result);
    } catch (err) {
      await recover(run.id, generation);
      if (currentLifecycle()) setError((err as Error).message);
    } finally {
      if (currentLifecycle()) {
        locked.current = false;
        setBusy(false);
      }
    }
  }

  async function changeReasoning(reasoningMode: ReasoningMode) {
    const generation = epoch.current;
    const currentLifecycle = () =>
      alive.current && generation === epoch.current;
    if (!run?.can_change_reasoning || locked.current) return;
    locked.current = true;
    setBusy(true);
    setError('');
    try {
      const result = await workspaceRequest<AgentRun>(
        projectId,
        `agent-runs/${run.id}/reasoning`,
        'POST',
        {
          expected_revision: run.revision,
          expected_reasoning_mode: run.provider.reasoning_mode || 'default',
          reasoning_mode: reasoningMode,
        },
      );
      if (currentLifecycle()) accept(result);
    } catch (err) {
      await recover(run.id, generation);
      if (currentLifecycle()) setError((err as Error).message);
    } finally {
      if (currentLifecycle()) locked.current = false;
      if (currentLifecycle()) setBusy(false);
    }
  }

  async function resetReasoning() {
    const generation = epoch.current;
    const currentLifecycle = () =>
      alive.current && generation === epoch.current;
    if (!run?.can_reset_reasoning || locked.current) return;
    locked.current = true;
    setBusy(true);
    setError('');
    try {
      const result = await workspaceRequest<AgentRun>(
        projectId,
        `agent-runs/${run.id}/reasoning-default`,
        'POST',
        {
          expected_revision: run.revision,
          expected_reasoning_mode: run.provider.reasoning_mode,
        },
      );
      if (currentLifecycle()) accept(result);
    } catch (err) {
      await recover(run.id, generation);
      if (currentLifecycle()) setError((err as Error).message);
    } finally {
      if (currentLifecycle()) locked.current = false;
      if (currentLifecycle()) setBusy(false);
    }
  }

  return {
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
    followLatest,
    send,
    resume,
    reload,
    pause,
    resetReasoning,
    changeReasoning,
    changeLimits,
  };
}
