import { useEffect, useState } from 'react';
import { Braces, RefreshCw, X } from 'lucide-react';
import Button from '@/components/ui/Button/Button';
import {
  Sheet,
  SheetTrigger,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
  SheetClose,
} from '@/components/ui/shadcn/sheet';
import { workspaceRequest, type AgentRun, type ModelContext } from '@/lib/chat';
import { formatBytes, totalUsage } from '@/lib/chat-observability';
import styles from './ChatInsights.module.css';

export default function ChatContext({
  projectId,
  run,
  model,
  provider,
}: {
  projectId: string;
  run: AgentRun | null;
  model: string;
  provider: string;
}) {
  const [open, setOpen] = useState(false);
  const [context, setContext] = useState<ModelContext | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [attempt, setAttempt] = useState(0);
  const runId = run?.id;
  useEffect(() => {
    if (!open || !runId) return;
    const controller = new AbortController();
    workspaceRequest<ModelContext>(
      projectId,
      `agent-runs/${runId}/context`,
      'GET',
      undefined,
      undefined,
      { signal: controller.signal },
    )
      .then((data) => {
        if (!controller.signal.aborted) {
          setContext(data);
          setError('');
        }
      })
      .catch((err) => {
        if (!controller.signal.aborted) setError((err as Error).message);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [projectId, runId, open, attempt]);

  const usage = run ? totalUsage(run) : null;
  const summary = context?.summary || run?.context;
  const lastUsage =
    context?.last_model_response?.usage || run?.last_model_response?.usage;
  const currentModel = run?.provider.model || model;
  const count = (value: number | undefined) =>
    value === undefined ? 'Not reported' : value.toLocaleString();

  return (
    <div className={styles.contextBar} aria-label="Conversation context">
      {run?.context && (
        <span
          className={styles.metric}
          title="Last saved model input size, including tools. Not the model’s context-window capacity."
        >
          {formatBytes(run.context.bytes)} input
        </span>
      )}
      {usage?.total_tokens !== undefined && (
        <span
          className={styles.metric}
          title="Cumulative provider-reported input and output across all model calls. Replayed history counts again on each call. The limit is a budget, not usage."
        >
          {usage.total_tokens.toLocaleString()}
          {run?.limits?.total_tokens
            ? ` / ${run.limits.total_tokens.toLocaleString()}`
            : ''}{' '}
          cumulative tokens
        </span>
      )}
      <Sheet
        open={open}
        onOpenChange={(next) => {
          setOpen(next);
          if (next) {
            setLoading(true);
            setError('');
          }
        }}
      >
        <SheetTrigger asChild>
          <Button
            type="button"
            variant="ghost"
            disabled={!run}
            aria-label="View model context"
            className={styles.contextButton}
          >
            <Braces size={16} aria-hidden="true" />
            Context
          </Button>
        </SheetTrigger>
        <SheetContent
          className={styles.contextSheet}
          showCloseButton={false}
          aria-describedby="model-context-description"
        >
          <SheetHeader className={styles.sheetHeader}>
            <div className={styles.sheetTitleRow}>
              <SheetTitle>Model context</SheetTitle>
              <SheetClose asChild>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  aria-label="Close model context"
                >
                  <X size={16} />
                </Button>
              </SheetClose>
            </div>
            <SheetDescription id="model-context-description">
              Inspect model input without running another request.
            </SheetDescription>
          </SheetHeader>
          <div className={styles.sheetBody}>
            <div className={styles.snapshotHeading}>
              <span>
                {context?.source === 'captured'
                  ? 'Last sent input'
                  : context
                    ? 'Prepared input · not yet sent'
                    : 'Model input'}
              </span>
              <Button
                type="button"
                variant="secondary"
                disabled={loading}
                onClick={() => {
                  setLoading(true);
                  setAttempt((value) => value + 1);
                }}
              >
                <RefreshCw size={16} />
                {loading ? 'Loading…' : 'Refresh'}
              </Button>
            </div>
            {error && (
              <p role="alert" className={styles.error}>
                {error}
              </p>
            )}
            {loading && !context && <p role="status">Loading context…</p>}
            <dl className={styles.metrics}>
              <div>
                <dt>Model</dt>
                <dd>{currentModel}</dd>
              </div>
              <div>
                <dt>Provider</dt>
                <dd>{run?.provider.provider || provider}</dd>
              </div>
              <div>
                <dt>Scope</dt>
                <dd>
                  {run?.draft_id
                    ? `Draft ${run.draft_id.slice(0, 8)}`
                    : 'Published API'}
                </dd>
              </div>
              <div>
                <dt>Permissions</dt>
                <dd>
                  {run?.allow_edits ? 'Draft edits' : 'Read-only'} ·{' '}
                  {run?.allow_execution ? 'Checks enabled' : 'Checks disabled'}
                </dd>
              </div>
              <div>
                <dt>Model calls</dt>
                <dd>
                  {run?.model_calls ?? 0}
                  {run?.limits ? ` / ${run.limits.model_calls}` : ''}
                </dd>
              </div>
              {run?.limits?.model_calls_per_turn && (
                <div>
                  <dt>This request’s model calls</dt>
                  <dd>
                    {run.turn_model_calls ?? 0} /{' '}
                    {run.limits.model_calls_per_turn}
                  </dd>
                </div>
              )}
              <div>
                <dt>Tool calls</dt>
                <dd>
                  {run?.tool_calls ?? 0}
                  {run?.limits ? ` / ${run.limits.tool_calls}` : ''}
                </dd>
              </div>
              <div>
                <dt>Last input size</dt>
                <dd>
                  {summary ? formatBytes(summary.bytes) : 'Not captured yet'}
                </dd>
              </div>
              <div>
                <dt>Messages / tool definitions</dt>
                <dd>
                  {summary
                    ? `${summary.message_count} / ${summary.tool_count}`
                    : 'Not captured yet'}
                </dd>
              </div>
              <div>
                <dt>Last input tokens</dt>
                <dd>{count(lastUsage?.prompt_tokens)}</dd>
              </div>
              <div>
                <dt>Last output tokens</dt>
                <dd>{count(lastUsage?.completion_tokens)}</dd>
              </div>
              <div>
                <dt>Reported conversation tokens</dt>
                <dd>
                  {count(usage?.total_tokens)}
                  {run?.limits?.total_tokens
                    ? ` / ${run.limits.total_tokens.toLocaleString()}`
                    : ''}
                </dd>
              </div>
              <div>
                <dt>Output limit per call</dt>
                <dd>{count(summary?.max_output_tokens)}</dd>
              </div>
            </dl>
            {context && (
              <>
                <p className={styles.note}>
                  Input size is measured in bytes, not the model’s token
                  capacity. Token usage appears only when reported by the
                  provider.
                </p>
                <details className={styles.payload}>
                  <summary>Messages ({context.messages.length})</summary>
                  <pre>{JSON.stringify(context.messages, null, 2)}</pre>
                </details>
                <details className={styles.payload}>
                  <summary>Tool definitions ({context.tools.length})</summary>
                  <pre>{JSON.stringify(context.tools, null, 2)}</pre>
                </details>
              </>
            )}
          </div>
        </SheetContent>
      </Sheet>
    </div>
  );
}
