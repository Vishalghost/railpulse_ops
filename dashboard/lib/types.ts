export type Severity = 1 | 2 | 3 | 4;
export type IncidentStatus = "OPEN" | "ACKNOWLEDGED" | "RESOLVED";

export interface Finding {
  sensor_id: string;
  ts: number;
  severity: number;
  metric: string;
  observed: number;
  expected: number | null;
  detail: string;
}

export interface Incident {
  incident_id: string;
  type: string;
  status: IncidentStatus;
  severity: Severity;
  segment_id: string;
  division: string;
  km_from?: number | null;
  line_code?: string | null;
  line_name?: string | null;
  section?: string | null;
  first_seen: number;
  last_seen: number;
  occurrences: number;
  sensor_ids?: string[];
  first_finding?: Finding;
  last_finding?: Finding;
  escalation_level?: number;
  escalation_role?: string;
  escalation_state?: string;
  last_notified_at?: number;
  acknowledged_by?: string;
  acknowledged_role?: string;
  acknowledged_at?: number;
  resolved_by?: string;
  resolved_at?: number;
  resolution_note?: string;
}

export interface NotifyResult {
  contact_id: string;
  lang: string;
  channel: string;
  status: string;
}

export interface TimelineEntry {
  level: number;
  role: string;
  at: number;
  awaiting_ack: boolean;
  results: NotifyResult[];
}

export interface MapSegment {
  segment_id: string;
  division: string; // added client-side: the map endpoint is per division
  line_code: string;
  line_name: string;
  line_seq: number;
  from_station: string;
  to_station: string;
  km_from: number;
  km_to: number;
  from_lat: number;
  from_lon: number;
  to_lat: number;
  to_lon: number;
  has_switch: boolean;
  worst_severity: Severity | null;
  worst_incident: string | null;
}

/** One running line: its block sections in order, and the worst aspect on any of them. */
export interface LineView {
  key: string; // division + line code; line codes are only unique within a division
  division: string;
  code: string;
  name: string;
  segments: MapSegment[];
  worst: Severity | null;
}

export interface SegmentMeta {
  segment_id: string;
  division: string;
  km_from?: number;
  km_to?: number;
  line_name?: string;
  from_station?: string;
  to_station?: string;
  stress_free_temp_c?: number;
}

export interface Me {
  username: string;
  roles: string[];
  divisions: string[];
  canResolve: boolean;
}

export interface TelemetrySensor {
  sensor_id: string;
  type: "VIB" | "TMP" | "UTS" | "SWT";
  points: { ts: number; metrics: Record<string, number | boolean> }[];
}

export interface SensorHealth {
  checked_at: number;
  total: number;
  reporting: number;
  silent: string[];
  not_reporting: string[];
}

export interface LiveEvent {
  kind: "created" | "updated" | "closed";
  incident: Incident;
}
