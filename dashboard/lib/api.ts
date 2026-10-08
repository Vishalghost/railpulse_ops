import { accessToken, userManager } from "./auth";
import { API_BASE } from "./config";
import type { LiveEvent } from "./types";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = await accessToken();
  const r = await fetch(`${API_BASE}/api${path}`, {
    ...init,
    headers: { ...init.headers, Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
  });
  if (r.status === 401) {
    await (await userManager()).removeUser(); // next call starts a fresh sign-in
  }
  if (!r.ok) {
    const body = await r.json().catch(() => ({}));
    throw new ApiError(r.status, typeof body.detail === "string" ? body.detail : r.statusText);
  }
  return r.json() as Promise<T>;
}

export type FeedStatus = "connecting" | "live" | "reconnecting";

/**
 * Server-Sent Events over fetch(). EventSource can't send an Authorization header, and
 * putting the token in the query string would leak it into CloudFront/ALB access logs.
 */
export function subscribeIncidents(
  division: string,
  onEvent: (ev: LiveEvent) => void,
  onStatus: (s: FeedStatus) => void,
): () => void {
  const ctrl = new AbortController();
  let stopped = false;

  (async () => {
    while (!stopped) {
      try {
        onStatus("connecting");
        const token = await accessToken();
        const r = await fetch(`${API_BASE}/api/stream/incidents?division=${encodeURIComponent(division)}`, {
          headers: { Authorization: `Bearer ${token}`, Accept: "text/event-stream" },
          signal: ctrl.signal,
        });
        if (!r.ok || !r.body) throw new Error(`stream HTTP ${r.status}`);
        onStatus("live");
        const reader = r.body.pipeThrough(new TextDecoderStream()).getReader();
        let buf = "";
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          buf += value;
          let cut: number;
          while ((cut = buf.indexOf("\n\n")) >= 0) {
            const frame = buf.slice(0, cut);
            buf = buf.slice(cut + 2);
            const lines = frame.split("\n");
            const event = lines.find((l) => l.startsWith("event: "))?.slice(7);
            const data = lines.filter((l) => l.startsWith("data: ")).map((l) => l.slice(6)).join("\n");
            if (event === "incident" && data) onEvent(JSON.parse(data) as LiveEvent);
          }
        }
      } catch {
        if (stopped) return;
      }
      onStatus("reconnecting");
      await new Promise((res) => setTimeout(res, 5000));
    }
  })();

  return () => {
    stopped = true;
    ctrl.abort();
  };
}
