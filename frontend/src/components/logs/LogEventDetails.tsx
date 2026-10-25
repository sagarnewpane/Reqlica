import { useEffect, useRef, useState } from 'react';
import Card from '@/components/ui/Card/Card';
import Button from '@/components/ui/Button/Button';
import { workspaceRequest } from '@/lib/chat';
import {
  isRequestId,
  logKindLabel,
  logTimestamp,
  type LogEvent,
  type CapturedBody,
} from '@/lib/logs';
import styles from './RequestLogs.module.css';

function BodyPreview({
  title,
  body,
}: {
  title: string;
  body: CapturedBody | null | undefined;
}) {
  return (
    <section className={styles.evidence}>
      <h3>{title}</h3>
      {!body ? (
        <p>Not captured for this event.</p>
      ) : (
        <>
          <p>
            {body.bytes !== undefined
              ? `${body.bytes} bytes`
              : 'Captured preview'}
            {body.truncated ? ' · Truncated' : ''}
          </p>
          {body.omitted ? (
            <p>Omitted: {body.omitted}</p>
          ) : (
            <pre>{body.text ?? '(empty body)'}</pre>
          )}
        </>
      )}
    </section>
  );
}

export default function LogEventDetails({
  projectId,
  eventId,
  onCorrelate,
}: {
  projectId: string;
  eventId: number;
  onCorrelate: (requestId: string) => void;
}) {
  const [event, setEvent] = useState<LogEvent | null>(null);
  const [error, setError] = useState('');
  const [attempt, setAttempt] = useState(0);
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    heading.current?.focus();
  }, []);
  useEffect(() => {
    let active = true;
    workspaceRequest<LogEvent>(projectId, `logs/${eventId}`)
      .then((value) => {
        if (active) setEvent(value);
      })
      .catch((err) => {
        if (active) setError((err as Error).message);
      });
    return () => {
      active = false;
    };
  }, [projectId, eventId, attempt]);

  return (
    <Card>
      <h2 id="event-details-heading" ref={heading} tabIndex={-1}>
        Event #{eventId}
      </h2>
      {!event && !error && <p role="status">Loading event details…</p>}
      {error && (
        <>
          <p role="alert" className={styles.warning}>
            {error} Events may have been removed by the 1,000-event retention
            limit.
          </p>
          <Button
            type="button"
            onClick={() => {
              setError('');
              setEvent(null);
              setAttempt((value) => value + 1);
            }}
          >
            Retry details
          </Button>
        </>
      )}
      {event && (
        <>
          <dl className={styles.metadata}>
            <dt>Kind</dt>
            <dd>{logKindLabel(event.kind)}</dd>
            <dt>Timestamp</dt>
            <dd>
              <time dateTime={event.timestamp}>
                {logTimestamp(event.timestamp)}
              </time>
            </dd>
            <dt>Request ID</dt>
            <dd>
              <code>{event.request_id || 'Not associated with a request'}</code>
            </dd>
            {event.method && (
              <>
                <dt>Method & path</dt>
                <dd>
                  <code>
                    {event.method} {event.path}
                  </code>
                </dd>
              </>
            )}
            {event.status_code !== undefined && (
              <>
                <dt>HTTP status</dt>
                <dd>
                  {event.status_code}
                  {event.status_code === 499
                    ? ' · Cancellation recorded; not a delivered response'
                    : ''}
                </dd>
              </>
            )}
            {event.duration_ms !== undefined && (
              <>
                <dt>Elapsed time</dt>
                <dd>{event.duration_ms} ms</dd>
              </>
            )}
            {event.response_bytes != null && (
              <>
                <dt>Response size</dt>
                <dd>{event.response_bytes} bytes</dd>
              </>
            )}
            {event.outcome && (
              <>
                <dt>Outcome</dt>
                <dd>{event.outcome}</dd>
              </>
            )}
            {event.exception_type && (
              <>
                <dt>Exception type</dt>
                <dd>
                  <code>{event.exception_type}</code>
                </dd>
              </>
            )}
          </dl>
          {event.request_id && isRequestId(event.request_id) && (
            <button
              type="button"
              className={styles.secondary}
              onClick={() => onCorrelate(event.request_id!)}
            >
              Show related request events
            </button>
          )}
          {event.frames && (
            <section className={styles.evidence}>
              <h3>Project stack frames</h3>
              {event.frames.length ? (
                <ul>
                  {event.frames.map((frame, index) => (
                    <li key={index}>
                      <code>
                        {frame.file}:{frame.line} · {frame.function}
                      </code>
                    </li>
                  ))}
                </ul>
              ) : (
                <p>No project stack frames were recorded.</p>
              )}
              <p>
                Source lines, exception messages, and local variables are not
                recorded.
              </p>
            </section>
          )}
          {event.kind === 'request' && (
            <>
              <BodyPreview title="Request body" body={event.request_body} />
              <BodyPreview title="Response body" body={event.response_body} />
            </>
          )}
          {(event.kind === 'stdout' || event.kind === 'stderr') && (
            <section className={styles.evidence}>
              <h3>Captured output</h3>
              <p>
                Process-wide output; not reliably attributable to a single
                request.
                {event.truncated ? ' Truncated to the capture limit.' : ''}
              </p>
              <pre>{event.text || '(empty output)'}</pre>
            </section>
          )}
          <p className={styles.context}>
            Capture is best-effort and may omit evidence. Redaction is not a
            guarantee that all sensitive data was removed.
          </p>
        </>
      )}
    </Card>
  );
}
