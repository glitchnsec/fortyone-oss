import { getAccessToken, setAccessToken } from "./auth.tsx";

function redirectToLogin() {
  window.location.href = "/auth/login";
}

export async function fetchWithAuth(url: string, init?: RequestInit): Promise<Response> {
  const token = getAccessToken();
  const headers = {
    ...(init?.headers ?? {}),
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };
  let res = await fetch(url, { ...init, credentials: "include", headers });
  if (res.status === 401) {
    const refreshed = await fetch("/auth/refresh", { method: "POST", credentials: "include" });
    if (!refreshed.ok) {
      redirectToLogin();
      throw new Error("Session expired");
    }
    const { access_token } = (await refreshed.json()) as { access_token: string };
    setAccessToken(access_token);
    res = await fetch(url, {
      ...init,
      credentials: "include",
      headers: { ...headers, Authorization: `Bearer ${access_token}` },
    });
  }
  return res;
}
