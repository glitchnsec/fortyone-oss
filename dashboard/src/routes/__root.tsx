import { createRootRouteWithContext, Outlet, redirect, useRouterState } from "@tanstack/react-router";
import { type QueryClient, useQuery } from "@tanstack/react-query";
import { type AuthContext, useAuth } from "../lib/auth.tsx";
import { Toaster } from "../components/ui/sonner";
import { AppShell } from "../components/layout/AppShell";
import { MigrationDialog } from "../components/MigrationDialog";
import { fetchWithAuth } from "../lib/api";

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
  component: RootLayout,
});

function RootLayout() {
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  const isAuth = pathname.startsWith("/auth");
  const isOnboarding = pathname.startsWith("/onboarding");
  const isAdmin = pathname.startsWith("/admin");

  // Auth and onboarding pages render without any shell
  if (isAuth || isOnboarding) {
    return (
      <>
        <Outlet />
        <Toaster />
      </>
    );
  }

  // Admin routes render without the user AppShell — AdminShell will wrap its own routes (Plan 03)
  if (isAdmin) {
    return (
      <>
        <Outlet />
        <Toaster />
      </>
    );
  }

  return (
    <AppShell>
      <Outlet />
      <MigrationDialogWrapper />
      <Toaster />
    </AppShell>
  );
}

/**
 * Wrapper that fetches connections + personas and conditionally renders
 * the migration dialog when there are unassigned connections (persona_id === null).
 * Only renders when the user is authenticated.
 */
function MigrationDialogWrapper() {
  const { isAuthenticated } = useAuth();

  const { data: connectionsData } = useQuery({
    queryKey: ["connections"],
    queryFn: () => fetchWithAuth("/api/v1/connections").then((r) => r.json()),
    enabled: isAuthenticated,
  });

  const { data: personasData } = useQuery({
    queryKey: ["personas"],
    queryFn: () => fetchWithAuth("/api/v1/personas").then((r) => r.json()),
    enabled: isAuthenticated,
  });

  const connections = connectionsData?.connections ?? [];
  const personas = personasData?.personas ?? [];
  const unassigned = connections.filter(
    (c: { persona_id: string | null }) => !c.persona_id
  );

  if (unassigned.length === 0 || personas.length === 0) return null;

  return <MigrationDialog connections={unassigned} personas={personas} />;
}
