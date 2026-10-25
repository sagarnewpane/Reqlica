import { useEffect, useRef, useState } from 'react';
import Button from '@/components/ui/Button/Button';
import SavedItems from '@/components/workspace/SavedItems';
import { executeRequest, type RequestConfig } from '@/lib/request-execution';
import {
  resolveChainStep,
  type ChainStep,
  type ChainResult,
} from '@/lib/request-chains';
import s from '@/components/workspace/Workspace.module.css';
export default function RequestChains({
  projectId,
  current,
}: {
  projectId: string;
  current: RequestConfig;
}) {
  const [steps, setSteps] = useState<ChainStep[]>([]);
  const [results, setResults] = useState<ChainResult[]>([]);
  const [stopOnFailure, setStopOnFailure] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const stop = useRef(false);
  useEffect(
    () => () => {
      stop.current = true;
    },
    [],
  );
  async function run() {
    if (busy) return;
    setBusy(true);
    setError('');
    setResults([]);
    stop.current = false;
    const values: Record<string, unknown> = {};
    try {
      for (const step of steps) {
        if (stop.current) break;
        let row: ChainResult;
        try {
          const response = await executeRequest(
            projectId,
            resolveChainStep(step, values),
          );
          let body: unknown = response.body;
          try {
            body = JSON.parse(response.body);
          } catch {}
          values[step.name] = {
            body,
            status: response.status,
            headers: response.headers,
          };
          row = {
            step: step.name,
            result: response,
            passed: response.status === step.expectedStatus,
          };
        } catch (reason) {
          row = {
            step: step.name,
            passed: false,
            error: (reason as Error).message,
          };
        }
        setResults((rows) => [...rows, row]);
        if (!row.passed && stopOnFailure) break;
      }
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className={s.panel}>
      <h2>Request chains</h2>
      <p>
        Run requests in order. Use {'{{create.body.id}}'} in a later path or a
        JSON string to use an earlier response. JSON bodies preserve the
        referenced value’s type. Each draft step runs against disposable data;
        use the published API for chains that need persistent writes. Saved
        chains omit header overrides; shared runtime headers still apply.
      </p>
      <SavedItems
        projectId={projectId}
        kind="chain"
        disabled={busy}
        data={{
          steps: steps.map((step) => ({
            name: step.name,
            method: step.method,
            path: step.path,
            body: step.body,
            draftId: step.draftId,
            expectedStatus: step.expectedStatus,
          })),
          stopOnFailure,
        }}
        onLoad={(data) => {
          const parsed = data as { steps: ChainStep[]; stopOnFailure: boolean };
          if (
            !Array.isArray(parsed.steps) ||
            parsed.steps.length > 50 ||
            parsed.steps.some(
              (step) => !step.name || !step.path || !step.method,
            )
          )
            throw new Error('Invalid saved chain.');
          setSteps(parsed.steps);
          setStopOnFailure(parsed.stopOnFailure !== false);
          setResults([]);
        }}
      />
      <ol className={s.list}>
        {steps.map((step, index) => (
          <li className={s.item} key={index}>
            <div className={s.grid}>
              <label>
                Step name
                <input
                  value={step.name}
                  pattern="[A-Za-z][A-Za-z0-9_]*"
                  onChange={(e) =>
                    setSteps(
                      steps.map((item, i) =>
                        i === index ? { ...item, name: e.target.value } : item,
                      ),
                    )
                  }
                  disabled={busy}
                />
              </label>
              <label>
                Method
                <select
                  value={step.method}
                  disabled={busy}
                  onChange={(e) =>
                    setSteps(
                      steps.map((item, i) =>
                        i === index
                          ? { ...item, method: e.target.value }
                          : item,
                      ),
                    )
                  }
                >
                  {[
                    'GET',
                    'POST',
                    'PUT',
                    'PATCH',
                    'DELETE',
                    'HEAD',
                    'OPTIONS',
                  ].map((v) => (
                    <option key={v}>{v}</option>
                  ))}
                </select>
              </label>
              <label>
                Path
                <input
                  value={step.path}
                  disabled={busy}
                  onChange={(e) =>
                    setSteps(
                      steps.map((item, i) =>
                        i === index ? { ...item, path: e.target.value } : item,
                      ),
                    )
                  }
                />
              </label>
              <label>
                Expected status
                <input
                  type="number"
                  min={100}
                  max={599}
                  value={step.expectedStatus}
                  disabled={busy}
                  onChange={(e) =>
                    setSteps(
                      steps.map((item, i) =>
                        i === index
                          ? { ...item, expectedStatus: Number(e.target.value) }
                          : item,
                      ),
                    )
                  }
                />
              </label>
            </div>
            <label>
              JSON body
              <textarea
                value={step.body}
                disabled={busy}
                onChange={(e) =>
                  setSteps(
                    steps.map((item, i) =>
                      i === index ? { ...item, body: e.target.value } : item,
                    ),
                  )
                }
              />
            </label>
            <div className={s.row}>
              <Button
                variant="secondary"
                disabled={busy || index === 0}
                onClick={() => {
                  const next = [...steps];
                  [next[index - 1], next[index]] = [
                    next[index],
                    next[index - 1],
                  ];
                  setSteps(next);
                }}
              >
                Move up
              </Button>
              <Button
                variant="secondary"
                disabled={busy}
                onClick={() => setSteps(steps.filter((_, i) => i !== index))}
              >
                Remove step
              </Button>
            </div>
          </li>
        ))}
      </ol>
      <label className={s.check}>
        <input
          type="checkbox"
          checked={stopOnFailure}
          disabled={busy}
          onChange={(e) => setStopOnFailure(e.target.checked)}
        />
        Stop on first failed status or request
      </label>
      <div className={s.row}>
        <Button
          variant="secondary"
          disabled={busy || steps.length >= 50}
          onClick={() =>
            setSteps([
              ...steps,
              {
                ...current,
                name: `step${steps.length + 1}`,
                expectedStatus: 200,
              },
            ])
          }
        >
          Add current request
        </Button>
        <Button
          disabled={busy || !steps.length}
          onClick={() => {
            if (
              new Set(steps.map((step) => step.name)).size !== steps.length ||
              steps.some((step) => !/^[A-Za-z][A-Za-z0-9_]*$/.test(step.name))
            ) {
              setError(
                'Use unique step names: letters, digits, underscores; start with a letter.',
              );
              return;
            }
            void run();
          }}
        >
          Run chain
        </Button>
        {busy && (
          <Button
            variant="secondary"
            onClick={() => {
              stop.current = true;
            }}
          >
            Stop after current request
          </Button>
        )}
      </div>
      {error && <p role="alert">{error}</p>}
      {results.map((row, index) => (
        <details key={index}>
          <summary>
            {row.step}: {row.passed ? 'passed' : 'failed'} {row.result?.status}
          </summary>
          <pre>{row.error || JSON.stringify(row.result, null, 2)}</pre>
        </details>
      ))}
    </section>
  );
}
