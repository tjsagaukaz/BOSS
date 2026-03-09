import type {
  ChatRequest,
  CommandCenterSnapshot,
  RunDetailsResponse,
  StreamEvent,
} from "./types";

function endpoint(baseUrl: string, path: string): string {
  return new URL(path, `${baseUrl}/`).toString();
}

async function request<T>(baseUrl: string, path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(endpoint(baseUrl, path), {
    cache: "no-store",
    ...init,
    headers: {
      Accept: "application/json",
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      ...(init?.headers || {}),
    },
  });
  if (!response.ok) {
    const body = await response.text();
    throw new Error(body || `Request failed: ${response.status}`);
  }
  return (await response.json()) as T;
}

export function postJson<T>(baseUrl: string, path: string, body: unknown): Promise<T> {
  return request<T>(baseUrl, path, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function setActiveProject(baseUrl: string, projectName: string): Promise<void> {
  await postJson(baseUrl, "/projects/active", { project_name: projectName });
}

export async function loadCommandCenter(baseUrl: string): Promise<CommandCenterSnapshot> {
  return request<CommandCenterSnapshot>(baseUrl, "/command-center");
}

export function fetchRunDetails(
  baseUrl: string,
  identifier: string | number,
  kind = "build",
): Promise<RunDetailsResponse> {
  return request<RunDetailsResponse>(
    baseUrl,
    `/runs/${encodeURIComponent(String(identifier))}?kind=${encodeURIComponent(kind)}`,
  );
}

export async function streamChat(
  baseUrl: string,
  payload: ChatRequest,
  onEvent: (event: StreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch(endpoint(baseUrl, "/chat/stream"), {
    method: "POST",
    headers: {
      Accept: "application/x-ndjson",
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
    signal,
  });

  if (!response.ok || !response.body) {
    const body = await response.text();
    throw new Error(body || `Chat request failed: ${response.status}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) {
      break;
    }
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";
    for (const rawLine of lines) {
      const line = rawLine.trim();
      if (!line) {
        continue;
      }
      onEvent(JSON.parse(line) as StreamEvent);
    }
  }

  if (buffer.trim()) {
    onEvent(JSON.parse(buffer.trim()) as StreamEvent);
  }
}
