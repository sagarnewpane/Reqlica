import { useEffect, useState } from 'react';
import { Plus, Copy, Trash2, Save } from 'lucide-react';
import {
  AlertDialog,
  AlertDialogTrigger,
  AlertDialogContent,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogCancel,
  AlertDialogAction,
} from '@/components/ui/shadcn/alert-dialog';
import Input from '@/components/ui/Input/Input';
import Select from '@/components/ui/Select/Select';
import {
  readModelProfiles,
  saveModelProfile,
  deleteModelProfile,
  modelConnection,
  type ModelConnection,
  type ModelProfile,
} from '@/lib/model-profiles';
import { newUuid } from '@/lib/ids';
import styles from './ModelProfiles.module.css';
export default function ModelProfiles({
  value,
  onApply,
  disabled,
}: {
  value: ModelConnection;
  onApply: (profile: ModelConnection) => void;
  disabled: boolean;
}) {
  const [profiles, setProfiles] = useState<ModelProfile[]>([]);
  const [selected, setSelected] = useState('');
  const [name, setName] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  useEffect(() => {
    let active = true;
    Promise.resolve().then(() => {
      if (!active) return;
      try {
        setProfiles(readModelProfiles());
      } catch {
        setError('Browser storage is unavailable.');
      }
    });
    return () => {
      active = false;
    };
  }, []);
  function save(copy = false) {
    try {
      let nextName = name.trim();
      if (copy) {
        const base = nextName || 'Model profile';
        nextName = `${base} copy`;
        let count = 2;
        while (
          profiles.some(
            (profile) => profile.name.toLowerCase() === nextName.toLowerCase(),
          )
        )
          nextName = `${base} copy ${count++}`;
      }
      const id = copy || !selected ? newUuid() : selected;
      const next = saveModelProfile({
        id,
        name: nextName,
        ...modelConnection(value),
      });
      setProfiles(next);
      setSelected(id);
      setName(nextName);
      setError('');
      setNotice(
        'Profile saved in this browser. Save changes below to make these settings the default for new chats.',
      );
    } catch (err) {
      setError((err as Error).message);
      setNotice('');
    }
  }
  function remove() {
    try {
      setProfiles(deleteModelProfile(selected));
      setSelected('');
      setName('');
      setError('');
      setNotice(
        'Profile deleted. Existing conversations and saved defaults are unchanged.',
      );
    } catch {
      setError(
        'Could not delete the profile. Check browser storage permissions.',
      );
    }
  }
  return (
    <div className={styles.box}>
      <div className={styles.top}>
        <div>
          <h3>Model profiles</h3>
        </div>
        <button
          type="button"
          disabled={disabled}
          className={styles.action}
          onClick={() => {
            setSelected('');
            setName('');
            setError('');
            setNotice(
              'Name your new profile, choose its connection below, then save profile.',
            );
          }}
        >
          <Plus size={14} />
          New profile
        </button>
      </div>
      <div className={styles.grid}>
        <div>
          <label htmlFor="saved-profile">Saved profiles</label>
          <Select
            id="saved-profile"
            disabled={disabled}
            value={selected}
            onValueChange={(nextValue) => {
              const profile = profiles.find((item) => item.id === nextValue);
              setSelected(profile?.id || '');
              setName(profile?.name || '');
              setError('');
              setNotice('');
              if (profile) onApply(modelConnection(profile));
            }}
          >
            <option value="">Custom / new profile</option>
            {profiles.map((profile) => (
              <option key={profile.id} value={profile.id}>
                {profile.name} · {profile.model}
              </option>
            ))}
          </Select>
        </div>
        <div>
          <label htmlFor="profile-name">Profile name</label>
          <Input
            id="profile-name"
            disabled={disabled}
            value={name}
            maxLength={80}
            onChange={(event) => setName(event.target.value)}
            placeholder="e.g. Fast coding, Local assistant"
          />
        </div>
      </div>
      <div className={styles.actions}>
        <button
          type="button"
          className={styles.primary}
          disabled={disabled || !name.trim() || !value.model.trim()}
          onClick={() => save()}
        >
          <Save size={14} />
          {selected ? 'Update profile' : 'Save profile'}
        </button>
        <button
          type="button"
          className={styles.action}
          disabled={disabled || !selected}
          onClick={() => save(true)}
        >
          <Copy size={14} />
          Duplicate
        </button>
        <AlertDialog>
          <AlertDialogTrigger asChild>
            <button
              type="button"
              className={styles.action}
              disabled={disabled || !selected}
            >
              <Trash2 size={14} />
              Delete
            </button>
          </AlertDialogTrigger>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>Delete this model profile?</AlertDialogTitle>
              <AlertDialogDescription>
                Remove “
                {profiles.find((profile) => profile.id === selected)?.name}”
                from saved profiles in this browser. Existing conversations and
                saved defaults will remain available.
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel>Keep profile</AlertDialogCancel>
              <AlertDialogAction variant="danger" onClick={remove}>
                Delete profile
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      </div>
      {error && (
        <p className={styles.error} role="alert">
          {error}
        </p>
      )}
      {notice && (
        <p className={styles.notice} role="status">
          {notice}
        </p>
      )}
      <p className={styles.caption}>
        Shared across projects in this browser. No API keys are saved.
      </p>
    </div>
  );
}
