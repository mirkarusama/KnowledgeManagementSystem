"use client";
import { useEffect, useState } from "react";
import type { User } from "oidc-client-ts";
import { currentUser, userManager } from "@/lib/auth";
import Chat from "./Chat";

export default function Home() {
  const [user, setUser] = useState<User | null | undefined>(undefined);
  useEffect(() => { currentUser().then(setUser).catch(() => setUser(null)); }, []);

  if (user === undefined) return <main className="center"><p>Loading…</p></main>;
  if (!user)
    return (
      <main className="center">
        <div className="signin">
          <h1>Knowledge Assistant</h1>
          <p>Ask questions about our Confluence pages and Jira tickets. You only ever see answers built from content you already have access to.</p>
          <button className="primary" onClick={async () => (await userManager()).signinRedirect()}>Sign in</button>
          <p className="hint">Demo users: alice, bob, carol, dave, erin · password: <code>password</code></p>
        </div>
      </main>
    );
  return <Chat user={user} />;
}
