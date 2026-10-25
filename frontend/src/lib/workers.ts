import type { Project } from './projects';

export type WorkerAction = 'start' | 'stop';
export const WORKER_STATUSES = ['idle', 'running', 'stopped', 'error'] as const;

export function workerCapabilities(status: string | undefined) {
  return {
    canStart: status === 'idle' || status === 'stopped' || status === 'error',
    // Stopping an idle worker explicitly disables lazy startup for this session.
    canStop: status === 'running' || status === 'idle' || status === 'error',
  };
}

export function workerStatusDescription(status: string | undefined) {
  switch (status) {
    case 'running':
      return 'The runtime reports a running worker serving its loaded API.';
    case 'idle':
      return 'No worker is running. A live API request can start it automatically.';
    case 'stopped':
      return 'Explicitly stopped for this gateway session. Live requests return 503 until an explicit start. Restarting the gateway resets this stopped state.';
    case 'error':
      return 'The runtime reported a project or worker error. Review its diagnostics before starting again.';
    default:
      return 'Worker status is unverified. Refresh status before taking an action.';
  }
}

export function isWorkerProject(
  value: unknown,
  projectId: string,
): value is Project {
  if (!value || typeof value !== 'object') return false;
  const project = value as Partial<Project>;
  return (
    project.id === projectId &&
    typeof project.status === 'string' &&
    WORKER_STATUSES.some((status) => status === project.status) &&
    Array.isArray(project.endpoints)
  );
}
