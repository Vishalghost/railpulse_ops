import { UserManager, WebStorageStateStore } from "oidc-client-ts";
import { loadConfig } from "./config";

let manager: Promise<UserManager> | null = null;

/** Cognito Hosted UI, authorization code + PKCE. Tokens live in sessionStorage (cleared with the tab). */
export function userManager(): Promise<UserManager> {
  manager ??= loadConfig().then(
    (c) =>
      new UserManager({
        authority: `https://cognito-idp.${c.region}.amazonaws.com/${c.userPoolId}`,
        client_id: c.clientId,
        redirect_uri: `${window.location.origin}/callback`,
        response_type: "code",
        scope: "openid email profile",
        automaticSilentRenew: true, // uses the refresh token; Cognito has no iframe renew
        userStore: new WebStorageStateStore({ store: window.sessionStorage }),
      }),
  );
  return manager;
}

export class SigningIn extends Error {
  constructor() {
    super("Redirecting to sign in");
  }
}

export async function accessToken(): Promise<string> {
  const mgr = await userManager();
  let user = await mgr.getUser();
  if (user?.expired) {
    user = await mgr.signinSilent().catch(() => null);
  }
  if (!user) {
    await mgr.signinRedirect({ state: window.location.pathname + window.location.search });
    throw new SigningIn();
  }
  return user.access_token;
}

export async function completeSignIn(): Promise<string> {
  const mgr = await userManager();
  const user = await mgr.signinCallback();
  const back = typeof user?.state === "string" ? user.state : "/";
  return back.startsWith("/") && !back.startsWith("//") ? back : "/"; // no open redirects
}

export async function signOut(): Promise<void> {
  const [c, mgr] = await Promise.all([loadConfig(), userManager()]);
  await mgr.removeUser();
  // Cognito's logout endpoint isn't in its OIDC discovery document, so build it by hand.
  const back = encodeURIComponent(`${window.location.origin}/`);
  window.location.assign(`${c.cognitoDomain}/logout?client_id=${c.clientId}&logout_uri=${back}`);
}
