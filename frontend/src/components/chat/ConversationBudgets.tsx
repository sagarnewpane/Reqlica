import { useState } from 'react';
import Button from '@/components/ui/Button/Button';
import Input from '@/components/ui/Input/Input';
import type { AgentRun } from '@/lib/chat';
import type { ChatSettings } from '@/lib/settings';
import styles from './ConversationBudgets.module.css';

export default function ConversationBudgets({
  run,
  preferences,
  disabled,
  onChange,
  onApply,
}: {
  run: AgentRun | null;
  preferences: ChatSettings;
  disabled: boolean;
  onChange: (
    budgets: Pick<ChatSettings, 'totalTokenLimit' | 'toolCallLimit'>,
  ) => void;
  onApply: (tokens: number, tools: number) => Promise<void>;
}) {
  const savedTokens = run?.limits?.total_tokens ?? 100000;
  const savedTools = run?.limits?.tool_calls ?? 64;
  const [tokens, setTokens] = useState(savedTokens);
  const [tools, setTools] = useState(savedTools);
  const tokenValue = run ? tokens : preferences.totalTokenLimit;
  const toolValue = run ? tools : preferences.toolCallLimit;
  const tokenUsage = run?.reported_tokens ?? 0;
  const toolUsage = run?.tool_calls ?? 0;
  const valid =
    Number.isInteger(tokenValue) &&
    tokenValue >= Math.max(1000, tokenUsage) &&
    tokenValue <= 1000000 &&
    Number.isInteger(toolValue) &&
    toolValue >= Math.max(1, toolUsage) &&
    toolValue <= 1000;
  return (
    <fieldset
      className={styles.budgets}
      disabled={disabled || (!!run && !run.can_change_limits)}
    >
      <legend>Conversation budgets</legend>
      <p>
        {run
          ? 'These are this chat’s saved limits, not the defaults in Settings. Updating them preserves usage and pending tools.'
          : 'Budgets for this new chat. Workspace and project defaults are unchanged.'}
      </p>
      <div className={styles.grid}>
        <div>
          <label htmlFor="chat-token-budget">Cumulative token budget</label>
          <Input
            id="chat-token-budget"
            type="number"
            inputMode="numeric"
            min={1000}
            max={1000000}
            step={1}
            value={tokenValue}
            aria-describedby="chat-token-usage"
            onChange={(event) => {
              const next = Number(event.target.value);
              setTokens(next);
              if (!run)
                onChange({ totalTokenLimit: next, toolCallLimit: toolValue });
            }}
          />
          <p id="chat-token-usage">
            {tokenUsage.toLocaleString()} used · input + output across all model
            calls
          </p>
        </div>
        <div>
          <label htmlFor="chat-tool-budget">Tool-call budget</label>
          <Input
            id="chat-tool-budget"
            type="number"
            inputMode="numeric"
            min={1}
            max={1000}
            step={1}
            value={toolValue}
            aria-describedby="chat-tool-usage"
            onChange={(event) => {
              const next = Number(event.target.value);
              setTools(next);
              if (!run)
                onChange({ totalTokenLimit: tokenValue, toolCallLimit: next });
            }}
          />
          <p id="chat-tool-usage">
            {toolUsage.toLocaleString()} used · includes failed attempts
          </p>
        </div>
      </div>
      {!valid && (
        <p role="alert">
          Use 1,000–1,000,000 tokens and 1–1,000 tool calls, no lower than
          recorded usage.
        </p>
      )}
      {run && (
        <div className={styles.actions}>
          <Button
            type="button"
            variant="secondary"
            disabled={
              !valid || (tokens === savedTokens && tools === savedTools)
            }
            onClick={() => void onApply(tokens, tools)}
          >
            Update this chat’s budgets
          </Button>
          <p>
            Does not start execution. Resume separately after reviewing the
            draft.
          </p>
        </div>
      )}
      <p>
        Separate model-call limits still apply: {run?.limits?.model_calls ?? 24}{' '}
        per chat, {run?.limits?.model_calls_per_turn ?? 16} per turn. This is a
        usage budget, not the model’s context window.
      </p>
    </fieldset>
  );
}
