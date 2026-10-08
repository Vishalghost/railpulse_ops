"use client";

import Link from "next/link";
import { Lamp } from "./Lamp";
import { ASPECT, TYPE_LABEL } from "@/lib/vocab";
import type { MapSegment } from "@/lib/types";

/**
 * The mimic panel: the division's line drawn as one rail, a signal per 1 km segment
 * showing its worst active aspect, and km posts underneath. Segments with an incident
 * link to it; clear segments are plain text so keyboard users only tab through trouble.
 */
export function TrackStrip({ segments, incidentType }: { segments: MapSegment[]; incidentType: Record<string, string> }) {
  if (segments.length === 0) {
    return <p className="muted">No track segments are registered for this division yet. Run seed_assets.py to load them.</p>;
  }
  return (
    <div className="strip-scroll" role="region" aria-label="Track diagram" tabIndex={0}>
      <ol className="strip">
        {segments.map((s) => {
          const sev = s.worst_severity;
          const what = s.worst_incident ? TYPE_LABEL[incidentType[s.worst_incident]] ?? "Incident" : null;
          const label = sev ? `${s.segment_id}, ${ASPECT[sev].label}: ${what}` : `${s.segment_id}, line clear`;
          const body = (
            <>
              <span className="signal">
                <Lamp severity={sev} size={16} flash={sev === 1} />
                <span className="post" />
              </span>
              <span className="sev-tag">{sev ? ASPECT[sev].label : ""}</span>
              {s.has_switch && <span className="points" title="Points (switch)">⟋</span>}
              <span className="kmpost">
                <span className="kmpost-cap" />
                <span className="kmpost-num">{s.km_to}</span>
              </span>
            </>
          );
          return (
            <li key={s.segment_id} className={`seg${sev ? ` sev-${sev}` : ""}`} title={label}>
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
      </ol>
    </div>
  );
}
