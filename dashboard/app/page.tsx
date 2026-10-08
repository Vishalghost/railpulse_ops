"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { GeoMap } from "@/components/GeoMap";
import { IncidentList } from "@/components/IncidentList";
import { Lamp } from "@/components/Lamp";
import { Shell, useSession } from "@/components/Shell";
import { TrackStrip } from "@/components/TrackStrip";
import { api, type FeedStatus, subscribeIncidents } from "@/lib/api";
import { loadConfig } from "@/lib/config";
import { ago, ASPECT, DIVISION_NAME } from "@/lib/vocab";
import type { Incident, MapSegment, SensorHealth, Severity } from "@/lib/types";

const FEED_TEXT: Record<FeedStatus, string> = {
  connecting: "Connecting live feed",
  live: "Live",
  reconnecting: "Live feed lost — reconnecting",
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
  return a.severity - b.severity || a.first_seen - b.first_seen;
}

function Overview({ onFeed }: { onFeed: (s: FeedStatus) => void }) {
  const { division } = useSession();
  const [segments, setSegments] = useState<MapSegment[]>([]);
  const [incidents, setIncidents] = useState<Incident[] | null>(null);
  const [styleUrl, setStyleUrl] = useState<string | null>(null);
  const [health, setHealth] = useState<SensorHealth | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const mapTimer = useRef<ReturnType<typeof setTimeout>>(undefined);

  const loadMap = useCallback(
    () => api<{ segments: MapSegment[] }>(`/divisions/${division}/map`).then((r) => setSegments(r.segments)),
    [division],
  );

  useEffect(() => {
    loadConfig().then((c) => setStyleUrl(c.mapStyleUrl)).catch(() => setStyleUrl(null));
    const tick = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(tick);
  }, []);

  useEffect(() => {
    if (!division) return;
    const loadHealth = () =>
      api<{ health: SensorHealth | null }>(`/divisions/${division}/sensor-health`)
        .then((r) => setHealth(r.health))
        .catch(() => setHealth(null));
    loadHealth();
    const every = setInterval(loadHealth, 60_000); // the checker itself runs every 5 min
    return () => clearInterval(every);
  }, [division]);

  useEffect(() => {
    if (!division) return;
    setIncidents(null);
    setError(null);
    Promise.all([
      loadMap(),
      api<{ items: Incident[] }>(`/divisions/${division}/incidents?status=OPEN`),
      api<{ items: Incident[] }>(`/divisions/${division}/incidents?status=ACKNOWLEDGED`),
    ])
      .then(([, open, acked]) => setIncidents([...open.items, ...acked.items]))
      .catch((e: Error) => setError(e.message));

    return subscribeIncidents(
      division,
      (ev) => {
        setIncidents((cur) => {
          const rest = (cur ?? []).filter((i) => i.incident_id !== ev.incident.incident_id);
          return ev.kind === "closed" ? rest : [...rest, ev.incident];
        });
        clearTimeout(mapTimer.current); // coalesce bursts into one map refresh
        mapTimer.current = setTimeout(() => loadMap().catch(() => undefined), 500);
      },
      onFeed,
    );
  }, [division, loadMap, onFeed]);

  const sorted = useMemo(() => (incidents ?? []).slice().sort(bySeverityThenAge), [incidents]);
  const typeById = useMemo(() => Object.fromEntries(sorted.map((i) => [i.incident_id, i.type])), [sorted]);
  const counts = useMemo(() => {
    const c: Record<Severity, number> = { 1: 0, 2: 0, 3: 0, 4: 0 };
    sorted.forEach((i) => c[i.severity]++);
    return c;
  }, [sorted]);
  const name = DIVISION_NAME[division] ?? division;
  const kmMax = segments.length ? segments[segments.length - 1].km_to : 0;

  return (
    <main className="page">
      <section className="panel strip-panel" aria-labelledby="strip-title">
        <div className="panel-head">
          <div>
            <h1 id="strip-title" className="display">
              {name} division <span className="muted">km 0–{kmMax}</span>
            </h1>
            {health && (
              <p className={`sensor-health${health.silent.length ? " has-silent" : ""}`}>
                {health.reporting} of {health.total} sensors reporting
                {health.silent.length > 0 && <> · <strong>{health.silent.length} gone silent</strong></>}
                {health.not_reporting.length > 0 && <> · {health.not_reporting.length} never reported</>}
                <span className="muted mono"> · checked {ago(health.checked_at * 1000, now)}</span>
              </p>
            )}
          </div>
          <ul className="aspects" aria-label="Active incidents by severity">
            {([1, 2, 3, 4] as Severity[]).map((s) => (
              <li key={s} className={counts[s] ? "" : "zero"}>
                <Lamp severity={s} size={10} />
                <span className="sev-tag">{ASPECT[s].label}</span>
                <span className="count">{counts[s]}</span>
                <span className="muted aspect-action">{ASPECT[s].action}</span>
              </li>
            ))}
          </ul>
        </div>
        <TrackStrip segments={segments} incidentType={typeById} />
      </section>

      {error && (
        <p role="alert" className="alert">
          Couldn&apos;t load {name} division: {error}. The page retries when the live feed reconnects.
        </p>
      )}

      <div className="split">
        <section className="panel" aria-labelledby="active-title">
          <h2 id="active-title" className="panel-title">
            Active incidents
          </h2>
          {incidents === null ? <p className="muted">Loading incidents…</p> : <IncidentList incidents={sorted} now={now} divisionName={name} />}
        </section>
        <section className="panel" aria-labelledby="map-title">
          <h2 id="map-title" className="panel-title">
            Map
          </h2>
          <GeoMap styleUrl={styleUrl} segments={segments} />
        </section>
      </div>
    </main>
  );
}
