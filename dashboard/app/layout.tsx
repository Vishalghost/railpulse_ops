import type { Metadata, Viewport } from "next";
import { Atkinson_Hyperlegible, Big_Shoulders, IBM_Plex_Mono } from "next/font/google";
import "maplibre-gl/dist/maplibre-gl.css";
import "./globals.css";

// Big Shoulders: a condensed face drawn from transit signage, for headings, numbers and
// station boards. Atkinson Hyperlegible for everything people read under pressure. Mono
// for IDs, km and clocks.
const display = Big_Shoulders({ subsets: ["latin"], weight: ["600", "700", "800"], variable: "--font-display" });
const body = Atkinson_Hyperlegible({ subsets: ["latin"], weight: ["400", "700"], variable: "--font-body" });
const mono = IBM_Plex_Mono({ subsets: ["latin"], weight: ["400", "500"], variable: "--font-mono" });

export const metadata: Metadata = {
  title: "RailPulse Ops",
  description: "Live track health and incident escalation across Indian Railways divisions",
};

export const viewport: Viewport = { themeColor: "#0D1520", colorScheme: "dark", width: "device-width", initialScale: 1 };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${display.variable} ${body.variable} ${mono.variable}`}>
      <body>{children}</body>
    </html>
  );
}
