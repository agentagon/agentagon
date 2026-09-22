let token = "";

export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

export async function bootstrap() {
  if (token) return token;
  const response = await fetch("/api/session", {
    headers: { "X-Agentagon-Bootstrap": "1" },
    mode: "same-origin",
    credentials: "same-origin",
  });
  const body = (await response.json()) as { token?: string; error?: string };
  if (!response.ok || !body.token) throw new ApiError(body.error || "Unable to open Agentagon.", response.status);
  token = body.token;
  return token;
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  if (path !== "/api/session") await bootstrap();
  const headers = new Headers(init.headers);
  if (token) headers.set("X-Agentagon-Token", token);
  if (init.body) headers.set("Content-Type", "application/json");
  const response = await fetch(path, {
    mode: "same-origin",
    credentials: "same-origin",
    ...init,
    headers,
  });
  const body = (await response.json()) as T & { error?: string };
  if (!response.ok) throw new ApiError(body.error || "The operation could not be completed.", response.status);
  return body;
}

export function post<T>(path: string, body: unknown) {
  return api<T>(path, { method: "POST", body: JSON.stringify(body) });
}

export function remove<T>(path: string) {
  return api<T>(path, { method: "DELETE", body: JSON.stringify({ confirmed: true }) });
}

export function projectPath(projectId: string, resource: string) {
  return `/api/projects/${encodeURIComponent(projectId)}${resource}`;
}

export function operationId() {
  return globalThis.crypto.randomUUID();
}
