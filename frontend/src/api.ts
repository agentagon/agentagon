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
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  const bytes = new Uint8Array(16);
  if (globalThis.crypto?.getRandomValues) globalThis.crypto.getRandomValues(bytes);
  else for (let index = 0; index < bytes.length; index += 1) bytes[index] = Math.floor(Math.random() * 256);
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const value = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
  return `${value.slice(0, 8)}-${value.slice(8, 12)}-${value.slice(12, 16)}-${value.slice(16, 20)}-${value.slice(20)}`;
}
