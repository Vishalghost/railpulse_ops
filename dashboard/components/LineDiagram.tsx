"use client";

import Link from "next/link";
import { ASPECT, DIVISION_NAME, TYPE_LABEL } from "@/lib/vocab";
import type { LineView } from "@/lib/types";

const PX_PER_KM = 14;
const MIN_SECTION_PX = 96; // room for two station boards side by side
const EDGE_PX = 72;

/**
 * The line as a control-room diagram: Indian Railways' yellow station-name boards along one
 * track, each block section drawn to scale and coloured by its worst open incident. Boards
 * alternate above and below the track so neighbouring names never collide.
 */
export function LineDiagram({ line, incidentType }: { line: LineView; incidentType: Record<string, string> }) {
  const widths = line.segments.map((s) => Math.max(MIN_SECTION_PX, (s.km_to - s.km_from) * PX_PER_KM));
  const xs = widths.reduce<number[]>((acc, w) => [...acc, acc[acc.length - 1] + w], [EDGE_PX]);
  const total = xs[xs.length - 1] + EDGE_PX;
  const stations = [line.segments[0].from_station, ...line.segments.map((s) => s.to_station)];
  const kms = [line.segments[0].km_from, ...line.segments.map((s) => s.km_to)];
  const open = line.segments.filter((s) => s.worst_severity).length;
  const length = line.segments[line.segments.length - 1].km_to;

  return (
    <section className="panel diagram" aria-labelledby="diagram-title">
      <header className="diagram-head">
        <h2 id="diagram-title" className="display">
          {line.name}
        </h2>
        <p className="muted">
          {DIVISION_NAME[line.division] ?? line.division} division · {stations.length} stations · {length.toFixed(0)} km ·{" "}
          {open === 0 ? "every block section clear" : `${open} block section${open === 1 ? "" : "s"} with an open incident`}
        </p>
      </header>
      <div className="diagram-scroll" role="region" aria-label={`${line.name} track diagram`} tabIndex={0}>
        <ol className="diagram-track" style={{ width: total }}>
          {line.segments.map((s, i) => {
            const sev = s.worst_severity;
            const what = s.worst_incident ? (TYPE_LABEL[incidentType[s.worst_incident]] ?? "Incident") : null;
            const label = `${s.from_station} to ${s.to_station}, km ${s.km_from}–${s.km_to}: ${sev ? `${ASPECT[sev].label} ${what}` : "clear"}`;
            const style = { left: xs[i], width: widths[i] };
            const body = (
              <>
                <span className="section-rail" />
                {sev && <span className="section-tag">{ASPECT[sev].label}</span>}
                {s.has_switch && <span className="section-points" title="Points (switch) with a monitored point machine" />}
              </>
            );
            return (
              <li key={s.segment_id} className={`section${sev ? ` sev-${sev}` : ""}`} style={style} title={label}>
                {s.worst_incident ? (
                  <Link href={`/incident?id=${encodeURIComponent(s.worst_incident)}`} aria-label={label}>
                    {body}
                  </Link>
                ) : (
                  <span aria-label={label}>{body}</span>
                )}
              </li>
            );
          })}
          {stations.map((name, i) => (
            <li key={`${name}-${i}`} className={`station ${i % 2 ? "below" : "above"}`} style={{ left: xs[i] }} aria-hidden="true">
              <span className="station-mast" />
              <span className="station-board">{name}</span>
              <span className="station-km mono">{kms[i].toFixed(1)}</span>
            </li>
          ))}
        </ol>
      </div>
    </section>
  );
}
