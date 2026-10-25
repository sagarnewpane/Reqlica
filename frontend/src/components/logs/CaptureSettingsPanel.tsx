import { useEffect, useRef, useState, type FormEvent } from 'react';
import Card from '@/components/ui/Card/Card';
import Button from '@/components/ui/Button/Button';
import Input from '@/components/ui/Input/Input';
import Switch from '@/components/ui/Switch/Switch';
import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogCancel,
} from '@/components/ui/shadcn/alert-dialog';
import { workspaceRequest } from '@/lib/chat';
import { validateCaptureSettings, type CaptureSettings } from '@/lib/logs';
import styles from './RequestLogs.module.css';

export default function CaptureSettingsPanel({
  projectId,
}: {
  projectId: string;
}) {
  const [saved, setSaved] = useState<CaptureSettings | null>(null);
  const [value, setValue] = useState<CaptureSettings | null>(null);
  const [target, setTarget] = useState<CaptureSettings | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [needsReload, setNeedsReload] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const locked = useRef(false);

  useEffect(() => {
    let active = true;
    workspaceRequest<CaptureSettings>(projectId, 'logs/settings')
      .then((data) => {
        if (active) {
          setSaved(data);
          setValue(data);
        }
      })
      .catch((err) => {
        if (active) setError((err as Error).message);
      });
    return () => {
      active = false;
    };
  }, [projectId, attempt]);

  function reload() {
    if (locked.current) return;
    setSaved(null);
    setValue(null);
    setError('');
    setNotice('');
    setNeedsReload(false);
    setAttempt((current) => current + 1);
  }

  function review(event: FormEvent) {
    event.preventDefault();
    if (!value || busy || needsReload) return;
    const invalid = validateCaptureSettings(value);
    if (invalid) {
      setError(invalid);
      return;
    }
    setError('');
    setTarget({ ...value });
  }

  async function save() {
    if (!target || locked.current || needsReload) return;
    locked.current = true;
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const data = await workspaceRequest<CaptureSettings>(
        projectId,
        'logs/settings',
        'PUT',
        {
          expected_revision: target.revision,
          capture_payloads: target.capture_payloads,
          capture_output: target.capture_output,
          max_body_bytes: target.max_body_bytes,
          confirm: true,
        },
      );
      setSaved(data);
      setValue(data);
      setTarget(null);
      setNotice(
        'Capture settings saved. Changes apply immediately; already retained evidence is not deleted.',
      );
    } catch (err) {
      setError((err as Error).message);
      setNeedsReload(true);
    } finally {
      locked.current = false;
      setBusy(false);
    }
  }

  const dirty =
    value && saved && JSON.stringify(value) !== JSON.stringify(saved);
  return (
    <Card className={styles.capture} id="capture-settings">
      <div className={styles.sectionHeading}>
        <div>
          <h2>Capture settings</h2>
          <p>
            Project-scoped evidence collection. Both capture modes default to
            off.
          </p>
        </div>
        <button
          type="button"
          className={styles.secondary}
          disabled={busy || (!value && !error)}
          onClick={reload}
        >
          Reload capture settings
        </button>
      </div>
      <p className={styles.warning}>
        Redaction is best-effort. Bodies and output can contain sensitive data,
        especially raw stdout/stderr. Capture is readable by local management
        tools and Project AI. Use only appropriate local test data. Turning
        capture off does not delete existing events.
      </p>
      {!value && !error && <p role="status">Loading capture settings…</p>}
      {error && !target && (
        <p role="alert" className={styles.warning}>
          {error}
        </p>
      )}
      {notice && (
        <p role="status" className={styles.notice}>
          {notice}
        </p>
      )}
      {needsReload && (
        <p role="alert" className={styles.warning}>
          Reload capture settings before saving again. A previous update may
          already have been applied.
        </p>
      )}
      {value && (
        <form onSubmit={review}>
          <fieldset disabled={busy || needsReload} className={styles.fields}>
            <label className={styles.toggle}>
              <span>
                <strong>Request & response bodies</strong>
                <span>
                  Bounded previews only. Headers and query strings remain
                  excluded.
                </span>
              </span>
              <Switch
                aria-label="Capture request and response bodies"
                checked={value.capture_payloads}
                onCheckedChange={(checked) =>
                  setValue(
                    (current) =>
                      current && { ...current, capture_payloads: checked },
                  )
                }
              />
            </label>
            <label className={styles.toggle}>
              <span>
                <strong>Stdout & stderr</strong>
                <span>
                  Process-wide output, up to 2 KiB per write; not
                  request-specific.
                </span>
              </span>
              <Switch
                aria-label="Capture stdout and stderr"
                checked={value.capture_output}
                onCheckedChange={(checked) =>
                  setValue(
                    (current) =>
                      current && { ...current, capture_output: checked },
                  )
                }
              />
            </label>
            <div className={styles.byteLimit}>
              <label htmlFor="capture-bytes">Body preview limit (bytes)</label>
              <Input
                id="capture-bytes"
                type="number"
                min={128}
                max={2048}
                step={1}
                required
                value={value.max_body_bytes}
                onChange={(event) =>
                  setValue(
                    (current) =>
                      current && {
                        ...current,
                        max_body_bytes: Number(event.target.value),
                      },
                  )
                }
              />
            </div>
            <div className={styles.actions}>
              <p>
                Saving requires confirmation and the loaded settings revision.
              </p>
              <Button type="submit" disabled={!dirty}>
                Review capture changes…
              </Button>
            </div>
          </fieldset>
        </form>
      )}
      <AlertDialog
        open={target !== null}
        onOpenChange={(next) => {
          if (!next && !locked.current) setTarget(null);
        }}
      >
        <AlertDialogContent
          className={styles.dialog}
          onEscapeKeyDown={(event) => {
            if (locked.current) event.preventDefault();
          }}
        >
          <AlertDialogHeader>
            <AlertDialogTitle>Apply capture settings?</AlertDialogTitle>
            <AlertDialogDescription>
              Changes apply immediately to this project. Captured data is
              retained locally and can be read by Project AI. Redaction may miss
              secrets. Disabling capture does not erase previously recorded
              data.
            </AlertDialogDescription>
          </AlertDialogHeader>
          {target && (
            <dl className={styles.metadata}>
              <dt>Request & response bodies</dt>
              <dd>{target.capture_payloads ? 'On' : 'Off'}</dd>
              <dt>Stdout & stderr</dt>
              <dd>{target.capture_output ? 'On' : 'Off'}</dd>
              <dt>Body preview limit</dt>
              <dd>{target.max_body_bytes} bytes</dd>
            </dl>
          )}
          {error && (
            <p role="alert" className={styles.warning}>
              {error} Cancel and reload capture settings before continuing.
            </p>
          )}
          <AlertDialogFooter>
            <AlertDialogCancel disabled={busy}>Cancel</AlertDialogCancel>
            <Button
              type="button"
              disabled={busy || needsReload}
              onClick={() => void save()}
            >
              {busy ? 'Saving…' : 'Apply capture settings'}
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Card>
  );
}
