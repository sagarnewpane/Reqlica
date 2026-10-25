export type DraftCheck = {
  status: string;
  revision: string;
  tests_run?: number;
  tests_passed?: number;
  handler_coverage?: { status: string; missing?: string[]; executed?: string[] };
};

export type Draft = {
  id: string;
  name: string;
  created_at?: string | null;
  revision: string;
  base_version: string;
  error?: string;
  validation?: DraftCheck | null;
  test?: DraftCheck | null;
  approval?: { revision: string } | null;
  files?: string[];
  changes?: Record<string, string[]>;
  base_is_current?: boolean;
  published_matches?: boolean;
  required_handlers?: string[];
  endpoints?: { method: string; path: string; handler: string }[];
};

export function draftCreatedLabel(draft: Pick<Draft, 'created_at'>): string {
  if (!draft.created_at) return '';
  const date = new Date(draft.created_at);
  if (!Number.isFinite(date.getTime())) return '';
  return new Intl.DateTimeFormat(undefined, {
    year: 'numeric', month: 'short', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(date);
}

export function draftLabel(draft: Pick<Draft, 'id' | 'name' | 'created_at'>): string {
  const created = draftCreatedLabel(draft);
  return `${draft.name || 'Draft'}${created ? ` · ${created}` : ''} · #${draft.id.slice(0, 8)}`;
}

export function orderedDrafts(drafts: Draft[]): Draft[] {
  const created = (draft: Draft) => Date.parse(draft.created_at || '') || 0;
  return [...drafts].sort((a, b) => created(b) - created(a) || a.id.localeCompare(b.id));
}

/** UI hints only: the backend rechecks code, dependencies, and data readiness. */
export function draftPublicationState(
  draft: Draft,
  activeVersion: string | null | undefined,
) {
  const validationPassed =
    draft.validation?.status === 'passed' &&
    draft.validation.revision === draft.revision;
  const testsPassed =
    draft.test?.status === 'passed' &&
    draft.test.revision === draft.revision &&
    (!draft.required_handlers?.length ||
      draft.required_handlers.every((handler) =>
        draft.test?.handler_coverage?.executed?.includes(handler))) &&
    (draft.test.tests_run ?? 0) > 0 &&
    (draft.test.tests_passed ?? 0) > 0;
  const baseIsCurrent =
    typeof activeVersion === 'string' && draft.base_version === activeVersion;
  const checksPassed = validationPassed && testsPassed;
  const approved = checksPassed && draft.approval?.revision === draft.revision;

  return {
    validationPassed,
    testsPassed,
    baseIsCurrent,
    approved,
    canApprove: baseIsCurrent && checksPassed && !approved && !draft.published_matches,
    canActivate: baseIsCurrent && approved && !draft.published_matches,
  };
}
