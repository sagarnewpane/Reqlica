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
import Button from '@/components/ui/Button/Button';
import Select from '@/components/ui/Select/Select';
import {
  readModelProfiles,
  subscribeModelProfiles,
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
  const [editing, setEditing] = useState(false);
  const selectedProfile = profiles.find((profile) => profile.id === selected);
  const changed =
    !!selectedProfile &&
    (name.trim() !== selectedProfile.name ||
      JSON.stringify(modelConnection(value)) !==
        JSON.stringify(modelConnection(selectedProfile)));
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
    const unsubscribe = subscribeModelProfiles(() => {
      if (active)
        try {
          setProfiles(readModelProfiles());
        } catch {
          setError('Cannot refresh app model profiles.');
        }
    });
    return () => {
      active = false;
      unsubscribe();
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
      setEditing(false);
      setError('');
      setNotice(
        'Profile saved for all projects. It uses the app API key for its provider and URL. Save defaults below only if you also want this connection for new chats.',
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
      setEditing(false);
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
          <p>
            Reuse a provider, model, URL and reasoning level across projects.
            API keys are shared by all projects for each provider and URL.
          </p>
        </div>
        <Button
          type="button"
          variant="secondary"
          disabled={disabled}
          onClick={() => {
            setSelected('');
            setName('');
            setEditing(true);
            setError('');
            setNotice(
              'This creates a profile from the connection above. Give it a name, then create it.',
            );
          }}
        >
          <Plus size={14} />
          Save as new profile
        </Button>
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
              setEditing(false);
              setError('');
              setNotice(
                profile
                  ? 'Connection loaded above. Save defaults below to use it for new chats, or select it directly in chat.'
                  : '',
              );
              if (profile) onApply(modelConnection(profile));
            }}
          >
            <option value="">Current connection (not a profile)</option>
            {profiles.map((profile) => (
              <option key={profile.id} value={profile.id}>
                {profile.name} · {profile.model}
              </option>
            ))}
          </Select>
        </div>
        {editing && (
          <div>
            <label htmlFor="profile-name">Profile name</label>
            <Input
              id="profile-name"
              disabled={disabled}
              value={name}
              maxLength={80}
              autoFocus
              onKeyDown={(event) => {
                if (event.key === 'Enter') {
                  event.preventDefault();
                  if (!disabled && name.trim() && value.model.trim()) save();
                }
              }}
              onChange={(event) => setName(event.target.value)}
              placeholder="e.g. Fast coding, Local assistant"
            />
          </div>
        )}
      </div>
      {editing && (
        <p className={styles.connection}>
          Saving current connection:{' '}
          <strong>
            {value.provider} · {value.model || 'Select a model above first'}
          </strong>
          {value.baseUrl ? ` · ${value.baseUrl}` : ''}
        </p>
      )}
      {selected && !editing && (
        <p>
          {changed
            ? 'The connection above differs from this saved profile.'
            : 'This profile matches the connection above.'}
        </p>
      )}
      <div className={styles.actions}>
        {editing ? (
          <>
            <Button
              type="button"
              variant="secondary"
              disabled={disabled || !name.trim() || !value.model.trim()}
              onClick={() => save()}
            >
              <Save size={14} />
              {selected ? 'Update profile' : 'Create profile'}
            </Button>
            <Button
              type="button"
              variant="ghost"
              onClick={() => {
                setEditing(false);
                setName(selectedProfile?.name || '');
                setError('');
                setNotice('');
              }}
            >
              Cancel
            </Button>
          </>
        ) : (
          selected && (
            <Button
              type="button"
              variant="secondary"
              disabled={disabled}
              onClick={() => setEditing(true)}
            >
              Edit / update profile
            </Button>
          )
        )}
        {selected && (
          <>
            <Button
              type="button"
              variant="ghost"
              disabled={disabled || !selected}
              onClick={() => save(true)}
            >
              <Copy size={14} />
              Duplicate current connection
            </Button>
            <AlertDialog>
              <AlertDialogTrigger asChild>
                <Button
                  type="button"
                  variant="ghost"
                  disabled={disabled || !selected}
                >
                  <Trash2 size={14} />
                  Delete
                </Button>
              </AlertDialogTrigger>
              <AlertDialogContent>
                <AlertDialogHeader>
                  <AlertDialogTitle>
                    Delete this model profile?
                  </AlertDialogTitle>
                  <AlertDialogDescription>
                    Remove “
                    {profiles.find((profile) => profile.id === selected)?.name}”
                    from app-wide saved profiles. Existing conversations and
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
          </>
        )}
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
        {profiles.length === 0
          ? 'No profiles yet. Choose a connection above, then save it as a profile.'
          : `${profiles.length} of 30 profiles saved in this browser. Saving a profile does not change your defaults.`}
      </p>
    </div>
  );
}
