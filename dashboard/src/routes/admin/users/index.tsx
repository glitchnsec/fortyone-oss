/**
 * Admin Users list page -- search, filter, and paginated table of all users.
 *
 * Fetches GET /api/v1/admin/users?page=1&limit=20&search=foo&status=all
 * Features: 300ms debounced search, status filter, role/status badges,
 * skeleton loading, empty state, pagination, row-click navigation.
 */
import { useState, useEffect } from "react";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { Search, Users } from "lucide-react";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { fetchWithAuth } from "@/lib/api";

export const Route = createFileRoute("/admin/users/")({
  component: UsersListPage,
});

// --- Types -------------------------------------------------------------------

interface UserRow {
  id: string;
  email: string;
  phone: string | null;
  role: "admin" | "user";
  status: "active" | "suspended" | "deleted";
  created_at: string;
  last_seen_at: string | null;
}

interface UsersResponse {
  users: UserRow[];
  total: number;
  page: number;
  limit: number;
}

const LIMIT = 20;

// --- Page --------------------------------------------------------------------

function UsersListPage() {
  const navigate = useNavigate();
  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [status, setStatus] = useState("all");
  const [page, setPage] = useState(1);

  // 300ms debounce for search input
  useEffect(() => {
    const timer = setTimeout(() => {
      setDebouncedSearch(search);
      setPage(1);
    }, 300);
    return () => clearTimeout(timer);
  }, [search]);

  // Reset page on filter change
  useEffect(() => {
    setPage(1);
  }, [status]);

  const { data, isLoading } = useQuery<UsersResponse>({
    queryKey: ["admin", "users", debouncedSearch, status, page],
    queryFn: () =>
      fetchWithAuth(
        `/api/v1/admin/users?page=${page}&limit=${LIMIT}&search=${encodeURIComponent(debouncedSearch)}&status=${status}`
      ).then((r) => r.json()) as Promise<UsersResponse>,
  });

  const users = data?.users ?? [];
  const total = data?.total ?? 0;
  const totalPages = Math.ceil(total / LIMIT);
  const start = (page - 1) * LIMIT + 1;
  const end = Math.min(page * LIMIT, total);

  return (
    <div className="mx-auto max-w-6xl px-4 sm:px-6 py-6 sm:py-8">
      <h1 className="mb-6 text-xl font-semibold text-neutral-900">Users</h1>

      {/* Search + filter row */}
      <div className="flex gap-4 items-center mb-6">
        <div className="relative max-w-sm flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-neutral-400" />
          <Input
            placeholder="Search users..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="pl-9 max-w-sm"
          />
        </div>
        <Select value={status} onValueChange={setStatus}>
          <SelectTrigger className="w-[140px]">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All</SelectItem>
            <SelectItem value="active">Active</SelectItem>
            <SelectItem value="suspended">Suspended</SelectItem>
            <SelectItem value="deleted">Deleted</SelectItem>
          </SelectContent>
        </Select>
      </div>

      {/* Table */}
      {isLoading ? (
        <LoadingSkeleton />
      ) : users.length === 0 ? (
        <EmptyState hasSearch={debouncedSearch.length > 0 || status !== "all"} />
      ) : (
        <>
          <div className="overflow-x-auto rounded-md border border-neutral-200">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Email</TableHead>
                  <TableHead className="hidden sm:table-cell">Phone</TableHead>
                  <TableHead>Role</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead className="hidden md:table-cell">Created</TableHead>
                  <TableHead className="hidden md:table-cell">Last Active</TableHead>
                  <TableHead className="w-[80px]">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {users.map((user) => (
                  <TableRow
                    key={user.id}
                    className="cursor-pointer"
                    onClick={() => navigate({ to: `/admin/users/${user.id}` })}
                  >
                    <TableCell className="text-sm text-neutral-800">
                      {user.email}
                    </TableCell>
                    <TableCell className="hidden sm:table-cell text-sm text-neutral-600">
                      {user.phone ?? "---"}
                    </TableCell>
                    <TableCell>
                      <RoleBadge role={user.role} />
                    </TableCell>
                    <TableCell>
                      <StatusBadge status={user.status} />
                    </TableCell>
                    <TableCell className="hidden md:table-cell text-sm text-neutral-500">
                      {formatDate(user.created_at)}
                    </TableCell>
                    <TableCell className="hidden md:table-cell text-sm text-neutral-500">
                      {user.last_seen_at ? formatRelative(user.last_seen_at) : "Never"}
                    </TableCell>
                    <TableCell>
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={(e) => {
                          e.stopPropagation();
                          navigate({ to: `/admin/users/${user.id}` });
                        }}
                      >
                        View
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>

          {/* Pagination */}
          {total > 0 && (
            <div className="mt-4 flex items-center justify-between">
              <span className="text-sm text-neutral-500">
                Showing {start}-{end} of {total} users
              </span>
              <div className="flex gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setPage((p) => Math.max(1, p - 1))}
                  disabled={page === 1}
                >
                  Previous
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                  disabled={page >= totalPages}
                >
                  Next
                </Button>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}

// --- Components --------------------------------------------------------------

function RoleBadge({ role }: { role: "admin" | "user" }) {
  if (role === "admin") {
    return (
      <Badge variant="default" className="bg-blue-100 text-blue-700 hover:bg-blue-100">
        admin
      </Badge>
    );
  }
  return <Badge variant="secondary">user</Badge>;
}

function StatusBadge({ status }: { status: "active" | "suspended" | "deleted" }) {
  switch (status) {
    case "active":
      return <Badge variant="secondary">active</Badge>;
    case "suspended":
      return (
        <Badge className="bg-amber-100 text-amber-700 hover:bg-amber-100">
          suspended
        </Badge>
      );
    case "deleted":
      return <Badge variant="destructive">deleted</Badge>;
    default:
      return <Badge variant="outline">{status}</Badge>;
  }
}

function LoadingSkeleton() {
  return (
    <div className="rounded-md border border-neutral-200">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Email</TableHead>
            <TableHead className="hidden sm:table-cell">Phone</TableHead>
            <TableHead>Role</TableHead>
            <TableHead>Status</TableHead>
            <TableHead className="hidden md:table-cell">Created</TableHead>
            <TableHead className="hidden md:table-cell">Last Active</TableHead>
            <TableHead className="w-[80px]">Actions</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {Array.from({ length: 5 }).map((_, i) => (
            <TableRow key={i}>
              <TableCell><Skeleton className="h-4 w-40" /></TableCell>
              <TableCell className="hidden sm:table-cell"><Skeleton className="h-4 w-28" /></TableCell>
              <TableCell><Skeleton className="h-5 w-14 rounded-full" /></TableCell>
              <TableCell><Skeleton className="h-5 w-16 rounded-full" /></TableCell>
              <TableCell className="hidden md:table-cell"><Skeleton className="h-4 w-24" /></TableCell>
              <TableCell className="hidden md:table-cell"><Skeleton className="h-4 w-24" /></TableCell>
              <TableCell><Skeleton className="h-8 w-14" /></TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

function EmptyState({ hasSearch }: { hasSearch: boolean }) {
  return (
    <div className="flex flex-col items-center gap-3 py-16 text-center">
      <Users className="h-10 w-10 text-neutral-300" />
      <h2 className="text-lg font-semibold text-neutral-900">No users found</h2>
      {hasSearch && (
        <p className="max-w-xs text-sm text-neutral-500">
          No users match your search. Try a different email or phone number.
        </p>
      )}
    </div>
  );
}

// --- Helpers -----------------------------------------------------------------

function formatDate(iso: string): string {
  try {
    return new Intl.DateTimeFormat("en-US", {
      month: "short",
      day: "numeric",
      year: "numeric",
    }).format(new Date(iso));
  } catch {
    return iso;
  }
}

function formatRelative(iso: string): string {
  try {
    const date = new Date(iso);
    const now = new Date();
    const diffMs = now.getTime() - date.getTime();
    const diffMins = Math.floor(diffMs / 60000);
    if (diffMins < 1) return "Just now";
    if (diffMins < 60) return `${diffMins}m ago`;
    const diffHours = Math.floor(diffMins / 60);
    if (diffHours < 24) return `${diffHours}h ago`;
    const diffDays = Math.floor(diffHours / 24);
    if (diffDays < 7) return `${diffDays}d ago`;
    return formatDate(iso);
  } catch {
    return iso;
  }
}
