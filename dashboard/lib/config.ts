export interface AppConfig {
  region: string;
  userPoolId: string;
  clientId: string;
  cognitoDomain: string;
  mapStyleUrl: string | null;
}

/** Empty in AWS (same origin behind CloudFront). Set to http://localhost:8000 for `next dev`. */
export const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "";

let cached: Promise<AppConfig> | null = null;

/** Runtime config from the API, so a single static build runs in every environment. */
export function loadConfig(): Promise<AppConfig> {
  cached ??= fetch(`${API_BASE}/api/config`).then((r) => {
    if (!r.ok) throw new Error(`Could not load configuration (HTTP ${r.status})`);
    return r.json() as Promise<AppConfig>;
  });
  return cached;
}
