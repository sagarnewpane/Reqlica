import { useEffect, useRef, useState } from 'react';
import { workspaceRequest } from '@/lib/chat';
import type { Project } from '@/lib/projects';
import {
  isWorkerProject,
  workerCapabilities,
  type WorkerAction,
} from '@/lib/workers';

type Options = {
  projectId: string;
  project?: Project;
  onProjectChange?: (project: Project) => void;
};

export function useWorkerControls({
  projectId,
  project: providedProject,
  onProjectChange,
}: Options) {
  const [localProject, setLocalProject] = useState<Project | null>(null);
  const [verified, setVerified] = useState(
    isWorkerProject(providedProject, projectId),
  );
  const [busy, setBusy] = useState<'refresh' | WorkerAction | null>(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [target, setTarget] = useState<{
    action: WorkerAction;
    projectId: string;
    name: string;
    status: string;
  } | null>(null);
  const alive = useRef(true);
  const locked = useRef(false);
  const readSequence = useRef(0);
  const project = providedProject || localProject;

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, [projectId]);

  useEffect(() => {
    if (providedProject) return;
    let active = true;
    const sequence = ++readSequence.current;
    workspaceRequest<unknown>(projectId, 'endpoints')
      .then((value) => {
        if (!active || sequence !== readSequence.current) return;
        if (!isWorkerProject(value, projectId))
          throw new Error(
            'The runtime returned an unrecognized project status. Refresh status to inspect it again.',
          );
        setLocalProject(value);
        setVerified(true);
        onProjectChange?.(value);
      })
      .catch((err) => {
        if (active && sequence === readSequence.current) {
          setError((err as Error).message);
          setVerified(false);
        }
      });
    return () => {
      active = false;
    };
  }, [projectId, providedProject, onProjectChange]);

  function accept(value: unknown): Project {
    if (!isWorkerProject(value, projectId))
      throw new Error(
        'The runtime returned an unrecognized project response. Refresh status before continuing.',
      );
    if (alive.current) {
      setLocalProject(value);
      setVerified(true);
      onProjectChange?.(value);
    }
    return value;
  }

  async function refresh() {
    if (locked.current) return;
    const sequence = ++readSequence.current;
    locked.current = true;
    setBusy('refresh');
    setVerified(false);
    setTarget(null);
    setError('');
    setNotice('');
    try {
      const value = await workspaceRequest<unknown>(projectId, 'endpoints');
      if (sequence === readSequence.current) accept(value);
    } catch (err) {
      if (alive.current) {
        setVerified(false);
        setError((err as Error).message);
      }
    } finally {
      locked.current = false;
      if (alive.current) setBusy(null);
    }
  }

  function open(action: WorkerAction) {
    if (locked.current || !verified || !project) return;
    const allowed = workerCapabilities(project.status);
    if (action === 'start' ? !allowed.canStart : !allowed.canStop) return;
    setError('');
    setNotice('');
    setTarget({
      action,
      projectId,
      name: project.name || projectId,
      status: project.status,
    });
  }

  function close() {
    if (!locked.current) setTarget(null);
  }

  async function confirm() {
    if (
      !target ||
      locked.current ||
      !verified ||
      target.projectId !== projectId ||
      !project
    )
      return;
    const allowed = workerCapabilities(project.status);
    if (
      target.status !== project.status ||
      (target.action === 'start' ? !allowed.canStart : !allowed.canStop)
    ) {
      setError(
        'The reported worker state changed. Cancel and refresh status before continuing.',
      );
      setVerified(false);
      return;
    }
    locked.current = true;
    setBusy(target.action);
    setError('');
    setNotice('');
    try {
      const value = await workspaceRequest<unknown>(
        projectId,
        target.action,
        'POST',
        { confirm: true },
      );
      if (
        !isWorkerProject(value, projectId) ||
        value.status !== (target.action === 'start' ? 'running' : 'stopped')
      )
        throw new Error(
          'The requested worker state was not confirmed. Refresh status before another action.',
        );
      accept(value);
      if (alive.current) {
        setTarget(null);
        setNotice(
          target.action === 'start'
            ? 'API started.'
            : 'API stopped. Your files and data are kept.',
        );
      }
    } catch (err) {
      if (alive.current) {
        setVerified(false);
        setError(
          `${(err as Error).message} Refresh status before taking another worker action; the operation may have completed.`,
        );
      }
    } finally {
      locked.current = false;
      if (alive.current) setBusy(null);
    }
  }

  return {
    project,
    verified,
    busy,
    error,
    notice,
    target,
    refresh,
    open,
    close,
    confirm,
  };
}
