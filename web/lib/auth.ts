"use client";
// Sign-in with Keycloak (OpenID Connect, authorization code + PKCE).
import { UserManager, WebStorageStateStore, type User } from "oidc-client-ts";

export type AppConfig = { authority: string; clientId: string; apiUrl: string };

let configPromise: Promise<AppConfig> | null = null;
let manager: UserManager | null = null;

export function getConfig(): Promise<AppConfig> {
  configPromise ??= fetch("/api/config").then((r) => r.json());
  return configPromise;
}

export async function userManager(): Promise<UserManager> {
  if (manager) return manager;
  const cfg = await getConfig();
  manager = new UserManager({
    authority: cfg.authority,
    client_id: cfg.clientId,
    redirect_uri: `${window.location.origin}/callback`,
    post_logout_redirect_uri: window.location.origin,
    response_type: "code",
    scope: "openid profile email",
    automaticSilentRenew: true,
    userStore: new WebStorageStateStore({ store: window.sessionStorage }),
  });
  return manager;
}

export async function currentUser(): Promise<User | null> {
  const um = await userManager();
  const u = await um.getUser();
  return u && !u.expired ? u : null;
}
