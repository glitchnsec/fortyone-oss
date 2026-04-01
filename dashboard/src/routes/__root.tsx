import { createRootRouteWithContext, Outlet, redirect } from "@tanstack/react-router";
import { type QueryClient } from "@tanstack/react-query";
import { type AuthContext } from "../lib/auth.tsx";
import { Toaster } from "../components/ui/sonner";

export const Route = createRootRouteWithContext<{ queryClient: QueryClient; auth: AuthContext }>()({
  beforeLoad({ context, location }) {
    // CRITICAL: defer auth gate until AuthProvider has attempted session restore (AUTH-04).
    // Without this guard, every page reload redirects to /auth/login even for valid sessions,
    // because the access token (module-level var) is wiped on reload and the async refresh
    // useEffect hasn't resolved yet.
    if (!context.auth.initialized) return; // still restoring — let the mount effect finish
    if (
      !context.auth.isAuthenticated &&
      !location.pathname.startsWith("/auth") &&
      !location.pathname.startsWith("/onboarding")
    ) {
      throw redirect({ to: "/auth/login" });
    }
  },
  component: () => (
    <>
      <Outlet />
      <Toaster />
    </>
  ),
});
