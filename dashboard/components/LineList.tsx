"use client";

import { Lamp } from "./Lamp";
import { ASPECT, CLEAR, DIVISION_NAME } from "@/lib/vocab";
import type { LineView } from "@/lib/types";

/** Every line in scope, worst first; each row carries a to-scale strip of its block sections. */
export function LineList({
  lines,
  selected,
  onSelect,
  showDivision,
}: {
  lines: LineView[];
  selected: string | null;
  onSelect: (key: string) => void;
  showDivision: boolean;
}) {
  if (lines.length === 0) {
    return <p className="muted">No lines are registered yet. Run seed_assets.py to load them.</p>;
  }
  const ordered = lines.slice().sort((a, b) => (a.worst ?? 9) - (b.worst ?? 9) || a.name.localeCompare(b.name));
  return (
    <ul className="lines">
      {ordered.map((l) => {
        const length = l.segments[l.segments.length - 1].km_to;
        const open = l.segments.filter((s) => s.worst_severity).length;
        return (
          <li key={l.key}>
            <button
              type="button"
              className={`line-row${selected === l.key ? " selected" : ""}`}
              aria-pressed={selected === l.key}
              onClick={() => onSelect(l.key)}
            >
              <Lamp severity={l.worst} size={9} />
              <span className="line-name">
                <strong>{l.name}</strong>
                <span className="muted">
                  {showDivision ? `${DIVISION_NAME[l.division] ?? l.division} · ` : ""}
                  {length.toFixed(0)} km · {open ? `${open} open` : "clear"}
                </span>
              </span>
              <span className="line-strip" aria-hidden="true">
                {l.segments.map((s) => (
                  <span
                    key={s.segment_id}
                    style={{
                      flexGrow: s.km_to - s.km_from,
                      background: s.worst_severity ? ASPECT[s.worst_severity].color : CLEAR,
                    }}
                  />
                ))}
              </span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}
