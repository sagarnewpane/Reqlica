import { useId, useState } from 'react';
import Button from '@/components/ui/Button/Button';
import Input from '@/components/ui/Input/Input';
import Select from '@/components/ui/Select/Select';
import { newRequestId } from '@/lib/ids';
import { useResource } from './use-resource';
import s from './SavedItems.module.css';
export type LibraryItem = {
  id: string;
  kind: string;
  name: string;
  revision: string;
  data: unknown;
};
export default function SavedItems({
  projectId,
  kind,
  data,
  onLoad,
  disabled = false,
}: {
  projectId: string;
  kind: 'request' | 'chain' | 'dataset';
  data: unknown;
  onLoad: (data: unknown) => void;
  disabled?: boolean;
}) {
  const resource = useResource<{ items: LibraryItem[] }>(projectId, 'library');
  const [selected, setSelected] = useState('');
  const [name, setName] = useState('');
  const [notice, setNotice] = useState('');
  const id = useId();
  const item = resource.data?.items.find((i) => i.id === selected);
  async function save() {
    try {
      await resource.action(`library/${newRequestId()}`, 'PUT', {
        kind,
        name: name.trim() || `${kind} ${new Date().toLocaleString()}`,
        data,
      });
      setNotice('Saved to project library.');
    } catch {}
  }
  return (
    <details className={s.panel}>
      <summary>
        Saved{' '}
        {kind === 'dataset'
          ? 'datasets'
          : kind === 'chain'
            ? 'chains'
            : 'requests'}
      </summary>
      <div className={s.grid}>
        <div>
          <label htmlFor={`${id}-item`}>Saved item</label>
          <Select
            id={`${id}-item`}
            value={selected}
            disabled={disabled || resource.busy}
            onValueChange={setSelected}
          >
            <option value="">Select saved item</option>
            {resource.data?.items
              .filter((i) => i.kind === kind)
              .map((i) => (
                <option key={i.id} value={i.id}>
                  {i.name}
                </option>
              ))}
          </Select>
        </div>
        <div>
          <label htmlFor={`${id}-name`}>Save as</label>
          <Input
            id={`${id}-name`}
            value={name}
            disabled={disabled || resource.busy}
            onChange={(e) => setName(e.target.value)}
            maxLength={100}
            placeholder={`${kind} ${new Date().toLocaleDateString()}`}
          />
        </div>
      </div>
      <div className={s.row}>
        <Button
          type="button"
          variant="secondary"
          disabled={disabled || resource.busy || !item}
          onClick={() => {
            if (item)
              try {
                onLoad(item.data);
                setNotice('Loaded into editor.');
              } catch (reason) {
                resource.setError((reason as Error).message);
              }
          }}
        >
          Load
        </Button>
        <Button
          type="button"
          variant="secondary"
          disabled={disabled || resource.busy}
          onClick={() => void save()}
        >
          Save new copy
        </Button>
        <Button
          type="button"
          variant="secondary"
          disabled={disabled || resource.busy || !item}
          onClick={() => {
            if (item)
              void resource
                .action(`library/${item.id}`, 'DELETE', {
                  expected_revision: item.revision,
                })
                .then(() => {
                  setSelected('');
                  setNotice('Removed from library.');
                })
                .catch(() => {});
          }}
        >
          Remove saved item
        </Button>
        <Button type="button" variant="secondary" onClick={resource.refresh}>
          Refresh
        </Button>
      </div>
      {resource.error && (
        <p className={s.error} role="alert">
          {resource.error}
        </p>
      )}
      {notice && (
        <p className={s.notice} role="status">
          {notice}
        </p>
      )}
    </details>
  );
}
