import { useState } from 'react';
import Button from '@/components/ui/Button/Button';
import Input from '@/components/ui/Input/Input';
import type { AgentRun } from '@/lib/chat';
import type { ChatSettings } from '@/lib/settings';
import styles from './ConversationBudgets.module.css';

type Budgets = Pick<
  ChatSettings,
  | 'totalTokenLimit'
  | 'toolCallLimit'
  | 'modelCallLimit'
  | 'perTurnModelCallLimit'
>;

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
  onChange: (budgets: Budgets) => void;
  onApply: (
    tokens: number,
    tools: number,
    modelCalls: number,
    perTurnModelCalls: number,
  ) => Promise<void>;
}) {
  const savedTokens = run?.limits?.total_tokens ?? 100000;
  const savedTools = run?.limits?.tool_calls ?? 64;
  const savedModelCalls = run?.limits?.model_calls ?? 24;
  const savedPerTurn = run?.limits?.model_calls_per_turn ?? 16;
  const [tokens, setTokens] = useState(savedTokens);
  const [tools, setTools] = useState(savedTools);
  const [modelCalls, setModelCalls] = useState(savedModelCalls);
  const [perTurn, setPerTurn] = useState(savedPerTurn);
  const tokenValue = run ? tokens : preferences.totalTokenLimit;
  const toolValue = run ? tools : preferences.toolCallLimit;
  const modelValue = run ? modelCalls : preferences.modelCallLimit;
  const perTurnValue = run ? perTurn : preferences.perTurnModelCallLimit;
  const tokenUsage = run?.reported_tokens ?? 0;
  const toolUsage = run?.tool_calls ?? 0;
  const modelUsage = run?.model_calls ?? 0;
  const turnUsage = run?.turn_model_calls ?? 0;
  const valid =
    Number.isInteger(tokenValue) &&
    tokenValue >= Math.max(1000, tokenUsage) &&
    tokenValue <= 1000000 &&
    Number.isInteger(toolValue) &&
    toolValue >= Math.max(1, toolUsage) &&
    toolValue <= 1000 &&
    Number.isInteger(modelValue) &&
    modelValue >= Math.max(1, modelUsage) &&
    modelValue <= 1000 &&
    Number.isInteger(perTurnValue) &&
    perTurnValue >= Math.max(1, turnUsage) &&
    perTurnValue <= modelValue;
  const changed =
    tokens !== savedTokens ||
    tools !== savedTools ||
    modelCalls !== savedModelCalls ||
    perTurn !== savedPerTurn;
  const update = (patch: Partial<Budgets>) => {
    if (!run)
      onChange({
        totalTokenLimit: tokenValue,
        toolCallLimit: toolValue,
        modelCallLimit: modelValue,
        perTurnModelCallLimit: perTurnValue,
        ...patch,
      });
  };
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
              update({ totalTokenLimit: next });
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
              update({ toolCallLimit: next });
            }}
          />
          <p id="chat-tool-usage">
            {toolUsage.toLocaleString()} used · includes failed attempts
          </p>
        </div>
        <div>
          <label htmlFor="chat-model-call-budget">Model calls per chat</label>
          <Input
            id="chat-model-call-budget"
            type="number"
            inputMode="numeric"
            min={1}
            max={1000}
            step={1}
            value={modelValue}
            aria-describedby="chat-model-call-usage"
            onChange={(event) => {
              const next = Number(event.target.value);
              setModelCalls(next);
              update({ modelCallLimit: next });
            }}
          />
          <p id="chat-model-call-usage">
            {modelUsage.toLocaleString()} used · every model request, including
            retries
          </p>
        </div>
        <div>
          <label htmlFor="chat-turn-budget">Model calls per request</label>
          <Input
            id="chat-turn-budget"
            type="number"
            inputMode="numeric"
            min={1}
            max={1000}
            step={1}
            value={perTurnValue}
            aria-describedby="chat-turn-usage"
            onChange={(event) => {
              const next = Number(event.target.value);
              setPerTurn(next);
              update({ perTurnModelCallLimit: next });
            }}
          />
          <p id="chat-turn-usage">
            {turnUsage.toLocaleString()} used this request · raise it to
            continue a stopped build
          </p>
        </div>
      </div>
      {!valid && (
        <p role="alert">
          Use 1,000–1,000,000 tokens and 1–1,000 tools or model calls, no lower
          than recorded usage. Per-request model calls cannot exceed calls per
          chat.
        </p>
      )}
      {run && (
        <div className={styles.actions}>
          <Button
            type="button"
            variant="secondary"
            disabled={!valid || !changed}
            onClick={() => void onApply(tokens, tools, modelCalls, perTurn)}
          >
            Update this chat’s budgets
          </Button>
          <p>
            Does not start execution. Resume separately after reviewing the
            draft.
          </p>
        </div>
      )}
    </fieldset>
  );
}
