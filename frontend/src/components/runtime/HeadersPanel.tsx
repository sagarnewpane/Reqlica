import { useEffect, useState } from 'react';
import Link from 'next/link';
import { Plus, Trash2 } from 'lucide-react';
import Input from '@/components/ui/Input/Input';
import Button from '@/components/ui/Button/Button';
import Switch from '@/components/ui/Switch/Switch';
import {
  getRequestHeaders,
  setRequestHeaders,
  type RequestHeader,
} from '@/lib/request-headers';
import styles from './RuntimePanels.module.css';

export default function HeadersPanel({ projectId }: { projectId: string }) {
  const [rows, setRows] = useState<RequestHeader[]>([]);
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');

  useEffect(() => {
    Promise.resolve().then(() => setRows(getRequestHeaders(projectId)));
  }, [projectId]);

  function update(
    index: number,
    field: keyof RequestHeader,
    value: string | boolean,
  ) {
    setRows((current) =>
      current.map((row, i) => (i === index ? { ...row, [field]: value } : row)),
    );
    setNotice('');
  }

  function save() {
    try {
      setRequestHeaders(projectId, rows);
      setNotice(
        'Headers applied to this project’s request tester for this session.',
      );
      setError('');
    } catch (err) {
      setError((err as Error).message);
    }
  }

  return (
    <section className={styles.panel}>
      <header className={styles.heading}>
        <div>
          <h2>Request headers</h2>
        </div>
      </header>
      <div className={styles.info}>
        Request tester defaults. Cleared on browser refresh.
      </div>
      {notice && (
        <p role="status" className={styles.success}>
          {notice}
        </p>
      )}
      {error && (
        <p role="alert" className={styles.error}>
          {error}
        </p>
      )}
      {!rows.length && (
        <p className={styles.empty}>
          No default headers. Add Authorization, Accept, or your own test
          headers.
        </p>
      )}
      {rows.map((row, index) => (
        <div key={index} className={styles.headerRow}>
          <Switch
            aria-label={`Enable header ${index + 1}`}
            checked={row.enabled}
            onCheckedChange={(next) => update(index, 'enabled', next)}
          />
          <Input
            aria-label={`Header ${index + 1} name`}
            placeholder="Header name"
            value={row.name}
            onChange={(event) => update(index, 'name', event.target.value)}
          />
          <Input
            type="password"
            autoComplete="off"
            aria-label={`Header ${index + 1} value`}
            placeholder="Value"
            value={row.value}
            onChange={(event) => update(index, 'value', event.target.value)}
          />
          <button
            className={styles.icon}
            aria-label={`Remove header ${index + 1}`}
            onClick={() => {
              setRows((current) => current.filter((_, i) => i !== index));
              setNotice('');
            }}
          >
            <Trash2 size={16} />
          </button>
        </div>
      ))}
      <div className={styles.actions}>
        <button
          className={styles.ghost}
          onClick={() =>
            setRows((current) => [
              ...current,
              { name: '', value: '', enabled: true },
            ])
          }
        >
          <Plus size={16} />
          Add header
        </button>
        <Button onClick={save}>Apply headers</Button>
      </div>
      <Link
        className={styles.link}
        href={{ pathname: '/request-tester', query: { projectId } }}
      >
        Open request tester →
      </Link>
    </section>
  );
}
