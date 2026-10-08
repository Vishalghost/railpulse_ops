import type { Severity } from "./types";

/**
 * Severity is drawn as a railway signal aspect. Colours were validated for colour-blind
 * separation against the panel surface (dataviz validator); P3 uses an advisory indigo
 * rather than a second yellow because amber vs yellow failed even for normal vision.
 * The lamp count (P3 = two lamps) and the always-visible "P#" label are the secondary
 * encodings, so severity never relies on colour alone.
 */
export const ASPECT: Record<Severity, { color: string; lamps: 1 | 2; label: string; action: string }> = {
  1: { color: "#C42B1C", lamps: 1, label: "P1", action: "Protect the line and inspect now" },
  2: { color: "#DB7C00", lamps: 1, label: "P2", action: "Inspect this shift" },
  3: { color: "#5A54A8", lamps: 2, label: "P3", action: "Inspect within 24 hours" },
  4: { color: "#6B7780", lamps: 1, label: "P4", action: "Advisory" },
};
export const CLEAR = "#2B8A4E";

export const TYPE_LABEL: Record<string, string> = {
  CRACK_SUSPECTED: "Suspected rail crack",
  IMPACT_ANOMALY: "Wheel impact on rail",
  VIBRATION_ANOMALY: "Abnormal vibration",
  BUCKLING_RISK: "Buckling risk",
  COLD_STRESS: "Cold-stress window",
  SWITCH_DEGRADED: "Point machine degrading",
  SWITCH_FAILURE: "Point machine failed",
  SENSOR_FAULT: "Sensor fault",
  SEGMENT_SILENT: "Segment gone silent",
  SENSOR_SILENT: "Sensor not reporting",
  LOW_BATTERY: "Sensor battery low",
};

export const DIVISION_NAME: Record<string, string> = { DLI: "Delhi", MAS: "Chennai", BB: "Mumbai" };

export const ROLE_LABEL: Record<string, string> = {
  GANGMAN: "Gangman",
  SSE: "Section Engineer",
  DEN: "Divisional Engineer",
  DASHBOARD: "Control room",
};

export function km(v: number | null | undefined): string {
  return v == null ? "—" : `km ${v.toFixed(1)}`;
}

export function ago(ms: number, now = Date.now()): string {
  const s = Math.max(0, Math.round((now - ms) / 1000));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

export function clock(ms: number): string {
  return new Date(ms).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", hour12: false });
}
