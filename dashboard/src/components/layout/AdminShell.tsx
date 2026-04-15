/**
 * AdminShell -- admin route wrapper using the FortyOne terminal theme.
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
import { ThemeToggle } from "@/components/layout/ThemeToggle";
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
              "flex items-center gap-3 border border-transparent px-3 py-2 text-[12px] uppercase tracking-[0.08em] transition-colors",
              isActive
                ? "border-l-[3px] border-l-primary border-[var(--operator-border-active)] bg-[var(--operator-bg-2)] pl-[calc(0.75rem-3px)] text-primary"
                : "text-muted-foreground hover:border-[var(--operator-border-active)] hover:bg-[var(--operator-bg-2)] hover:text-foreground",
            ].join(" ")}
          >
            {item.icon}
            {item.label}
          </Link>
        );
      })}

      {/* Back to Dashboard link */}
      <div className="mt-auto border-t border-border px-1 pt-3">
        <Link
          to="/"
          onClick={onNavClick}
          className="flex items-center gap-2 border border-transparent px-3 py-2 text-[12px] uppercase tracking-[0.08em] text-muted-foreground transition-colors hover:border-[var(--operator-border-active)] hover:bg-[var(--operator-bg-2)] hover:text-foreground"
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
    <div className="operator-frame flex h-screen overflow-hidden text-foreground">
      {/* Desktop sidebar -- hidden on mobile */}
      <aside className="operator-panel hidden w-60 flex-shrink-0 flex-col border-y-0 border-l-0 md:flex">
        {/* Brand */}
        <div className="flex h-14 items-center border-b border-border px-4">
          <span className="operator-brand">FortyOne <span className="text-muted-foreground">Admin</span></span>
        </div>
        <AdminSidebarNav pathname={pathname} />
      </aside>

      {/* Mobile sidebar -- Sheet overlay */}
      <Sheet open={mobileOpen} onOpenChange={setMobileOpen}>
        <SheetContent side="left" className="operator-panel w-60 p-0" showCloseButton={false}>
          <SheetTitle className="sr-only">Admin Navigation</SheetTitle>
          <div className="flex h-14 items-center border-b border-border px-4">
            <span className="operator-brand">FortyOne <span className="text-muted-foreground">Admin</span></span>
          </div>
          <AdminSidebarNav pathname={pathname} onNavClick={() => setMobileOpen(false)} />
        </SheetContent>
      </Sheet>

      {/* Main content column */}
      <div className="flex flex-1 flex-col overflow-hidden">
        {/* Top bar */}
        <header className="operator-panel flex h-14 flex-shrink-0 items-center justify-between border-x-0 border-t-0 px-4 backdrop-blur-xl">
          {/* Hamburger -- visible only on mobile */}
          <button
            className="border border-border p-1.5 text-muted-foreground transition-colors hover:border-[var(--operator-border-active)] hover:text-foreground focus:outline-none focus:ring-2 focus:ring-primary md:hidden"
            onClick={() => setMobileOpen(true)}
            aria-label="Open admin navigation"
          >
            <Menu className="h-5 w-5" />
          </button>
          {/* Spacer for desktop where hamburger is hidden */}
          <div className="hidden md:block" />

          <div className="flex items-center gap-2">
            <ThemeToggle />
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <button className="rounded-full focus:outline-none focus:ring-2 focus:ring-primary focus:ring-offset-2 focus:ring-offset-background">
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
          </div>
        </header>

        {/* Page content -- wider max-width for admin data views */}
        <main className="flex-1 overflow-y-auto bg-transparent">
          <div className="mx-auto max-w-[1400px] px-6 py-6">
            {children}
          </div>
        </main>
      </div>
    </div>
  );
}
