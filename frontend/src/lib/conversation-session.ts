/** Browser recovery journal. The backend owns history and draft progress. */
export type PendingSubmission = {
  path: string;
  method: string;
  body: unknown;
  runId: string;
};
export type ConversationSession = {
  selectedId: string | null;
  message: string;
  pending: PendingSubmission | null;
};
const key = (projectId: string) => `reqlica:conversation:${projectId}`;
export function readConversationSession(
  projectId: string,
): ConversationSession | null {
  try {
    const value = JSON.parse(sessionStorage.getItem(key(projectId)) || 'null');
    if (
      !value ||
      typeof value.message !== 'string' ||
      !(
        value.selectedId === null ||
        (typeof value.selectedId === 'string' &&
          /^[a-f0-9]{32}$/.test(value.selectedId))
      )
    )
      return null;
    const pending = value.pending;
    if (
      pending &&
      (!/^[a-f0-9]{32}$/.test(pending.runId) ||
        !(
          (pending.method === 'PUT' &&
            pending.path === `agent-runs/${pending.runId}`) ||
          (pending.method === 'POST' &&
            pending.path === `agent-runs/${pending.runId}/replies`)
        ) ||
        typeof pending.body?.message !== 'string')
    )
      return null;
    return {
      selectedId: value.selectedId,
      message: value.message,
      pending: pending || null,
    };
  } catch {
    return null;
  }
}
export function saveConversationSession(
  projectId: string,
  value: ConversationSession,
) {
  try {
    sessionStorage.setItem(key(projectId), JSON.stringify(value));
  } catch {
    // Storage can be disabled. Server checkpoints still retain acknowledged work.
  }
}
