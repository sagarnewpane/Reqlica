import { useEffect, useState } from 'react';
import { workspaceRequest } from '@/lib/chat';
export function useResource<T>(projectId: string, path: string) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let active = true;
    workspaceRequest<T>(projectId, path)
      .then((value) => {
        if (active) {
          setData(value);
          setError('');
        }
      })
      .catch((reason) => {
        if (active) setError(reason.message);
      });
    return () => {
      active = false;
    };
  }, [projectId, path, attempt]);
  async function action(route: string, method = 'POST', body?: unknown) {
    if (busy) return;
    setBusy(true);
    setError('');
    try {
      const value = await workspaceRequest<T>(projectId, route, method, body);
      setAttempt((a) => a + 1);
      return value;
    } catch (reason) {
      setError((reason as Error).message);
      throw reason;
    } finally {
      setBusy(false);
    }
  }
  return {
    data,
    setData,
    error,
    setError,
    busy,
    action,
    refresh: () => setAttempt((a) => a + 1),
  };
}
