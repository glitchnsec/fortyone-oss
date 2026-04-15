/**
 * AdminShell -- admin route wrapper with fixed 240px sidebar and top bar.
 *
 * Layout mirrors AppShell but with admin-specific nav items, "FortyOne Admin"
 * branding, wider content area (max-w-[1400px]), and a "Back to Dashboard" link.
 *
 * Nav items: Overview (/admin), Users (/admin/users), System Health (/admin/health)
 * Active state: 3px blue-600 left border + blue-600 text + neutral-100 bg
 */
import { type ReactNode, useState } from "react";
import { Link, useRouterState } from "@tanstack/react-router";
import {
  BarChart3,
  Users,
  Activity,
  Zap,
  LogOut,
  Menu,
  ArrowLeft,
} from "lucide-react";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Sheet,
  SheetContent,
  SheetTitle,
} from "@/components/ui/sheet";
import { useAuth } from "@/lib/auth.tsx";
import { fetchWithAuth } from "@/lib/api";
import { useNavigate } from "@tanstack/react-router";

interface NavItem {
  label: string;
  to: string;
  icon: ReactNode;
}

const ADMIN_NAV: NavItem[] = [
  { label: "Overview", to: "/admin", icon: <BarChart3 className="h-4 w-4" /> },
  { label: "Users", to: "/admin/users", icon: <Users className="h-4 w-4" /> },
  { label: "Proactivity", to: "/admin/proactivity", icon: <Zap className="h-4 w-4" /> },
  { label: "System Health", to: "/admin/health", icon: <Activity className="h-4 w-4" /> },
];

function AdminSidebarNav({
  pathname,
  onNavClick,
}: {
  pathname: string;
  onNavClick?: () => void;
}) {
  return (
    <nav className="flex flex-1 flex-col gap-1 px-2 py-3">
      {ADMIN_NAV.map((item) => {
        // Exact match for /admin (Overview), startsWith for sub-pages
        const isActive =
          item.to === "/admin"
            ? pathname === "/admin" || pathname === "/admin/"
            : pathname.startsWith(item.to);
        return (
          <Link
            key={item.to}
            to={item.to}
            onClick={onNavClick}
            className={[
              "flex items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors",
              isActive
                ? "border-l-[3px] border-blue-600 bg-neutral-100 pl-[calc(0.75rem-3px)] text-blue-600"
                : "text-neutral-700 hover:bg-neutral-100",
            ].join(" ")}
          >
            {item.icon}
            {item.label}
          </Link>
        );
      })}

      {/* Back to Dashboard link */}
      <div className="mt-auto border-t border-neutral-200 pt-3 px-1">
        <Link
          to="/"
          onClick={onNavClick}
          className="flex items-center gap-2 rounded-md px-3 py-2 text-sm text-neutral-500 hover:text-neutral-700 hover:bg-neutral-100 transition-colors"
        >
          <ArrowLeft className="h-4 w-4" />
          Back to Dashboard
        </Link>
      </div>
    </nav>
  );
}

export function AdminShell({ children }: { children: ReactNode }) {
  const { logout } = useAuth();
  const navigate = useNavigate();
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  const [mobileOpen, setMobileOpen] = useState(false);

  const handleLogout = async () => {
    try {
      await fetchWithAuth("/auth/logout", { method: "POST" });
    } catch {
      // Ignore errors -- we're logging out regardless
    }
    logout();
    navigate({ to: "/auth/login" });
  };

  return (
    <div className="flex h-screen overflow-hidden bg-white">
      {/* Desktop sidebar -- hidden on mobile */}
      <aside className="hidden md:flex w-60 flex-shrink-0 flex-col bg-neutral-50 border-r border-neutral-200">
        {/* Brand */}
        <div className="flex h-14 items-center px-4 border-b border-neutral-200">
          <span className="text-base font-semibold text-neutral-900">FortyOne Admin</span>
        </div>
        <AdminSidebarNav pathname={pathname} />
      </aside>

      {/* Mobile sidebar -- Sheet overlay */}
      <Sheet open={mobileOpen} onOpenChange={setMobileOpen}>
        <SheetContent side="left" className="w-60 p-0 bg-neutral-50" showCloseButton={false}>
          <SheetTitle className="sr-only">Admin Navigation</SheetTitle>
          <div className="flex h-14 items-center px-4 border-b border-neutral-200">
            <span className="text-base font-semibold text-neutral-900">FortyOne Admin</span>
          </div>
          <AdminSidebarNav pathname={pathname} onNavClick={() => setMobileOpen(false)} />
        </SheetContent>
      </Sheet>

      {/* Main content column */}
      <div className="flex flex-1 flex-col overflow-hidden">
        {/* Top bar */}
        <header className="flex h-14 flex-shrink-0 items-center justify-between border-b border-neutral-200 bg-white px-4">
          {/* Hamburger -- visible only on mobile */}
          <button
            className="md:hidden rounded-md p-1.5 text-neutral-700 hover:bg-neutral-100 focus:outline-none focus:ring-2 focus:ring-blue-600"
            onClick={() => setMobileOpen(true)}
            aria-label="Open admin navigation"
          >
            <Menu className="h-5 w-5" />
          </button>
          {/* Spacer for desktop where hamburger is hidden */}
          <div className="hidden md:block" />

          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <button className="rounded-full focus:outline-none focus:ring-2 focus:ring-blue-600 focus:ring-offset-2">
                <Avatar>
                  <AvatarFallback>A</AvatarFallback>
                </Avatar>
              </button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem onClick={handleLogout} className="text-red-600 focus:text-red-600">
                <LogOut className="mr-2 h-4 w-4" />
                Log out
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </header>

        {/* Page content -- wider max-width for admin data views */}
        <main className="flex-1 overflow-y-auto bg-white">
          <div className="mx-auto max-w-[1400px] px-6 py-6">
            {children}
          </div>
        </main>
      </div>
    </div>
  );
}
