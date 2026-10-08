"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";
import { Lamp } from "@/components/Lamp";
import { Shell, useSession } from "@/components/Shell";
import { type Point, TelemetryChart } from "@/components/TelemetryChart";
import { api, ApiError } from "@/lib/api";
import { ago, ASPECT, clock, DIVISION_NAME, km, ROLE_LABEL, TYPE_LABEL } from "@/lib/vocab";
import type { Incident, SegmentMeta, TelemetrySensor, TimelineEntry } from "@/lib/types";

interface Detail {
  incident: Incident;
  timeline: TimelineEntry[];
  segment: SegmentMeta | null;
}

interface MetricSpec {
  type: TelemetrySensor["type"];
  key: string;
  title: string;
  unit: string;
  underLoadOnly?: boolean;
  reference?: (seg: SegmentMeta | null) => { value: number; label: string } | undefined;
}

// Thresholds mirror worker/app/detection.py so the chart shows what the detector judged against.
const METRICS: MetricSpec[] = [
  { type: "UTS", key: "backwall_echo_db", title: "Ultrasonic back-wall echo", unit: "dB" },
  { type: "UTS", key: "flaw_echo_db", title: "Ultrasonic flaw echo", unit: "dB", reference: () => ({ value: 30, label: "Flaw gate" }) },
  { type: "VIB", key: "kurtosis", title: "Vibration kurtosis, trains passing", unit: "", underLoadOnly: true, reference: () => ({ value: 8, label: "Impact limit" }) },
  { type: "VIB", key: "rms_g", title: "Vibration RMS, trains passing", unit: "g", underLoadOnly: true },
  {
    type: "TMP", key: "rail_temp_c", title: "Rail temperature", unit: "°C",
    reference: (s) => (s?.stress_free_temp_c != null ? { value: s.stress_free_temp_c, label: "Stress-free temp" } : undefined),
  },
  { type: "SWT", key: "throw_time_ms", title: "Point throw time", unit: "ms", reference: () => ({ value: 6000, label: "Limit" }) },
  { type: "SWT", key: "motor_current_a", title: "Point motor current", unit: "A", reference: () => ({ value: 5, label: "Limit" }) },
];

export default function IncidentPage() {
  return (
    <Shell>
      <Suspense fallback={<main className="page"><p className="muted">Loading…</p></main>}>
        <IncidentView />
      </Suspense>
    </Shell>
  );
}

function IncidentView() {
  const id = useSearchParams().get("id") ?? "";
  const { me } = useSession();
  const [detail, setDetail] = useState<Detail | null>(null);
  const [sensors, setSensors] = useState<TelemetrySensor[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [done, setDone] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const d = await api<Detail>(`/incidents/${encodeURIComponent(id)}`);
      setDetail(d);
      const t = await api<{ sensors: TelemetrySensor[] }>(`/segments/${d.incident.segment_id}/telemetry?hours=6`);
      setSensors(t.sensors);
    } catch (e) {
      setError(e instanceof ApiError && e.status === 404 ? "This incident doesn't exist or was removed." : (e as Error).message);
    }
  }, [id]);

  useEffect(() => {
    if (id) load();
    else setError("No incident selected.");
  }, [id, load]);

  const act = async (action: "acknowledge" | "resolve") => {
    setBusy(true);
    setDone(null);
    try {
      const body = action === "resolve" ? JSON.stringify({ note: note || null }) : undefined;
      await api(`/incidents/${encodeURIComponent(id)}/${action}`, { method: "POST", body });
      setDone(action === "acknowledge" ? "Acknowledged" : "Resolved");
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  if (error) {
    return (
      <main className="page narrow">
        <p role="alert">{error}</p>
        <Link href="/">Back to the division</Link>
      </main>
    );
  }
  if (!detail) return <main className="page"><p className="muted">Loading incident…</p></main>;

  const { incident: inc, timeline, segment } = detail;
  const aspect = ASPECT[inc.severity];
  const finding = inc.last_finding;

  return (
    <main className="page">
      <Link href="/" className="back">← {DIVISION_NAME[inc.division] ?? inc.division} division</Link>

      <section className={`panel incident-head sev-${inc.severity}`}>
        <Lamp severity={inc.severity} size={28} flash={inc.severity === 1 && inc.status === "OPEN"} />
        <div>
          <p className="eyebrow">
            <span className="sev-tag">{aspect.label}</span> {aspect.action}
          </p>
          <h1 className="display">{TYPE_LABEL[inc.type] ?? inc.type}</h1>
          <p className="mono">
            {inc.segment_id} · {km(inc.km_from)}
            {segment?.km_to != null ? `–${segment.km_to.toFixed(1)}` : ""} · {DIVISION_NAME[inc.division] ?? inc.division}
          </p>
        </div>
        <dl className="facts">
          <div><dt>Status</dt><dd>{inc.status === "OPEN" ? "Open" : inc.status === "ACKNOWLEDGED" ? "Acknowledged" : "Resolved"}</dd></div>
          <div><dt>First detected</dt><dd className="mono">{clock(inc.first_seen)} · {ago(inc.first_seen)}</dd></div>
          <div><dt>Readings</dt><dd className="mono">{inc.occurrences}</dd></div>
          {finding && <div><dt>Latest reading</dt><dd>{finding.detail}</dd></div>}
        </dl>
      </section>

      {inc.status !== "RESOLVED" && (
        <section className="panel actions" aria-label="Actions">
          {inc.status === "OPEN" && (
            <button className="btn btn-ack" disabled={busy} onClick={() => act("acknowledge")}>
              Acknowledge
            </button>
          )}
          {me.canResolve && (
            <form
              className="resolve"
              onSubmit={(e) => {
                e.preventDefault();
                act("resolve");
              }}
            >
              <label>
                <span>Resolution note</span>
                <input value={note} maxLength={500} onChange={(e) => setNote(e.target.value)} placeholder="e.g. Rail replaced, speed restriction lifted" />
              </label>
              <button className="btn" disabled={busy} type="submit">Resolve</button>
            </form>
          )}
          {done && <p role="status" className="done">{done}</p>}
        </section>
      )}

      <div className="split">
        <section className="panel" aria-labelledby="ladder-title">
          <h2 id="ladder-title" className="panel-title">Escalation</h2>
          <Ladder incident={inc} timeline={timeline} />
        </section>
        <section className="panel" aria-labelledby="tele-title">
          <h2 id="tele-title" className="panel-title">Sensor readings, last 6 hours</h2>
          {sensors === null ? <p className="muted">Loading readings…</p> : <Charts sensors={sensors} segment={segment} />}
        </section>
      </div>
    </main>
  );
}

/** The ladder is a real sequence (rung 1 → 2 → 3), so it's numbered. */
function Ladder({ incident, timeline }: { incident: Incident; timeline: TimelineEntry[] }) {
  const rungs = timeline.slice().sort((a, b) => a.at - b.at);
  if (rungs.length === 0) {
    return <p className="muted">Nobody has been paged yet. P4 advisories are shown here only.</p>;
  }
  return (
    <ol className="ladder">
      {rungs.map((r) => {
        const reached = r.results.filter((x) => x.status === "sent" || x.status === "dry_run").length;
        const acked = incident.acknowledged_role === r.role;
        return (
          <li key={`${r.at}-${r.role}`} className={acked ? "acked" : ""}>
            <span className="rung">{r.level + 1}</span>
            <div>
              <strong>{ROLE_LABEL[r.role] ?? r.role}</strong>
              <span className="mono muted"> paged {clock(r.at * 1000)}</span>
              <p className="muted">
                {r.results.length} contact{r.results.length === 1 ? "" : "s"}, {reached} SMS delivered
                {r.results.length ? ` · ${[...new Set(r.results.map((x) => x.lang))].join(", ")}` : ""}
                {r.awaiting_ack ? "" : " · for information"}
              </p>
            </div>
          </li>
        );
      })}
      {incident.acknowledged_by && (
        <li className="acked">
          <span className="rung">✓</span>
          <div>
            <strong>Acknowledged by {incident.acknowledged_by}</strong>
            {incident.acknowledged_at && <span className="mono muted"> {clock(incident.acknowledged_at * 1000)}</span>}
          </div>
        </li>
      )}
      {incident.escalation_state === "UNACKNOWLEDGED" && !incident.acknowledged_by && (
        <li className="alarm">
          <span className="rung">!</span>
          <div><strong>Nobody acknowledged.</strong> Call the Divisional Engineer directly.</div>
        </li>
      )}
    </ol>
  );
}

function Charts({ sensors, segment }: { sensors: TelemetrySensor[]; segment: SegmentMeta | null }) {
  const charts = METRICS.flatMap((m) => {
    const sensor = sensors.find((s) => s.type === m.type);
    if (!sensor) return [];
    const points: Point[] = sensor.points
      .filter((p) => !m.underLoadOnly || p.metrics.train_passing === true)
      .filter((p) => typeof p.metrics[m.key] === "number")
      .map((p) => ({ t: p.ts, v: p.metrics[m.key] as number }));
    return [<TelemetryChart key={m.key} title={m.title} unit={m.unit} points={points} reference={m.reference?.(segment)} />];
  });
  return charts.length ? <div className="charts">{charts}</div> : <p className="muted">No sensors are registered on this segment.</p>;
}
