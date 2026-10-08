"use client";

import { useEffect, useState } from "react";
import { completeSignIn } from "@/lib/auth";

export default function Callback() {
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    completeSignIn()
      .then((back) => window.location.replace(back))
      .catch((e: Error) => setError(e.message));
  }, []);
  return (
    <main className="page narrow">
      {error ? (
        <p role="alert">
          Sign-in didn&apos;t complete: {error}. <a href="/">Start again</a>.
        </p>
      ) : (
        <p className="muted">Completing sign-in…</p>
      )}
    </main>
  );
}
