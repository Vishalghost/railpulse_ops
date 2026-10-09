"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import type { GeoJSONSource, LngLatBoundsLike, Map as MapLibreMap, MapLayerMouseEvent } from "maplibre-gl";
import { ASPECT, CLEAR } from "@/lib/vocab";
import type { LineView } from "@/lib/types";

const BOARD_YELLOW = "#FFD23F";
const BOARD_INK = "#0D1520";
// Fonts that exist in the Amazon Location Monochrome style's glyph set.
const LABEL_FONT = ["Amazon Ember Condensed RC Bold,Noto Sans Bold"];

type Bounds = [[number, number], [number, number]];

function boundsOf(lines: LineView[]): Bounds | null {
  const pts = lines.flatMap((l) => l.segments.flatMap((s) => [[s.from_lon, s.from_lat], [s.to_lon, s.to_lat]]));
  if (pts.length === 0) return null;
  const lons = pts.map((p) => p[0]);
  const lats = pts.map((p) => p[1]);
  return [[Math.min(...lons), Math.min(...lats)], [Math.max(...lons), Math.max(...lats)]];
}

function toGeoJSON(lines: LineView[]) {
  const sections = lines.flatMap((l) =>
    l.segments.map((s) => ({
      type: "Feature" as const,
      geometry: { type: "LineString" as const, coordinates: [[s.from_lon, s.from_lat], [s.to_lon, s.to_lat]] },
      properties: {
        key: l.key,
        sev: s.worst_severity ?? 0,
        color: s.worst_severity ? ASPECT[s.worst_severity].color : CLEAR,
        incident: s.worst_incident ?? "",
        label: `${l.name}: ${s.from_station} → ${s.to_station}`,
        detail: s.worst_severity ? `${ASPECT[s.worst_severity].label} open` : "Clear",
      },
    })),
  );
  const seen = new Set<string>();
  const stations = lines.flatMap((l) =>
    l.segments.flatMap((s, i) => {
      const ends: [string, number, number][] = i === 0 ? [[s.from_station, s.from_lon, s.from_lat]] : [];
      ends.push([s.to_station, s.to_lon, s.to_lat]);
      return ends
        .filter(([name, lon, lat]) => !seen.has(`${name}${lon}${lat}`) && seen.add(`${name}${lon}${lat}`))
        .map(([name, lon, lat]) => ({
          type: "Feature" as const,
          geometry: { type: "Point" as const, coordinates: [lon, lat] },
          properties: { name },
        }));
    }),
  );
  return {
    sections: { type: "FeatureCollection" as const, features: sections },
    stations: { type: "FeatureCollection" as const, features: stations },
  };
}

export function NetworkMap({
  styleUrl,
  lines,
  selected,
  onSelect,
}: {
  styleUrl: string | null;
  lines: LineView[];
  selected: string | null;
  onSelect: (key: string) => void;
}) {
  const el = useRef<HTMLDivElement>(null);
  const map = useRef<MapLibreMap | null>(null);
  const [ready, setReady] = useState(false);
  const [hover, setHover] = useState<{ x: number; y: number; label: string; detail: string } | null>(null);
  const router = useRouter();
  const onSelectRef = useRef(onSelect);
  onSelectRef.current = onSelect;
  const fittedFor = useRef<string>("");

  // Create the map once.
  useEffect(() => {
    if (!styleUrl || !el.current || map.current) return;
    let cancelled = false;
    (async () => {
      const maplibre = (await import("maplibre-gl")).default;
      if (cancelled || !el.current) return;
      const m = new maplibre.Map({
        container: el.current,
        style: styleUrl,
        center: [79, 22],
        zoom: 4,
        attributionControl: { compact: true },
      });
      m.addControl(new maplibre.NavigationControl({ showCompass: false }), "top-right");
      // Add our layers as soon as the basemap style is usable, rather than waiting for "load"
      // (which also waits for every tile of the first frame).
      const init = () => {
        if (m.getSource("sections") || !m.isStyleLoaded()) return;
        m.addSource("sections", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
        m.addSource("stations", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
        // zoom may only drive a top-level interpolate, so each width gets its own scaled curve
        const width = (k: number, add = 0) =>
          ["interpolate", ["linear"], ["zoom"], 4, 4 * k + add, 9, 6 * k + add, 13, 10 * k + add] as unknown as number;
        m.addLayer({
          id: "sections-casing", type: "line", source: "sections",
          layout: { "line-cap": "round", "line-join": "round" },
          paint: { "line-color": BOARD_INK, "line-width": width(1, 3) },
        });
        m.addLayer({
          id: "sections-alarm", type: "line", source: "sections", filter: ["==", ["get", "sev"], 1],
          layout: { "line-cap": "round" },
          paint: { "line-color": ASPECT[1].color, "line-width": width(4), "line-blur": 8, "line-opacity": 0.55 },
        });
        m.addLayer({
          id: "sections", type: "line", source: "sections",
          // Where lines share track (e.g. Mumbai), draw the most severe section on top.
          layout: {
            "line-cap": "round", "line-join": "round",
            "line-sort-key": ["case", ["==", ["get", "sev"], 0], 0, ["-", 5, ["get", "sev"]]] as unknown as number,
          },
          paint: { "line-color": ["get", "color"], "line-width": width(1) },
        });
        m.addLayer({
          // Stations appear once a line is big enough to tell them apart.
          id: "stations", type: "circle", source: "stations", minzoom: 7,
          paint: {
            "circle-radius": ["interpolate", ["linear"], ["zoom"], 7, 2.5, 10, 4, 13, 5.5],
            "circle-color": BOARD_YELLOW,
            "circle-stroke-color": BOARD_INK,
            "circle-stroke-width": 1.5,
          },
        });
        m.addLayer({
          id: "station-names", type: "symbol", source: "stations", minzoom: 9,
          layout: {
            "text-field": ["get", "name"], "text-font": LABEL_FONT, "text-size": 12,
            "text-offset": [0, 1.1], "text-anchor": "top", "text-optional": true,
          },
          paint: { "text-color": BOARD_YELLOW, "text-halo-color": BOARD_INK, "text-halo-width": 1.6 },
        });

        m.on("mousemove", "sections", (e: MapLayerMouseEvent) => {
          const f = e.features?.[0];
          if (!f) return;
          m.getCanvas().style.cursor = "pointer";
          setHover({ x: e.point.x, y: e.point.y, label: String(f.properties.label), detail: String(f.properties.detail) });
        });
        m.on("mouseleave", "sections", () => {
          m.getCanvas().style.cursor = "";
          setHover(null);
        });
        m.on("click", "sections", (e: MapLayerMouseEvent) => {
          const f = e.features?.[0];
          if (!f) return;
          const incident = String(f.properties.incident);
          if (incident) router.push(`/incident?id=${encodeURIComponent(incident)}`);
          else onSelectRef.current(String(f.properties.key));
        });
        setReady(true);
      };
      m.on("styledata", init);
      m.on("load", init);
      map.current = m;
    })();
    return () => {
      cancelled = true;
    };
  }, [styleUrl, router]);

  useEffect(() => () => map.current?.remove(), []);

  // Push data and keep the view framed on what's in scope.
  useEffect(() => {
    const m = map.current;
    if (!ready || !m) return;
    const { sections, stations } = toGeoJSON(lines);
    (m.getSource("sections") as GeoJSONSource).setData(sections);
    (m.getSource("stations") as GeoJSONSource).setData(stations);
    const scopeKey = lines.map((l) => l.key).join(",");
    if (scopeKey && fittedFor.current !== scopeKey) {
      fittedFor.current = scopeKey;
      const b = boundsOf(lines);
      if (b) m.fitBounds(b as LngLatBoundsLike, { padding: 48, duration: 0, maxZoom: 11 });
    }
  }, [ready, lines]);

  // Dim the other lines and fly to the selected one.
  useEffect(() => {
    const m = map.current;
    if (!ready || !m) return;
    const opacity = selected ? ["case", ["==", ["get", "key"], selected], 1, 0.3] : 1;
    m.setPaintProperty("sections", "line-opacity", opacity as unknown as number);
    m.setPaintProperty("sections-casing", "line-opacity", opacity as unknown as number);
    const line = lines.find((l) => l.key === selected);
    const b = line && boundsOf([line]);
    if (b) m.fitBounds(b as LngLatBoundsLike, { padding: 64, maxZoom: 12, duration: 900 });
    // Re-run only when the selection changes, not on every live update.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, selected]);

  if (!styleUrl) {
    return (
      <div className="map-off">
        <p>Map tiles aren&apos;t configured for this environment.</p>
        <p className="muted">Every line is still listed on the right, and the line diagram below shows its stations.</p>
      </div>
    );
  }
  return (
    <div className="netmap-wrap">
      <div ref={el} className="netmap" role="img" aria-label="Map of every monitored line, coloured by its worst open incident" />
      {hover && (
        <div className="map-tip" style={{ left: hover.x, top: hover.y }} role="presentation">
          <strong>{hover.label}</strong>
          <span>{hover.detail}</span>
        </div>
      )}
    </div>
  );
}
