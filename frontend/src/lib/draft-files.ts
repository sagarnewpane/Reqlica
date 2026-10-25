export const MAX_DRAFT_SOURCE_BYTES = 1024 * 1024;
export type DraftFile = { path: string; content: string; revision: string };
export type DraftEditorState = {
  dirty: boolean;
  busy: boolean;
  blocked: boolean;
};

export function draftFilePathError(path: string) {
  if (
    !path ||
    path.length > 1024 ||
    path.startsWith('/') ||
    /^[a-z]:/i.test(path) ||
    /[\\\x00-\x1f\x7f]/.test(path)
  )
    return 'Use a safe project-relative file path, without control characters or backslashes.';
  const parts = path.split('/');
  if (parts.some((part) => !part || part === '.' || part === '..'))
    return 'File paths cannot contain empty, current-directory, or parent-directory segments.';
  if (
    parts.some(
      (part) =>
        [
          '.reqlica',
          '.git',
          'drafts',
          'versions',
          '__pycache__',
          'project.json',
        ].includes(part) ||
        /\.(sqlite|sqlite3|db|pyc)$|(-wal|-shm)$/.test(part),
    )
  )
    return 'Control, version, cache, and database paths cannot be edited as draft source.';
  return null;
}

export function draftFileRoute(draftId: string, path: string) {
  if (!/^[a-f0-9]{32}$/.test(draftId))
    throw new Error('Invalid draft address.');
  const invalid = draftFilePathError(path);
  if (invalid) throw new Error(invalid);
  return `drafts/${draftId}/files?path=${encodeURIComponent(path)}`;
}
