import type { Metadata, Viewport } from "next";
import { Atkinson_Hyperlegible, Barlow_Condensed, IBM_Plex_Mono } from "next/font/google";
import "maplibre-gl/dist/maplibre-gl.css";
import "./globals.css";

// Signage-style condensed display for km posts and headings; a typeface designed for
// legibility for everything people read under pressure; mono for IDs and clocks.
const display = Barlow_Condensed({ subsets: ["latin"], weight: ["500", "600", "700"], variable: "--font-display" });
const body = Atkinson_Hyperlegible({ subsets: ["latin"], weight: ["400", "700"], variable: "--font-body" });
const mono = IBM_Plex_Mono({ subsets: ["latin"], weight: ["400", "500"], variable: "--font-mono" });

export const metadata: Metadata = {
  title: "RailPulse Ops",
  description: "Track health and incident escalation for Indian Railways divisions",
};

export const viewport: Viewport = { themeColor: "#DDE3DC", width: "device-width", initialScale: 1 };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${display.variable} ${body.variable} ${mono.variable}`}>
      <body>{children}</body>
    </html>
  );
}
