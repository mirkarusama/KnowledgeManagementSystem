"use client";
import { useEffect, useState } from "react";
import { userManager } from "@/lib/auth";

export default function Callback() {
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    userManager()
      .then((um) => um.signinRedirectCallback())
      .then(() => window.location.replace("/"))
      .catch((e) => setError(String(e)));
  }, []);
  return <main className="center">{error ? <p className="error">Sign-in failed: {error}</p> : <p>Signing you in…</p>}</main>;
}
