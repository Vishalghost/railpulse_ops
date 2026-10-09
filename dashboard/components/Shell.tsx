"use client";

import Link from "next/link";
import { createContext, useContext, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { SigningIn, signOut } from "@/lib/auth";
import { ALL, DIVISION_NAME } from "@/lib/vocab";
import type { Me } from "@/lib/types";

interface Session {
  me: Me;
  /** A division code, or ALL for every division the user can see. */
  scope: string;
  scopeDivisions: string[];
  setScope: (s: string) => void;
}

const SessionContext = createContext<Session | null>(null);

export function useSession(): Session {
  const s = useContext(SessionContext);
  if (!s) throw new Error("useSession outside <Shell>");
  return s;
}

const SCOPE_KEY = "railpulse.scope";

function readScope(): string | null {
  try {
    return window.localStorage.getItem(SCOPE_KEY);
  } catch {
    return null;
  }
}

export function Shell({ children, status }: { children: React.ReactNode; status?: React.ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [scope, setScopeState] = useState<string>("");

  useEffect(() => {
    api<Me>("/me")
      .then((m) => {
        setMe(m);
        const saved = readScope();
        const valid = saved && (m.divisions.includes(saved) || (saved === ALL && m.divisions.length > 1));
        setScopeState(valid ? saved : m.divisions.length > 1 ? ALL : (m.divisions[0] ?? ""));
      })
      .catch((e) => {
        if (!(e instanceof SigningIn)) setError(e.message);
      });
  }, []);

  const setScope = (s: string) => {
    try {
      window.localStorage.setItem(SCOPE_KEY, s);
    } catch {
      /* private window: the choice just isn't remembered */
    }
    setScopeState(s);
  };

  if (error) {
    return (
      <main className="page narrow">
        <h1 className="display">RailPulse</h1>
        <p role="alert">Couldn&apos;t load your account: {error}. Reload the page to try again.</p>
      </main>
    );
  }
  if (!me) {
    return (
      <main className="page narrow" aria-busy="true">
        <p className="muted">Signing in…</p>
      </main>
    );
  }
  if (me.divisions.length === 0) {
    return (
      <main className="page narrow">
        <h1 className="display">RailPulse</h1>
        <p>Your account isn&apos;t assigned to a division yet. Ask an administrator to add you to a DIV_ group.</p>
        <button className="btn" onClick={() => signOut()}>Sign out</button>
      </main>
    );
  }

  const scopeDivisions = scope === ALL ? me.divisions : [scope];

  return (
    <SessionContext.Provider value={{ me, scope, scopeDivisions, setScope }}>
      <header className="topbar">
        <Link href="/" className="brand" aria-label="RailPulse Ops, overview">
          <span className="brand-board">RailPulse</span>
          <span className="brand-sub">Ops</span>
        </Link>
        {me.divisions.length > 1 ? (
          <label className="scope">
            <span className="sr-only">Show</span>
            <select value={scope} onChange={(e) => setScope(e.target.value)}>
              <option value={ALL}>All divisions</option>
              {me.divisions.map((d) => (
                <option key={d} value={d}>
                  {DIVISION_NAME[d] ?? d} division
                </option>
              ))}
            </select>
          </label>
        ) : (
          <span className="scope">{DIVISION_NAME[scope] ?? scope} division</span>
        )}
        <span className="topbar-status">{status}</span>
        <Clock />
        <span className="who">
          <span className="mono">{me.username}</span>
          <span className="muted">{me.roles.join(", ")}</span>
          <button className="btn btn-quiet" onClick={() => signOut()}>
            Sign out
          </button>
        </span>
      </header>
      {children}
    </SessionContext.Provider>
  );
}

/** Control rooms run on IST whatever the viewer's laptop says. */
function Clock() {
  const [now, setNow] = useState<Date | null>(null);
  useEffect(() => {
    setNow(new Date());
    const t = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(t);
  }, []);
  if (!now) return null;
  const time = now.toLocaleTimeString("en-IN", { timeZone: "Asia/Kolkata", hour12: false });
  return (
    <span className="clock mono" aria-label={`Time ${time} IST`}>
      {time} <span className="muted">IST</span>
    </span>
  );
}
