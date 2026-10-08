import { ASPECT, CLEAR } from "@/lib/vocab";
import type { Severity } from "@/lib/types";

/** A signal head: one lamp, or two for P3 ("double" aspect). Decorative — callers supply the text label. */
export function Lamp({ severity, size = 14, flash = false }: { severity: Severity | null; size?: number; flash?: boolean }) {
  const color = severity ? ASPECT[severity].color : CLEAR;
  const lamps = severity ? ASPECT[severity].lamps : 1;
  return (
    <span className={`lamp-head${flash ? " flash" : ""}`} aria-hidden="true">
      {Array.from({ length: lamps }, (_, i) => (
        <span key={i} className="lamp" style={{ width: size, height: size, background: color, color }} />
      ))}
    </span>
  );
}
