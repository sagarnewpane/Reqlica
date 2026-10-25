import { useEffect, useState } from 'react';
import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogCancel,
} from '@/components/ui/shadcn/alert-dialog';
import Button from '@/components/ui/Button/Button';
import Input from '@/components/ui/Input/Input';
import { workspaceRequest } from '@/lib/chat';
type Preview = {
  directory: string | null;
  name: string | null;
  error: string | null;
};
export default function DeleteProject({
  id,
  name,
  open,
  onOpenChange,
  onDeleted,
}: {
  id: string;
  name: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onDeleted: () => void;
}) {
  const [files, setFiles] = useState(false);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [typed, setTyped] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(false);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (!open) return;
    let active = true;
    Promise.resolve().then(async () => {
      if (!active) return;
      setLoading(true);
      setPreview(null);
      setError('');
      try {
        const data = await workspaceRequest<Preview>(
          id,
          files ? 'deletion-files' : 'deletion',
        );
        if (active) setPreview(data);
      } catch (err) {
        if (active) setError((err as Error).message);
      } finally {
        if (active) setLoading(false);
      }
    });
    return () => {
      active = false;
    };
  }, [id, open, files, attempt]);
  async function remove() {
    if (!preview || busy || typed !== name) return;
    setBusy(true);
    setError('');
    try {
      await workspaceRequest(id, 'registration', 'DELETE', {
        confirm: true,
        delete_files: files,
        ...(files ? { expected_directory: preview.directory } : {}),
      });
      onDeleted();
      onOpenChange(false);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <AlertDialog
      open={open}
      onOpenChange={(next) => {
        if (busy) return;
        onOpenChange(next);
        if (!next) {
          setFiles(false);
          setTyped('');
        }
      }}
    >
      <AlertDialogContent
        onEscapeKeyDown={(event) => {
          if (busy) event.preventDefault();
        }}
      >
        <AlertDialogHeader>
          <AlertDialogTitle>Remove {name}?</AlertDialogTitle>
          <AlertDialogDescription>
            This stops its worker and removes it from your project list. Files
            are kept unless you explicitly choose to delete them.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <label className="flex gap-3 items-start text-sm">
          <input
            type="checkbox"
            checked={files}
            disabled={busy}
            onChange={(event) => {
              setFiles(event.target.checked);
              setPreview(null);
            }}
          />
          Permanently delete the project folder, data, drafts, and saved
          conversations too
        </label>
        {loading ? (
          <p role="status" className="text-sm">
            Checking project location…
          </p>
        ) : (
          preview && (
            <div className="rounded-lg bg-muted p-3 text-xs break-all">
              <strong>
                {files
                  ? 'Folder to permanently delete'
                  : 'Files will remain at'}
              </strong>
              <p>{preview.directory || 'Location unavailable'}</p>
              {preview.error && <p>{preview.error}</p>}
            </div>
          )
        )}
        {files && (
          <p className="text-sm text-destructive">
            File deletion cannot be undone.
          </p>
        )}
        <label className="text-sm" htmlFor="delete-project-name">
          Type “{name}” to confirm
        </label>
        <Input
          id="delete-project-name"
          value={typed}
          disabled={busy}
          onChange={(event) => setTyped(event.target.value)}
          autoComplete="off"
        />
        {error && (
          <p role="alert" className="text-sm text-destructive">
            {error}{' '}
            <button
              type="button"
              onClick={() => setAttempt((count) => count + 1)}
            >
              Reload preview
            </button>
          </p>
        )}
        <AlertDialogFooter>
          <AlertDialogCancel disabled={busy}>Cancel</AlertDialogCancel>
          <Button
            variant="danger"
            disabled={busy || loading || !preview || typed !== name}
            onClick={() => void remove()}
          >
            {busy
              ? 'Removing…'
              : files
                ? 'Delete project and files'
                : 'Remove project, keep files'}
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
