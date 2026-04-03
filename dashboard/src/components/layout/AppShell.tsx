/**
 * AppShell — authenticated route wrapper with fixed 240px sidebar and top bar.
 *
 * Layout:
 *   - Fixed left sidebar: 240px wide, neutral-50 background
 *   - Top bar: 56px height, white background, 1px bottom border
 *   - Main content: remaining width, white background, overflow-y auto
 *
 * Nav items (D-11):
 *   Connections /connections | Conversations /conversations |
 *   Assistant /settings/assistant | Account /settings/account
 *
 * Active state: 3px blue-600 left border + blue-600 text + neutral-100 bg
 */
import { type ReactNode } from "react";
import { Link, useRouterState } from "@tanstack/react-router";
import {
  Link2,
  MessageSquare,
  Bot,
  UserCircle,
  LogOut,
  Target,
  Clock,
  Users,
  ListTodo,
} from "lucide-react";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useAuth } from "@/lib/auth.tsx";
import { fetchWithAuth } from "@/lib/api";
import { useNavigate } from "@tanstack/react-router";

interface NavItem {
  label: string;
  to: string;
  icon: ReactNode;
}

const NAV_ITEMS: NavItem[] = [
  { label: "Connections", to: "/connections", icon: <Link2 className="h-4 w-4" /> },
  { label: "Conversations", to: "/conversations", icon: <MessageSquare className="h-4 w-4" /> },
  { label: "Tasks", to: "/tasks", icon: <ListTodo className="h-4 w-4" /> },
  { label: "Goals", to: "/goals", icon: <Target className="h-4 w-4" /> },
  { label: "Activity", to: "/actions", icon: <Clock className="h-4 w-4" /> },
  { label: "Profile", to: "/profile", icon: <UserCircle className="h-4 w-4" /> },
  { label: "Assistant", to: "/settings/assistant", icon: <Bot className="h-4 w-4" /> },
  { label: "Personas", to: "/settings/personas", icon: <Users className="h-4 w-4" /> },
  { label: "Account", to: "/settings/account", icon: <UserCircle className="h-4 w-4" /> },
];

export function AppShell({ children }: { children: ReactNode }) {
  const { logout } = useAuth();
  const navigate = useNavigate();
  const pathname = useRouterState({ select: (s) => s.location.pathname });

  const handleLogout = async () => {
    try {
      await fetchWithAuth("/auth/logout", { method: "POST" });
    } catch {
      // Ignore errors — we're logging out regardless
    }
    logout();
    navigate({ to: "/auth/login" });
  };

  return (
    <div className="flex h-screen overflow-hidden bg-white">
      {/* Sidebar */}
      <aside className="flex w-60 flex-shrink-0 flex-col bg-neutral-50 border-r border-neutral-200">
        {/* Logo / Brand */}
        <div className="flex h-14 items-center px-4 border-b border-neutral-200">
          <span className="text-base font-semibold text-neutral-900">Operator</span>
        </div>

        {/* Navigation */}
        <nav className="flex flex-1 flex-col gap-1 px-2 py-3">
          {NAV_ITEMS.map((item) => {
            const isActive = pathname.startsWith(item.to);
            return (
              <Link
                key={item.to}
                to={item.to}
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
        </nav>
      </aside>

      {/* Main content column */}
      <div className="flex flex-1 flex-col overflow-hidden">
        {/* Top bar */}
        <header className="flex h-14 flex-shrink-0 items-center justify-end border-b border-neutral-200 bg-white px-4">
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <button className="rounded-full focus:outline-none focus:ring-2 focus:ring-blue-600 focus:ring-offset-2">
                <Avatar>
                  <AvatarFallback>U</AvatarFallback>
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

        {/* Page content */}
        <main className="flex-1 overflow-y-auto bg-white">
          {children}
        </main>
      </div>
    </div>
  );
}
