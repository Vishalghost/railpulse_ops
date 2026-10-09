"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { IncidentList } from "@/components/IncidentList";
import { Lamp } from "@/components/Lamp";
import { LineDiagram } from "@/components/LineDiagram";
import { LineList } from "@/components/LineList";
import { NetworkMap } from "@/components/NetworkMap";
import { Shell, useSession } from "@/components/Shell";
import { api, type FeedStatus, subscribeIncidents } from "@/lib/api";
import { loadConfig } from "@/lib/config";
import { ago, ALL, ASPECT, DIVISION_NAME } from "@/lib/vocab";
import type { Incident, LineView, MapSegment, SensorHealth, Severity } from "@/lib/types";

const FEED_TEXT: Record<FeedStatus, string> = {
  connecting: "Connecting live feed",
  live: "Live",
  reconnecting: "Live feed lost, reconnecting",
};

export default function OverviewPage() {
  const [feed, setFeed] = useState<FeedStatus>("connecting");
  return (
    <Shell status={<span className={`feed feed-${feed}`}>{FEED_TEXT[feed]}</span>}>
      <Overview onFeed={setFeed} />
    </Shell>
  );
}

function bySeverityThenAge(a: Incident, b: Incident) {
  return a.severity - b.severity || b.first_seen - a.first_seen;
}

function Overview({ onFeed }: { onFeed: (s: FeedStatus) => void }) {
  const { scope, scopeDivisions } = useSession();
  const [segments, setSegments] = useState<Record<string, MapSegment[]>>({});
  const [incidents, setIncidents] = useState<Record<string, Incident> | null>(null);
  const [health, setHealth] = useState<Record<string, SensorHealth | null>>({});
  const [styleUrl, setStyleUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const scopeKey = scopeDivisions.join(",");

  useEffect(() => {
    loadConfig().then((c) => setStyleUrl(c.mapStyleUrl)).catch(() => setStyleUrl(null));
    const tick = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(tick);
  }, []);

  // Track layout and the incident snapshot, then the live feed for every division in scope.
  useEffect(() => {
    const divs = scopeKey ? scopeKey.split(",") : [];
    if (divs.length === 0) return;
    setIncidents(null);
    setError(null);
    setSelected(null);
    Promise.all(
      divs.map(async (d) => {
        const [map, open, acked] = await Promise.all([
          api<{ segments: MapSegment[] }>(`/divisions/${d}/map`),
          api<{ items: Incident[] }>(`/divisions/${d}/incidents?status=OPEN`),
          api<{ items: Incident[] }>(`/divisions/${d}/incidents?status=ACKNOWLEDGED`),
        ]);
        return { d, segs: map.segments.map((s) => ({ ...s, division: d })), items: [...open.items, ...acked.items] };
      }),
    )
      .then((rows) => {
        setSegments(Object.fromEntries(rows.map((r) => [r.d, r.segs])));
        setIncidents(Object.fromEntries(rows.flatMap((r) => r.items).map((i) => [i.incident_id, i])));
      })
      .catch((e: Error) => setError(e.message));

    const status: Record<string, FeedStatus> = {};
    const report = () => {
      const all = divs.map((d) => status[d] ?? "connecting");
      onFeed(all.includes("reconnecting") ? "reconnecting" : all.every((s) => s === "live") ? "live" : "connecting");
    };
    const stops = divs.map((d) =>
      subscribeIncidents(
        d,
        (ev) =>
          setIncidents((cur) => {
            const next = { ...(cur ?? {}) };
            if (ev.kind === "closed") delete next[ev.incident.incident_id];
            else next[ev.incident.incident_id] = ev.incident;
            return next;
          }),
        (s) => {
          status[d] = s;
          report();
        },
      ),
    );
    return () => stops.forEach((stop) => stop());
  }, [scopeKey, onFeed]);

  useEffect(() => {
    const divs = scopeKey ? scopeKey.split(",") : [];
    const load = () =>
      Promise.all(
        divs.map((d) =>
          api<{ health: SensorHealth | null }>(`/divisions/${d}/sensor-health`)
            .then((r) => [d, r.health] as const)
            .catch(() => [d, null] as const),
        ),
      ).then((rows) => setHealth(Object.fromEntries(rows)));
    load();
    const every = setInterval(load, 60_000); // the checker itself runs every 5 min
    return () => clearInterval(every);
  }, [scopeKey]);

  const active = useMemo(() => Object.values(incidents ?? {}).sort(bySeverityThenAge), [incidents]);
  const typeById = useMemo(() => Object.fromEntries(active.map((i) => [i.incident_id, i.type])), [active]);

  // Worst open incident per block section, recomputed from the live incident set.
  const lines = useMemo<LineView[]>(() => {
    const worst: Record<string, Incident> = {};
    for (const i of active) if (!worst[i.segment_id] || i.severity < worst[i.segment_id].severity) worst[i.segment_id] = i;
    const byLine = new Map<string, LineView>();
    for (const d of scopeDivisions) {
      for (const s of segments[d] ?? []) {
        const key = `${d}:${s.line_code}`;
        const w = worst[s.segment_id];
        const seg = { ...s, worst_severity: w ? w.severity : null, worst_incident: w ? w.incident_id : null };
        const line = byLine.get(key) ?? { key, division: d, code: s.line_code, name: s.line_name, segments: [], worst: null };
        line.segments.push(seg);
        if (seg.worst_severity && (!line.worst || seg.worst_severity < line.worst)) line.worst = seg.worst_severity;
        byLine.set(key, line);
      }
    }
    const out = [...byLine.values()];
    out.forEach((l) => l.segments.sort((a, b) => a.line_seq - b.line_seq));
    return out;
  }, [segments, active, scopeDivisions]);

  const counts = useMemo(() => {
    const c: Record<Severity, number> = { 1: 0, 2: 0, 3: 0, 4: 0 };
    active.forEach((i) => c[i.severity]++);
    return c;
  }, [active]);

  const sensors = useMemo(() => {
    const hs = scopeDivisions.map((d) => health[d]).filter((h): h is SensorHealth => !!h);
    if (hs.length === 0) return null;
    return {
      total: hs.reduce((n, h) => n + h.total, 0),
      reporting: hs.reduce((n, h) => n + h.reporting, 0),
      silent: hs.reduce((n, h) => n + h.silent.length, 0),
      checked: Math.min(...hs.map((h) => h.checked_at)),
    };
  }, [health, scopeDivisions]);

  // The diagram always shows something: the chosen line, else the one in worst shape.
  const shown = useMemo(() => {
    if (lines.length === 0) return null;
    const pick = lines.find((l) => l.key === selected);
    if (pick) return pick;
    return lines.slice().sort((a, b) => (a.worst ?? 9) - (b.worst ?? 9))[0];
  }, [lines, selected]);

  const scopeName = scope === ALL ? "any division" : `${DIVISION_NAME[scope] ?? scope} division`;
  const km = lines.reduce((n, l) => n + l.segments[l.segments.length - 1].km_to, 0);
  const sections = lines.reduce((n, l) => n + l.segments.length, 0);
  const select = (key: string) => setSelected((cur) => (cur === key ? null : key));

  return (
    <main className="board">
      <section className="kpis" aria-label="Network status">
        <div className="kpi">
          <span className="kpi-num">{lines.length}</span>
          <span className="kpi-label">lines · {Math.round(km).toLocaleString("en-IN")} km</span>
        </div>
        <div className="kpi">
          <span className="kpi-num">{sections}</span>
          <span className="kpi-label">block sections</span>
        </div>
        <div className={`kpi${sensors?.silent ? " kpi-warn" : ""}`}>
          <span className="kpi-num">
            {sensors ? sensors.reporting.toLocaleString("en-IN") : "—"}
            {sensors && <span className="kpi-of">/{sensors.total.toLocaleString("en-IN")}</span>}
          </span>
          <span className="kpi-label">
            {sensors
              ? `sensors reporting${sensors.silent ? ` · ${sensors.silent} silent` : ""} · checked ${ago(sensors.checked * 1000, now)}`
              : "sensors: first heartbeat check pending"}
          </span>
        </div>
        <ul className="kpi aspects" aria-label="Open incidents by severity">
          {([1, 2, 3, 4] as Severity[]).map((s) => (
            <li key={s} className={counts[s] ? "" : "zero"} title={ASPECT[s].action}>
              <Lamp severity={s} size={10} flash={s === 1 && counts[1] > 0} />
              <span className="sev-tag">{ASPECT[s].label}</span>
              <span className="kpi-num">{counts[s]}</span>
            </li>
          ))}
        </ul>
      </section>

      {error && (
        <p role="alert" className="alert">
          Couldn&apos;t load the network: {error}. The page retries when the live feed reconnects.
        </p>
      )}

      <div className="board-main">
        <section className="map-panel" aria-label="Network map">
          <NetworkMap styleUrl={styleUrl} lines={lines} selected={selected} onSelect={select} />
          <ul className="legend" aria-label="Map key">
            <li><span className="legend-line" style={{ background: "var(--clear)" }} />Clear</li>
            {([1, 2, 3, 4] as Severity[]).map((s) => (
              <li key={s}><span className="legend-line" style={{ background: ASPECT[s].color }} />{ASPECT[s].label}</li>
            ))}
            <li><span className="legend-station" />Station</li>
          </ul>
        </section>

        <aside className="side">
          <section className="panel feed-panel" aria-labelledby="active-title">
            <h2 id="active-title" className="panel-title">
              Open incidents <span className="count-pill">{active.length}</span>
            </h2>
            {incidents === null ? <p className="muted">Loading incidents…</p> : <IncidentList incidents={active} now={now} scopeName={scopeName} />}
          </section>
          <section className="panel" aria-labelledby="lines-title">
            <h2 id="lines-title" className="panel-title">
              Lines <span className="muted">select one to see its stations</span>
            </h2>
            <LineList lines={lines} selected={selected} onSelect={select} showDivision={scope === ALL} />
          </section>
        </aside>
      </div>

      {shown && <LineDiagram line={shown} incidentType={typeById} />}
    </main>
  );
}
