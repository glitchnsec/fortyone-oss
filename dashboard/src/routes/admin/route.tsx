/**
 * Admin layout route -- wraps all /admin/* pages in AdminShell with auth guard.
 *
 * beforeLoad checks:
 *   1. Auth initialized (wait for session restore)
 *   2. User is authenticated (redirect to /auth/login if not)
 *   3. User has admin role (redirect to / if not)
 */
import { createFileRoute, Outlet, redirect } from "@tanstack/react-router";
import { AdminShell } from "@/components/layout/AdminShell";

export const Route = createFileRoute("/admin")({
  beforeLoad({ context }) {
    if (!context.auth.initialized) return;
    if (!context.auth.isAuthenticated) {
      throw redirect({ to: "/auth/login" });
    }
    if (context.auth.role !== "admin") {
      throw redirect({ to: "/" });
    }
  },
  component: AdminLayout,
});

function AdminLayout() {
  return (
    <AdminShell>
      <Outlet />
    </AdminShell>
  );
}
