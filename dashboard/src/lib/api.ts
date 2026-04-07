import { getAccessToken, setAccessToken } from "./auth.tsx";

function redirectToLogin() {
  window.location.href = "/auth/login";
}

// ── Refresh-promise queue ──────────────────────────────────────────────
// When multiple fetchWithAuth calls detect a missing/expired token at the
// same time, only ONE refresh request fires.  All callers await the same
// promise and then retry with the fresh token.
let _refreshPromise: Promise<string | null> | null = null;

async function ensureAccessToken(): Promise<string | null> {
  // Fast path: token already available
  const existing = getAccessToken();
  if (existing) return existing;

  // If a refresh is already in-flight, piggyback on it
  if (_refreshPromise) return _refreshPromise;

  // Start a single refresh attempt
  _refreshPromise = (async () => {
    try {
      const res = await fetch("/auth/refresh", { method: "POST", credentials: "include" });
      if (!res.ok) return null;
      const { access_token } = (await res.json()) as { access_token: string };
      setAccessToken(access_token);
      return access_token;
    } catch {
      return null;
    } finally {
      _refreshPromise = null;
    }
  })();

  return _refreshPromise;
}

export async function fetchWithAuth(url: string, init?: RequestInit): Promise<Response> {
  // Wait for a valid token before firing the request.
  // On page load the module-level _accessToken is null until AuthProvider's
  // refresh effect resolves.  This ensures we never send a request without
  // an Authorization header, eliminating the 403 race.
  let token = await ensureAccessToken();
  const headers = {
    ...(init?.headers ?? {}),
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };

  let res = await fetch(url, { ...init, credentials: "include", headers });

  // Retry on 401 (expired token) or 403 (missing/invalid bearer header).
  // FastAPI's HTTPBearer returns 403 when no Authorization header is present,
  // so we must handle both status codes.
  if (res.status === 401 || res.status === 403) {
    // Force a fresh refresh (clear any cached token so ensureAccessToken
    // doesn't short-circuit on the stale value).
    setAccessToken(null);
    token = await ensureAccessToken();
    if (!token) {
      redirectToLogin();
      throw new Error("Session expired");
    }
    res = await fetch(url, {
      ...init,
      credentials: "include",
      headers: { ...(init?.headers ?? {}), Authorization: `Bearer ${token}` },
    });
  }

  return res;
}
