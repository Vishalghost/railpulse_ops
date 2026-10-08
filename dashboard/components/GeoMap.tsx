"use client";

import { useEffect, useRef } from "react";
import type { Map as MapLibreMap, Marker } from "maplibre-gl";
import { ASPECT, CLEAR } from "@/lib/vocab";
import type { MapSegment } from "@/lib/types";

/** Amazon Location Service (Maps v2) basemap with one marker per segment. */
export function GeoMap({ styleUrl, segments }: { styleUrl: string | null; segments: MapSegment[] }) {
  const el = useRef<HTMLDivElement>(null);
  const map = useRef<MapLibreMap | null>(null);
  const markers = useRef<Marker[]>([]);

  useEffect(() => {
    if (!styleUrl || !el.current || segments.length === 0) return;
    let cancelled = false;
    (async () => {
      const maplibre = (await import("maplibre-gl")).default;
      if (cancelled || !el.current) return;
      if (!map.current) {
        const lons = segments.map((s) => s.lon);
        const lats = segments.map((s) => s.lat);
        map.current = new maplibre.Map({
          container: el.current,
          style: styleUrl,
          bounds: [
            [Math.min(...lons) - 0.02, Math.min(...lats) - 0.02],
            [Math.max(...lons) + 0.02, Math.max(...lats) + 0.02],
          ],
          attributionControl: { compact: true },
        });
        map.current.addControl(new maplibre.NavigationControl({ showCompass: false }), "top-right");
      }
      markers.current.forEach((m) => m.remove());
      markers.current = segments.map((s) => {
        const dot = document.createElement("span");
        dot.className = `map-dot${s.worst_severity ? ` sev-${s.worst_severity}` : ""}`;
        dot.style.background = s.worst_severity ? ASPECT[s.worst_severity].color : CLEAR;
        dot.title = `${s.segment_id}${s.worst_severity ? ` · ${ASPECT[s.worst_severity].label}` : " · clear"}`;
        return new maplibre.Marker({ element: dot }).setLngLat([s.lon, s.lat]).addTo(map.current!);
      });
    })();
    return () => {
      cancelled = true;
    };
  }, [styleUrl, segments]);

  useEffect(() => () => map.current?.remove(), []);

  if (!styleUrl) {
    return <p className="muted map-off">Map tiles aren&apos;t configured for this environment. The track diagram above shows every segment.</p>;
  }
  return <div ref={el} className="geomap" role="img" aria-label="Map of track segments coloured by severity" />;
}
