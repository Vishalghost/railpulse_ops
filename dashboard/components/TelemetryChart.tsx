"use client";

import { useId, useMemo, useState } from "react";
import { clock } from "@/lib/vocab";

export interface Point {
  t: number;
  v: number;
}

const W = 640;
const H = 180;
const PAD = { top: 16, right: 16, bottom: 26, left: 48 };

/**
 * One metric, one axis (small multiple). Single series → no legend; the title names it.
 * Reference line (limit / stress-free temp) is dashed and labelled in text ink.
 * Hover/focus shows a crosshair + value; a table view sits underneath for screen readers.
 */
export function TelemetryChart({
  title,
  unit,
  points,
  reference,
}: {
  title: string;
  unit: string;
  points: Point[];
  reference?: { value: number; label: string };
}) {
  const [hover, setHover] = useState<number | null>(null);
  const tableId = useId();

  const geom = useMemo(() => {
    if (points.length === 0) return null;
    const t0 = points[0].t;
    const t1 = points[points.length - 1].t || t0 + 1;
    const values = points.map((p) => p.v).concat(reference ? [reference.value] : []);
    let lo = Math.min(...values);
    let hi = Math.max(...values);
    const span = hi - lo || Math.abs(hi) || 1;
    lo -= span * 0.08;
    hi += span * 0.08;
    const x = (t: number) => PAD.left + ((t - t0) / Math.max(1, t1 - t0)) * (W - PAD.left - PAD.right);
    const y = (v: number) => PAD.top + (1 - (v - lo) / (hi - lo)) * (H - PAD.top - PAD.bottom);
    const path = points.map((p, i) => `${i ? "L" : "M"}${x(p.t).toFixed(1)},${y(p.v).toFixed(1)}`).join("");
    const ticks = [lo + (hi - lo) * 0.1, (lo + hi) / 2, hi - (hi - lo) * 0.1];
    return { x, y, path, ticks, t0, t1 };
  }, [points, reference]);

  const fmt = (v: number) => `${Math.abs(v) >= 100 ? v.toFixed(0) : v.toFixed(2)}${unit ? ` ${unit}` : ""}`;

  if (!geom) {
    return (
      <figure className="chart">
        <figcaption>{title}</figcaption>
        <p className="muted">No readings in this window.</p>
      </figure>
    );
  }

  const onMove = (e: React.PointerEvent<SVGRectElement>) => {
    const box = e.currentTarget.getBoundingClientRect();
    const px = ((e.clientX - box.left) / box.width) * (W - PAD.left - PAD.right) + PAD.left;
    let best = 0;
    for (let i = 1; i < points.length; i++) {
      if (Math.abs(geom.x(points[i].t) - px) < Math.abs(geom.x(points[best].t) - px)) best = i;
    }
    setHover(best);
  };
  const h = hover != null ? points[hover] : null;
  const last = points[points.length - 1];

  return (
    <figure className="chart">
      <figcaption>
        {title} <span className="muted mono">latest {fmt(last.v)}</span>
      </figcaption>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-describedby={tableId}>
        {geom.ticks.map((v) => (
          <g key={v}>
            <line className="grid" x1={PAD.left} x2={W - PAD.right} y1={geom.y(v)} y2={geom.y(v)} />
            <text className="axis" x={PAD.left - 6} y={geom.y(v) + 4} textAnchor="end">
              {Math.abs(v) >= 100 ? v.toFixed(0) : v.toFixed(1)}
            </text>
          </g>
        ))}
        <text className="axis" x={PAD.left} y={H - 6}>{clock(geom.t0)}</text>
        <text className="axis" x={W - PAD.right} y={H - 6} textAnchor="end">{clock(geom.t1)}</text>
        {reference && (
          <g>
            <line className="ref" x1={PAD.left} x2={W - PAD.right} y1={geom.y(reference.value)} y2={geom.y(reference.value)} />
            <text className="ref-label" x={W - PAD.right} y={geom.y(reference.value) - 5} textAnchor="end">
              {reference.label} {fmt(reference.value)}
            </text>
          </g>
        )}
        <path className="series" d={geom.path} />
        <circle className="series-end" cx={geom.x(last.t)} cy={geom.y(last.v)} r={4} />
        {h && (
          <g pointerEvents="none">
            <line className="crosshair" x1={geom.x(h.t)} x2={geom.x(h.t)} y1={PAD.top} y2={H - PAD.bottom} />
            <circle className="series-end" cx={geom.x(h.t)} cy={geom.y(h.v)} r={4} />
            <text className="tip" x={Math.min(geom.x(h.t) + 8, W - 120)} y={PAD.top + 12}>
              {clock(h.t)} · {fmt(h.v)}
            </text>
          </g>
        )}
        <rect
          className="hit"
          x={PAD.left}
          y={PAD.top}
          width={W - PAD.left - PAD.right}
          height={H - PAD.top - PAD.bottom}
          onPointerMove={onMove}
          onPointerLeave={() => setHover(null)}
        />
      </svg>
      <details>
        <summary>Show readings as a table</summary>
        <table id={tableId}>
          <caption className="sr-only">{title}</caption>
          <thead>
            <tr>
              <th scope="col">Time</th>
              <th scope="col">{unit ? `Value (${unit})` : "Value"}</th>
            </tr>
          </thead>
          <tbody>
            {points.slice(-60).map((p) => (
              <tr key={p.t}>
                <td className="mono">{clock(p.t)}</td>
                <td className="mono">{fmt(p.v)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </figure>
  );
}
