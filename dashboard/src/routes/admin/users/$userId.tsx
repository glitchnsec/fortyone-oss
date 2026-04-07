/**
 * Admin User detail page -- profile, activity, connections, impersonate tabs
 * with suspend/restore/delete/purge action buttons and AlertDialog confirmations.
 *
 * Fetches:
 * - GET /api/v1/admin/users/{userId} (detail)
 * - GET /api/v1/admin/users/{userId}/activity (activity tab)
 * - GET /api/v1/admin/users/{userId}/connections (connections tab)
 *
 * Mutations: suspend, restore, soft-delete, hard-purge with toast feedback.
 */
import { useState } from "react";
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  AlertCircle,
  User as UserIcon,
  Shield,
} from "lucide-react";
import { toast } from "sonner";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import { fetchWithAuth } from "@/lib/api";

export const Route = createFileRoute("/admin/users/$userId")({
  component: UserDetailPage,
});

// --- Types -------------------------------------------------------------------

interface UserDetail {
  id: string;
  email: string;
  phone: string | null;
  name: string | null;
  timezone: string | null;
  role: "admin" | "user";
  status: "active" | "suspended" | "deleted";
  assistant_name: string | null;
  personality_notes: string | null;
  phone_verified: boolean;
  created_at: string;
  last_seen_at: string | null;
  deleted_at: string | null;
  suspended_at: string | null;
  message_count: number;
  task_count: number;
  goal_count: number;
  memory_count: number;
}

interface ActivityItem {
  id: string;
  created_at: string;
  direction: "inbound" | "outbound";
  body: string;
  channel: string | null;
  intent: string | null;
}

interface ActivityResponse {
  activity: ActivityItem[];
  total: number;
}

interface ConnectionItem {
  provider: string;
  status: string;
}

interface ConnectionsResponse {
  connections: ConnectionItem[];
}

// --- Page --------------------------------------------------------------------

function UserDetailPage() {
  const { userId } = Route.useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [activityPage, setActivityPage] = useState(1);

  // --- Queries ---------------------------------------------------------------

  const detail = useQuery<UserDetail>({
    queryKey: ["admin", "user", userId],
    queryFn: () =>
      fetchWithAuth(`/api/v1/admin/users/${userId}`).then((r) => {
        if (!r.ok) throw new Error("not-found");
        return r.json();
      }) as Promise<UserDetail>,
  });

  const activity = useQuery<ActivityResponse>({
    queryKey: ["admin", "user-activity", userId, activityPage],
    queryFn: () =>
      fetchWithAuth(
        `/api/v1/admin/users/${userId}/activity?page=${activityPage}&limit=50`
      ).then((r) => r.json()) as Promise<ActivityResponse>,
  });

  const connections = useQuery<ConnectionsResponse>({
    queryKey: ["admin", "user-connections", userId],
    queryFn: () =>
      fetchWithAuth(`/api/v1/admin/users/${userId}/connections`).then((r) =>
        r.json()
      ) as Promise<ConnectionsResponse>,
  });

  // --- Mutations -------------------------------------------------------------

  const suspendUser = useMutation({
    mutationFn: () =>
      fetchWithAuth(`/api/v1/admin/users/${userId}/suspend`, {
        method: "POST",
      }),
    onSuccess: () => {
      toast.success("User suspended.");
      queryClient.invalidateQueries({ queryKey: ["admin", "user", userId] });
    },
  });

  const restoreUser = useMutation({
    mutationFn: () =>
      fetchWithAuth(`/api/v1/admin/users/${userId}/restore`, {
        method: "POST",
      }),
    onSuccess: () => {
      toast.success("User access restored.");
      queryClient.invalidateQueries({ queryKey: ["admin", "user", userId] });
    },
  });

  const deleteUser = useMutation({
    mutationFn: () =>
      fetchWithAuth(`/api/v1/admin/users/${userId}`, { method: "DELETE" }),
    onSuccess: () => {
      toast.success("User deleted.");
      queryClient.invalidateQueries({ queryKey: ["admin", "user", userId] });
    },
  });

  const purgeUser = useMutation({
    mutationFn: () =>
      fetchWithAuth(`/api/v1/admin/users/${userId}/purge`, {
        method: "DELETE",
      }),
    onSuccess: () => {
      toast.success("User and all data permanently removed.");
      navigate({ to: "/admin/users" });
    },
  });

  // --- Error state -----------------------------------------------------------

  if (detail.isError) {
    return (
      <div className="mx-auto max-w-4xl px-4 sm:px-6 py-6 sm:py-8">
        <Link
          to="/admin/users"
          className="inline-flex items-center gap-1 text-sm text-neutral-500 hover:underline mb-6"
        >
          <ArrowLeft className="h-4 w-4" /> Back to Users
        </Link>
        <div className="flex flex-col items-center gap-3 py-16 text-center">
          <UserIcon className="h-10 w-10 text-neutral-300" />
          <p className="text-sm text-neutral-500">
            Unable to load user details. The user may have been deleted.
          </p>
        </div>
      </div>
    );
  }

  // --- Loading state ---------------------------------------------------------

  if (detail.isLoading || !detail.data) {
    return (
      <div className="mx-auto max-w-4xl px-4 sm:px-6 py-6 sm:py-8">
        <Skeleton className="h-4 w-32 mb-6" />
        <Skeleton className="h-32 w-full mb-6" />
        <Skeleton className="h-8 w-64 mb-4" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  const user = detail.data;

  return (
    <div className="mx-auto max-w-4xl px-4 sm:px-6 py-6 sm:py-8">
      {/* Back link */}
      <Link
        to="/admin/users"
        className="inline-flex items-center gap-1 text-sm text-neutral-500 hover:underline mb-6"
      >
        <ArrowLeft className="h-4 w-4" /> Back to Users
      </Link>

      {/* User header card */}
      <Card className="mb-6">
        <CardHeader>
          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
            <div className="flex items-center gap-4">
              {/* Avatar fallback */}
              <div className="flex h-12 w-12 items-center justify-center rounded-full bg-neutral-100 text-neutral-600 text-lg font-semibold">
                {user.email?.charAt(0)?.toUpperCase() ?? "?"}
              </div>
              <div>
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="text-lg font-semibold text-neutral-900">
                    {user.email}
                  </span>
                  <RoleBadge role={user.role} />
                  <StatusBadge status={user.status} />
                </div>
                <div className="text-sm text-neutral-500 mt-1">
                  {user.phone ?? "No phone"} &middot; Joined{" "}
                  {formatDate(user.created_at)}
                  {user.last_seen_at &&
                    ` \u00B7 Last active ${formatDate(user.last_seen_at)}`}
                </div>
              </div>
            </div>

            {/* Action buttons */}
            <div className="flex gap-2 flex-wrap">
              {user.status === "active" && (
                <>
                  <ConfirmDialog
                    title="Suspend this user?"
                    description="This user will be unable to log in or send messages. You can restore access at any time."
                    actionLabel="Suspend User"
                    onConfirm={() => suspendUser.mutate()}
                    variant="outline"
                    triggerClassName="text-amber-600 border-amber-600"
                  />
                  <ConfirmDialog
                    title="Delete this user?"
                    description="This user will be marked as deleted and lose all access. Their data is preserved for the grace period."
                    actionLabel="Delete User"
                    onConfirm={() => deleteUser.mutate()}
                    variant="outline"
                    triggerClassName="text-red-600 border-red-600"
                  />
                </>
              )}
              {user.status === "suspended" && (
                <>
                  <ConfirmDialog
                    title="Restore this user?"
                    description="This user will regain login and messaging access immediately."
                    actionLabel="Restore User"
                    onConfirm={() => restoreUser.mutate()}
                    variant="outline"
                  />
                  <ConfirmDialog
                    title="Delete this user?"
                    description="This user will be marked as deleted and lose all access. Their data is preserved for the grace period."
                    actionLabel="Delete User"
                    onConfirm={() => deleteUser.mutate()}
                    variant="outline"
                    triggerClassName="text-red-600 border-red-600"
                  />
                </>
              )}
              {user.status === "deleted" && (
                <>
                  <ConfirmDialog
                    title="Restore this user?"
                    description="This user will regain login and messaging access immediately."
                    actionLabel="Restore User"
                    onConfirm={() => restoreUser.mutate()}
                    variant="outline"
                  />
                  <ConfirmDialog
                    title="Permanently purge this user?"
                    description="This will permanently remove this user and ALL associated data -- memories, tasks, messages, sessions, and connections. This action cannot be undone."
                    actionLabel="Purge Permanently"
                    onConfirm={() => purgeUser.mutate()}
                    variant="destructive"
                    actionVariant="destructive"
                  />
                </>
              )}
            </div>
          </div>
        </CardHeader>
      </Card>

      {/* Tabs */}
      <Tabs defaultValue="profile">
        <TabsList>
          <TabsTrigger value="profile">Profile</TabsTrigger>
          <TabsTrigger value="activity">Activity</TabsTrigger>
          <TabsTrigger value="connections">Connections</TabsTrigger>
          <TabsTrigger value="impersonate">Impersonate</TabsTrigger>
        </TabsList>

        {/* Profile Tab */}
        <TabsContent value="profile" className="mt-4">
          <ProfileTab user={user} />
        </TabsContent>

        {/* Activity Tab */}
        <TabsContent value="activity" className="mt-4">
          <ActivityTab
            activity={activity.data}
            isLoading={activity.isLoading}
            page={activityPage}
            setPage={setActivityPage}
          />
        </TabsContent>

        {/* Connections Tab */}
        <TabsContent value="connections" className="mt-4">
          <ConnectionsTab
            connections={connections.data}
            isLoading={connections.isLoading}
            isError={connections.isError}
          />
        </TabsContent>

        {/* Impersonate Tab */}
        <TabsContent value="impersonate" className="mt-4">
          <ImpersonateTab user={user} connections={connections.data} />
        </TabsContent>
      </Tabs>
    </div>
  );
}

// --- Profile Tab -------------------------------------------------------------

function ProfileTab({ user }: { user: UserDetail }) {
  const fields: [string, string | number | boolean | null][] = [
    ["Name", user.name],
    ["Email", user.email],
    ["Phone", user.phone],
    ["Timezone", user.timezone],
    ["Assistant Name", user.assistant_name],
    ["Phone Verified", user.phone_verified ? "Yes" : "No"],
    ["Created", user.created_at ? formatDate(user.created_at) : null],
    ["Last Active", user.last_seen_at ? formatDate(user.last_seen_at) : null],
    ["Messages", user.message_count],
    ["Tasks", user.task_count],
    ["Goals", user.goal_count],
    ["Memories", user.memory_count],
  ];

  return (
    <Card>
      <CardContent>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          {fields.map(([label, value]) => (
            <div key={label}>
              <span className="text-sm font-medium text-neutral-500">
                {label}
              </span>
              <p className="text-sm text-neutral-900 mt-0.5">
                {value !== null && value !== undefined
                  ? String(value)
                  : "---"}
              </p>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

// --- Activity Tab ------------------------------------------------------------

function ActivityTab({
  activity,
  isLoading,
  page,
  setPage,
}: {
  activity: ActivityResponse | undefined;
  isLoading: boolean;
  page: number;
  setPage: (p: number | ((p: number) => number)) => void;
}) {
  if (isLoading) {
    return (
      <div className="space-y-2">
        {Array.from({ length: 5 }).map((_, i) => (
          <Skeleton key={i} className="h-10 w-full" />
        ))}
      </div>
    );
  }

  const items = activity?.activity ?? [];
  const total = activity?.total ?? 0;
  const totalPages = Math.ceil(total / 50);

  if (items.length === 0) {
    return (
      <p className="text-sm text-neutral-500 py-8 text-center">
        No activity recorded.
      </p>
    );
  }

  return (
    <>
      <div className="overflow-x-auto rounded-md border border-neutral-200">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-[160px]">Time</TableHead>
              <TableHead className="w-[80px]">Direction</TableHead>
              <TableHead className="w-[80px]">Channel</TableHead>
              <TableHead className="w-[100px]">Intent</TableHead>
              <TableHead>Content</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {items.map((item) => (
              <TableRow key={item.id}>
                <TableCell className="text-xs text-neutral-500">
                  {formatDateTime(item.created_at)}
                </TableCell>
                <TableCell>
                  <Badge
                    className={
                      item.direction === "inbound"
                        ? "bg-blue-100 text-blue-700 hover:bg-blue-100 text-xs"
                        : "bg-neutral-100 text-neutral-600 hover:bg-neutral-100 text-xs"
                    }
                  >
                    {item.direction === "inbound" ? "In" : "Out"}
                  </Badge>
                </TableCell>
                <TableCell className="text-xs text-neutral-500">
                  {item.channel ?? "---"}
                </TableCell>
                <TableCell>
                  {item.intent ? (
                    <Badge variant="outline" className="text-xs capitalize">
                      {item.intent.replace(/_/g, " ")}
                    </Badge>
                  ) : (
                    <span className="text-xs text-neutral-400">---</span>
                  )}
                </TableCell>
                <TableCell className="max-w-xs">
                  <span
                    className="block truncate text-sm text-neutral-800"
                    title={item.body}
                  >
                    {item.body?.slice(0, 200)}
                  </span>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

      {totalPages > 1 && (
        <div className="mt-4 flex items-center justify-end gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => setPage((p: number) => Math.max(1, p - 1))}
            disabled={page === 1}
          >
            Previous
          </Button>
          <span className="text-sm text-neutral-500">
            Page {page} of {totalPages}
          </span>
          <Button
            variant="outline"
            size="sm"
            onClick={() => setPage((p: number) => Math.min(totalPages, p + 1))}
            disabled={page >= totalPages}
          >
            Next
          </Button>
        </div>
      )}
    </>
  );
}

// --- Connections Tab ---------------------------------------------------------

function ConnectionsTab({
  connections,
  isLoading,
  isError,
}: {
  connections: ConnectionsResponse | undefined;
  isLoading: boolean;
  isError: boolean;
}) {
  if (isLoading) {
    return (
      <div className="space-y-2">
        {Array.from({ length: 3 }).map((_, i) => (
          <Skeleton key={i} className="h-16 w-full" />
        ))}
      </div>
    );
  }

  if (isError) {
    return (
      <p className="text-sm text-neutral-500 py-8 text-center">
        Connections service unavailable.
      </p>
    );
  }

  const items = connections?.connections ?? [];

  if (items.length === 0) {
    return (
      <p className="text-sm text-neutral-500 py-8 text-center">
        No connections configured.
      </p>
    );
  }

  return (
    <div className="grid gap-3">
      {items.map((conn) => (
        <Card key={conn.provider}>
          <CardContent className="flex items-center justify-between py-3">
            <span className="text-sm font-medium text-neutral-900 capitalize">
              {conn.provider}
            </span>
            <Badge
              className={
                conn.status === "connected"
                  ? "bg-green-100 text-green-700 hover:bg-green-100"
                  : "bg-neutral-100 text-neutral-500 hover:bg-neutral-100"
              }
            >
              {conn.status}
            </Badge>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

// --- Impersonate Tab ---------------------------------------------------------

function ImpersonateTab({
  user,
  connections,
}: {
  user: UserDetail;
  connections: ConnectionsResponse | undefined;
}) {
  return (
    <div className="space-y-4">
      {/* Read-only banner */}
      <Alert>
        <AlertCircle className="h-4 w-4" />
        <AlertTitle>Read-only view</AlertTitle>
        <AlertDescription>
          You are viewing {user.email}'s dashboard. This is read-only.
        </AlertDescription>
      </Alert>

      {/* Summary: Conversations */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Conversations Summary</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <span className="text-sm text-neutral-500">Total Messages</span>
              <p className="text-lg font-semibold">{user.message_count}</p>
            </div>
            <div>
              <span className="text-sm text-neutral-500">Total Tasks</span>
              <p className="text-lg font-semibold">{user.task_count}</p>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Connections */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Connections</CardTitle>
        </CardHeader>
        <CardContent>
          {(connections?.connections ?? []).length === 0 ? (
            <p className="text-sm text-neutral-500">No connections.</p>
          ) : (
            <div className="space-y-2">
              {connections?.connections.map((conn) => (
                <div
                  key={conn.provider}
                  className="flex items-center justify-between"
                >
                  <span className="text-sm capitalize">{conn.provider}</span>
                  <Badge
                    className={
                      conn.status === "connected"
                        ? "bg-green-100 text-green-700 hover:bg-green-100"
                        : "bg-neutral-100 text-neutral-500 hover:bg-neutral-100"
                    }
                  >
                    {conn.status}
                  </Badge>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Settings (read-only) */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Settings</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div>
              <span className="text-sm text-neutral-500">Assistant Name</span>
              <p className="text-sm mt-0.5">
                {user.assistant_name ?? "Default"}
              </p>
            </div>
            <div>
              <span className="text-sm text-neutral-500">Timezone</span>
              <p className="text-sm mt-0.5">{user.timezone ?? "Not set"}</p>
            </div>
            <div className="sm:col-span-2">
              <span className="text-sm text-neutral-500">
                Personality Notes
              </span>
              <p className="text-sm mt-0.5">
                {user.personality_notes ?? "No notes"}
              </p>
            </div>
          </div>
          <Separator className="my-4" />
          <div className="flex gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled
              aria-disabled="true"
              title="Read-only view"
            >
              Edit Settings
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled
              aria-disabled="true"
              title="Read-only view"
            >
              Manage Connections
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

// --- Shared Components -------------------------------------------------------

function ConfirmDialog({
  title,
  description,
  actionLabel,
  onConfirm,
  variant = "outline",
  actionVariant,
  triggerClassName,
}: {
  title: string;
  description: string;
  actionLabel: string;
  onConfirm: () => void;
  variant?: "outline" | "destructive";
  actionVariant?: "default" | "destructive";
  triggerClassName?: string;
}) {
  return (
    <AlertDialog>
      <AlertDialogTrigger asChild>
        <Button variant={variant} size="sm" className={triggerClassName}>
          {actionLabel}
        </Button>
      </AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{title}</AlertDialogTitle>
          <AlertDialogDescription>{description}</AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction
            variant={actionVariant}
            onClick={onConfirm}
          >
            {actionLabel}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

function RoleBadge({ role }: { role: "admin" | "user" }) {
  if (role === "admin") {
    return (
      <Badge
        variant="default"
        className="bg-blue-100 text-blue-700 hover:bg-blue-100"
      >
        <Shield className="h-3 w-3 mr-1" />
        admin
      </Badge>
    );
  }
  return <Badge variant="secondary">user</Badge>;
}

function StatusBadge({
  status,
}: {
  status: "active" | "suspended" | "deleted";
}) {
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

function formatDateTime(iso: string): string {
  try {
    return new Intl.DateTimeFormat("en-US", {
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
    }).format(new Date(iso));
  } catch {
    return iso;
  }
}
