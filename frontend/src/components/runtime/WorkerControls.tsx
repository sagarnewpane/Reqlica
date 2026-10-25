import { Play, Square, RefreshCw } from 'lucide-react';
import Badge from '@/components/ui/Badge/Badge';
import Button from '@/components/ui/Button/Button';
import Card from '@/components/ui/Card/Card';
import PageHeader from '@/components/ui/PageHeader/PageHeader';
import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogCancel,
} from '@/components/ui/shadcn/alert-dialog';
import type { Project } from '@/lib/projects';
import { workerCapabilities, workerStatusDescription } from '@/lib/workers';
import { useWorkerControls } from './use-worker-controls';
import styles from './WorkerControls.module.css';

export default function WorkerControls(props: {
  projectId: string;
  project?: Project;
  title?: string;
  onProjectChange?: (project: Project) => void;
}) {
  const worker = useWorkerControls(props);
  const { project, verified, busy, target } = worker;
  const state = verified ? project?.status : undefined;
  const capabilities = workerCapabilities(state);
  const disabled = !!busy || !verified;
  const pid = project?.pid;
  const metadata = (
    <>
      <Badge
        variant={
          state === 'running'
            ? 'success'
            : state === 'error'
              ? 'danger'
              : 'warning'
        }
      >
        {state || 'Unverified'}
      </Badge>
      {verified && Number.isSafeInteger(pid) && Number(pid) > 0 && (
        <span className={styles.pid} aria-label={`Process ID ${pid}`}>
          PID <code>{pid}</code>
        </span>
      )}
    </>
  );
  const actions = (
    <>
      <Button
        type="button"
        disabled={disabled || !capabilities.canStart}
        onClick={() => worker.open('start')}
      >
        <Play size={16} aria-hidden="true" />
        {busy === 'start' ? 'Starting…' : 'Start'}
      </Button>
      <Button
        type="button"
        variant="secondary"
        disabled={disabled || !capabilities.canStop}
        onClick={() => worker.open('stop')}
      >
        <Square size={16} aria-hidden="true" />
        {busy === 'stop' ? 'Stopping…' : 'Stop'}
      </Button>
      <Button
        type="button"
        variant="secondary"
        disabled={!!busy}
        onClick={() => void worker.refresh()}
      >
        <RefreshCw size={16} aria-hidden="true" />
        {busy === 'refresh' ? 'Refreshing…' : 'Refresh'}
      </Button>
    </>
  );

  return (
    <Card
      className={`${styles.controls} ${props.title ? styles.compact : ''}`}
      role="region"
      aria-labelledby="worker-heading"
    >
      {props.title ? (
        <PageHeader
          id="worker-heading"
          title={props.title}
          metadata={metadata}
          actions={actions}
          className={styles.pageHeader}
        />
      ) : (
        <div className={styles.heading}>
          <div className={styles.identity}>
            <h2 id="worker-heading">API runtime</h2>
            {metadata}
          </div>
          <div className={styles.buttons} aria-label="Runtime actions">
            {actions}
          </div>
        </div>
      )}
      {!props.title && (
        <details className={styles.details}>
          <summary>Details</summary>
          <p className={styles.context}>{workerStatusDescription(state)}</p>
          {verified && project && (
            <dl className={styles.metadata}>
              <div>
                <dt>Process ID</dt>
                <dd>
                  {Number.isSafeInteger(pid) && Number(pid) > 0
                    ? pid
                    : 'Not reported'}
                </dd>
              </div>
              {project.environment_revision && (
                <div>
                  <dt>Loaded environment revision</dt>
                  <dd>
                    <code>{project.environment_revision.slice(0, 12)}</code>
                  </dd>
                </div>
              )}
            </dl>
          )}
          <p className={styles.context}>
            After changing environment variables, stop and start the API to
            apply them. Status reflects the last refresh.
          </p>
        </details>
      )}
      {!project && !worker.error && (
        <p role="status" className={styles.context}>
          Loading worker status…
        </p>
      )}
      {verified && project?.error && (
        <p role="alert" className={styles.warning}>
          {project.error}
        </p>
      )}
      {worker.error && !target && (
        <p role="alert" className={styles.warning}>
          {worker.error}
        </p>
      )}
      {worker.notice && (
        <p role="status" className={styles.notice}>
          {worker.notice}
        </p>
      )}
      {busy && (
        <p role="status" className={styles.context}>
          {busy === 'refresh'
            ? 'Refreshing worker status…'
            : busy === 'start'
              ? 'Starting worker…'
              : 'Stopping worker…'}
        </p>
      )}
      <AlertDialog
        open={target !== null}
        onOpenChange={(next) => {
          if (!next) worker.close();
        }}
      >
        <AlertDialogContent
          className={styles.dialog}
          onEscapeKeyDown={(event) => {
            if (busy) event.preventDefault();
          }}
        >
          <AlertDialogHeader>
            <AlertDialogTitle>
              {target?.action === 'start' ? 'Start API?' : 'Stop API?'}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {target?.action === 'start'
                ? 'Runs this project’s published Python code with access to project data and this machine. Only start code you trust. Drafts are not published.'
                : 'Active requests may be interrupted. This API will return 503 until you start it again. Your files, data, and drafts are kept.'}
            </AlertDialogDescription>
          </AlertDialogHeader>
          {target && (
            <dl className={styles.target}>
              <dt>Project</dt>
              <dd>{target.name}</dd>
            </dl>
          )}
          {worker.error && (
            <p role="alert" className={styles.warning}>
              {worker.error} Cancel, then use Refresh status.
            </p>
          )}
          <AlertDialogFooter>
            <AlertDialogCancel disabled={!!busy}>Cancel</AlertDialogCancel>
            <Button
              type="button"
              disabled={disabled}
              onClick={() => void worker.confirm()}
            >
              {busy
                ? 'Working…'
                : target?.action === 'start'
                  ? 'Start API'
                  : 'Stop API'}
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Card>
  );
}
