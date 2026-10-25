import { useEffect, useRef, useState } from 'react';
import Button from '@/components/ui/Button/Button';
import { workspaceRequest } from '@/lib/chat';
import styles from './SpecificationAttachment.module.css';

export type Specification = {
  filename: string;
  content: string;
  operations?: string[];
};
export type Attachment = { specification?: Specification; ready: boolean };
type Preview = {
  kind: string;
  operations: string[];
  context_bytes: number;
  ready: boolean;
};

export default function SpecificationAttachment({
  projectId,
  disabled,
  onChange,
}: {
  projectId: string;
  disabled: boolean;
  onChange: (value: Attachment) => void;
}) {
  const [spec, setSpec] = useState<Specification>();
  const [preview, setPreview] = useState<Preview>();
  const [error, setError] = useState('');
  const [checking, setChecking] = useState(false);
  const fileSequence = useRef(0);
  useEffect(() => {
    onChange({ ready: true });
  }, [projectId, onChange]);
  useEffect(() => {
    if (!spec) return;
    let active = true;
    onChange({ specification: spec, ready: false });
    const timer = setTimeout(() => {
      setChecking(true);
      setError('');
      workspaceRequest<Preview>(
        projectId,
        'specifications/preview',
        'POST',
        spec,
      )
        .then((result) => {
          if (!active) return;
          setPreview(result);
          onChange({ specification: spec, ready: result.ready });
          if (!result.ready)
            setError(
              'Selected context exceeds 24 KiB. Select fewer operations or split the document.',
            );
        })
        .catch((reason: Error) => {
          if (active) setError(reason.message);
        })
        .finally(() => {
          if (active) setChecking(false);
        });
    }, 300);
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [spec, projectId, onChange]);

  return (
    <div className={styles.attachment}>
      <label className={styles.upload}>
        Attach specification
        <input
          type="file"
          accept=".json,.yaml,.yml,.md,.txt"
          disabled={disabled}
          onChange={async (event) => {
            const file = event.target.files?.[0];
            event.target.value = '';
            if (!file) return;
            const sequence = ++fileSequence.current;
            setError('');
            setPreview(undefined);
            setSpec(undefined);
            setChecking(false);
            setChecking(false);
            onChange({ ready: false });
            if (file.size > 131072) {
              setError('Attachment exceeds 128 KiB.');
              return;
            }
            try {
              const content = await file.text();
              if (sequence !== fileSequence.current) return;
              if (!content.trim()) throw new Error('Attachment is empty.');
              setSpec({ filename: file.name, content });
              onChange({ ready: false });
            } catch (reason) {
              if (sequence === fileSequence.current)
                setError((reason as Error).message);
            }
          }}
        />
      </label>
      <small>
        New chats only · OpenAPI JSON/YAML or Markdown/text · 128 KiB upload, 24
        KiB context · no AI call until Send
      </small>
      {spec && (
        <>
          <div className={styles.heading}>
            <strong>{spec.filename}</strong>
            <Button
              type="button"
              variant="secondary"
              disabled={disabled}
              onClick={() => {
                fileSequence.current++;
                setSpec(undefined);
                setChecking(false);
                setChecking(false);
                setPreview(undefined);
                setError('');
                onChange({ ready: true });
              }}
            >
              Remove
            </Button>
          </div>
          <details>
            <summary>Review / edit attachment</summary>
            <textarea
              aria-label="Specification contents"
              value={spec.content}
              disabled={disabled}
              rows={6}
              onChange={(event) => {
                setSpec({
                  ...spec,
                  content: event.target.value,
                  operations: undefined,
                });
                setPreview(undefined);
                onChange({ ready: false });
              }}
            />
          </details>
          {!!preview?.operations.length && (
            <details>
              <summary>
                Select operations (
                {spec.operations?.length ?? preview.operations.length})
              </summary>
              <div className={styles.operations}>
                {preview.operations.map((operation) => (
                  <label key={operation}>
                    <input
                      type="checkbox"
                      disabled={disabled}
                      checked={
                        !spec.operations || spec.operations.includes(operation)
                      }
                      onChange={(event) => {
                        const selected = new Set(
                          spec.operations || preview.operations,
                        );
                        if (event.target.checked) selected.add(operation);
                        else selected.delete(operation);
                        setSpec({ ...spec, operations: [...selected] });
                        onChange({ ready: false });
                      }}
                    />{' '}
                    {operation}
                  </label>
                ))}
              </div>
            </details>
          )}
          {preview && (
            <small>
              {preview.kind === 'openapi'
                ? 'OpenAPI contract checks enabled when tests run.'
                : 'Prose requirements need behavioral tests; automatic OpenAPI checks do not apply.'}{' '}
              Selected context: {preview.context_bytes.toLocaleString()} bytes.
            </small>
          )}
        </>
      )}
      {checking && <small role="status">Validating attachment…</small>}
      {error && <p role="alert">{error}</p>}
      {error && !spec && (
        <Button
          type="button"
          variant="secondary"
          disabled={disabled}
          onClick={() => {
            fileSequence.current++;
            setError('');
            onChange({ ready: true });
          }}
        >
          Dismiss attachment
        </Button>
      )}
    </div>
  );
}
