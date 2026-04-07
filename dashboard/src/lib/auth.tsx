import { createContext, useContext, useState, useEffect, type ReactNode } from "react";

export interface AuthContext {
  isAuthenticated: boolean;
  userId: string | null;
  role: string | null;
  initialized: boolean; // true once the mount refresh attempt has resolved
  login: (accessToken: string, userId: string, role?: string) => void;
  logout: () => void;
}

const Ctx = createContext<AuthContext | null>(null);

// Access token lives in module-level variable — NOT state, NOT localStorage (XSS risk)
let _accessToken: string | null = null;
export const getAccessToken = () => _accessToken;
export const setAccessToken = (t: string | null) => {
  _accessToken = t;
};

export function AuthProvider({ children }: { children: ReactNode }) {
  const [userId, setUserId] = useState<string | null>(null);
  const [role, setRole] = useState<string | null>(null);
  const [initialized, setInitialized] = useState(false);

  // CRITICAL: On mount, attempt to restore the session from the httpOnly refresh cookie.
  // If the cookie is valid, POST /auth/refresh returns a new access token and userId.
  // The auth guard in __root.tsx must NOT run until initialized=true to prevent
  // spurious redirects to /auth/login on every page reload (AUTH-04).
  useEffect(() => {
    (async () => {
      try {
        const res = await fetch("/auth/refresh", { method: "POST", credentials: "include" });
        if (res.ok) {
          const { access_token } = await res.json() as { access_token: string };
          setAccessToken(access_token);
          const me = await fetch("/api/v1/me", {
            headers: { Authorization: `Bearer ${access_token}` },
            credentials: "include",
          });
          if (me.ok) {
            const data = await me.json() as { user_id: string; role?: string };
            setUserId(data.user_id);
            setRole(data.role ?? "user");
          }
        }
      } catch {
        // Network error or no cookie — treat as unauthenticated
      } finally {
        setInitialized(true);
      }
    })();
  }, []);

  const login = (accessToken: string, uid: string, userRole?: string) => {
    setAccessToken(accessToken);
    setUserId(uid);
    setRole(userRole ?? "user");
  };

  const logout = () => {
    setAccessToken(null);
    setUserId(null);
    setRole(null);
  };

  return (
    <Ctx.Provider value={{ isAuthenticated: !!userId, userId, role, initialized, login, logout }}>
      {children}
    </Ctx.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useAuth must be inside AuthProvider");
  return ctx;
}
