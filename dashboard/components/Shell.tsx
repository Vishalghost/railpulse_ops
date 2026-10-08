"use client";

import Link from "next/link";
import { createContext, useContext, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { SigningIn, signOut } from "@/lib/auth";
import { DIVISION_NAME } from "@/lib/vocab";
import type { Me } from "@/lib/types";

interface Session {
  me: Me;
  division: string;
  setDivision: (d: string) => void;
}

const SessionContext = createContext<Session | null>(null);

export function useSession(): Session {
  const s = useContext(SessionContext);
  if (!s) throw new Error("useSession outside <Shell>");
  return s;
}

const DIVISION_KEY = "railpulse.division";

export function Shell({ children, status }: { children: React.ReactNode; status?: React.ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [division, setDivisionState] = useState<string>("");

  useEffect(() => {
    api<Me>("/me")
      .then((m) => {
        setMe(m);
        const saved = window.localStorage.getItem(DIVISION_KEY);
        setDivisionState(saved && m.divisions.includes(saved) ? saved : m.divisions[0] ?? "");
      })
      .catch((e) => {
        if (!(e instanceof SigningIn)) setError(e.message);
      });
  }, []);

  const setDivision = (d: string) => {
    window.localStorage.setItem(DIVISION_KEY, d);
    setDivisionState(d);
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

  return (
    <SessionContext.Provider value={{ me, division, setDivision }}>
      <header className="topbar">
        <Link href="/" className="brand">
          <span className="brand-rail" aria-hidden="true" />
          RailPulse
        </Link>
        {me.divisions.length > 1 ? (
          <label className="division">
            <span className="sr-only">Division</span>
            <select value={division} onChange={(e) => setDivision(e.target.value)}>
              {me.divisions.map((d) => (
                <option key={d} value={d}>
                  {DIVISION_NAME[d] ?? d} division
                </option>
              ))}
            </select>
          </label>
        ) : (
          <span className="division">{DIVISION_NAME[division] ?? division} division</span>
        )}
        <span className="topbar-status">{status}</span>
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
