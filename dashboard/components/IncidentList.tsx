import Link from "next/link";
import { Lamp } from "./Lamp";
import { ago, ASPECT, DIVISION_NAME, km, ROLE_LABEL, TYPE_LABEL } from "@/lib/vocab";
import type { Incident } from "@/lib/types";

export function IncidentList({ incidents, now, scopeName }: { incidents: Incident[]; now: number; scopeName: string }) {
  if (incidents.length === 0) {
    return (
      <div className="empty">
        <p className="empty-head">Line clear</p>
        <p className="muted">No open incidents in {scopeName}. New detections appear here within seconds.</p>
      </div>
    );
  }
  return (
    <ul className="incidents">
      {incidents.map((i) => (
        <li key={i.incident_id}>
          <Link href={`/incident?id=${encodeURIComponent(i.incident_id)}`} className={`incident sev-${i.severity}`}>
            <span className="incident-sev">
              <Lamp severity={i.severity} size={11} flash={i.severity === 1 && i.status === "OPEN"} />
              <span className="sev-tag">{ASPECT[i.severity].label}</span>
            </span>
            <span className="incident-what">
              <strong>{TYPE_LABEL[i.type] ?? i.type}</strong>
              <span>
                {i.section ?? i.segment_id}
                <span className="muted"> · {i.line_name ?? DIVISION_NAME[i.division] ?? i.division}</span>
              </span>
              <span className="mono muted">
                {i.segment_id} · {km(i.km_from)} · {ago(i.first_seen, now)}
              </span>
            </span>
            <span className="incident-state">
              {i.status === "ACKNOWLEDGED" ? (
                <span className="chip chip-ack">Acknowledged{i.acknowledged_by ? ` · ${i.acknowledged_by}` : ""}</span>
              ) : i.escalation_state === "UNACKNOWLEDGED" ? (
                <span className="chip chip-alarm">Nobody acknowledged</span>
              ) : i.escalation_role ? (
                <span className="chip">Paging {ROLE_LABEL[i.escalation_role] ?? i.escalation_role}</span>
              ) : (
                <span className="chip">Open</span>
              )}
            </span>
          </Link>
        </li>
      ))}
    </ul>
  );
}
