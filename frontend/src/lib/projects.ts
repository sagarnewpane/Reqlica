export type Endpoint = {
  method: string;
  path: string;
  handler?: string;
};

export type Project = {
  id: string;
  name: string | null;
  directory: string | null;
  status: string;
  endpoints: Endpoint[];
  error: string | null;
  active_version?: string | null;
  pid?: number | null;
  environment_revision?: string | null;
};

export async function projectRequest(init?: RequestInit) {
  const response = await fetch('/api/projects', init);
  const data = await response.json();
  if (!response.ok)
    throw new Error(
      data.error || 'Unable to complete the request. Please try again.',
    );
  return data;
}
