/**
 * Admin Overview page -- the first page an admin sees at /admin.
 *
 * Displays:
 *   - 4 stat cards: Total Users, Active Today, Messages Today, Pending Tasks
 *   - Time range selector (7d, 30d, 90d, All)
 *   - 4 analytics charts: Signups, Active Users, Messages per Day, Top Intents
 *
 * All data fetched via fetchWithAuth from /api/v1/admin/analytics/* endpoints.
 * Time range state controls all query refetches.
 */
import { useState } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { Users, UserCheck, MessageSquare, ListTodo } from "lucide-react";
import { fetchWithAuth } from "@/lib/api";
import { StatCard } from "@/components/admin/StatCard";
import { TimeRangeSelector } from "@/components/admin/TimeRangeSelector";
import { SignupsChart } from "@/components/admin/charts/SignupsChart";
import { ActiveUsersChart } from "@/components/admin/charts/ActiveUsersChart";
import { MessagesChart } from "@/components/admin/charts/MessagesChart";
import { IntentsChart } from "@/components/admin/charts/IntentsChart";

export const Route = createFileRoute("/admin/")({
  component: AdminOverview,
});

interface OverviewData {
  total_users: number;
  active_today: number;
  messages_today: number;
  pending_tasks: number;
}

function AdminOverview() {
  const [range, setRange] = useState("30d");

  const overview = useQuery({
    queryKey: ["admin", "overview", range],
    queryFn: () =>
      fetchWithAuth(`/api/v1/admin/analytics/overview?range=${range}`).then(
        (r) => r.json() as Promise<OverviewData>,
      ),
  });

  const signups = useQuery({
    queryKey: ["admin", "signups", range],
    queryFn: () =>
      fetchWithAuth(`/api/v1/admin/analytics/signups?range=${range}`).then(
        (r) => r.json() as Promise<{ data: Array<{ date: string; count: number }> }>,
      ),
  });

  const activeUsers = useQuery({
    queryKey: ["admin", "active-users", range],
    queryFn: () =>
      fetchWithAuth(`/api/v1/admin/analytics/active-users?range=${range}`).then(
        (r) => r.json() as Promise<{ data: Array<{ date: string; dau: number }> }>,
      ),
  });

  const messages = useQuery({
    queryKey: ["admin", "messages", range],
    queryFn: () =>
      fetchWithAuth(`/api/v1/admin/analytics/messages?range=${range}`).then(
        (r) => r.json() as Promise<{ data: Array<{ date: string; count: number }> }>,
      ),
  });

  const intents = useQuery({
    queryKey: ["admin", "intents", range],
    queryFn: () =>
      fetchWithAuth(`/api/v1/admin/analytics/intents?range=${range}`).then(
        (r) => r.json() as Promise<{ data: Array<{ intent: string; count: number }> }>,
      ),
  });

  return (
    <div className="space-y-6">
      {/* Page header */}
      <h1 className="text-xl font-semibold text-neutral-900">Admin Overview</h1>

      {/* Stat cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-6">
        <StatCard
          label="Total Users"
          value={overview.data?.total_users ?? 0}
          icon={<Users className="h-6 w-6" />}
          loading={overview.isLoading}
        />
        <StatCard
          label="Active Today"
          value={overview.data?.active_today ?? 0}
          icon={<UserCheck className="h-6 w-6" />}
          loading={overview.isLoading}
        />
        <StatCard
          label="Messages Today"
          value={overview.data?.messages_today ?? 0}
          icon={<MessageSquare className="h-6 w-6" />}
          loading={overview.isLoading}
        />
        <StatCard
          label="Pending Tasks"
          value={overview.data?.pending_tasks ?? 0}
          icon={<ListTodo className="h-6 w-6" />}
          loading={overview.isLoading}
        />
      </div>

      {/* Time range selector */}
      <div className="flex justify-end">
        <TimeRangeSelector value={range} onChange={setRange} />
      </div>

      {/* Error state */}
      {overview.isError && (
        <p className="text-sm text-red-600">
          Unable to load analytics. Check your connection and try refreshing the page.
        </p>
      )}

      {/* Charts grid */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <SignupsChart
          data={signups.data?.data ?? []}
          loading={signups.isLoading}
        />
        <ActiveUsersChart
          data={activeUsers.data?.data ?? []}
          loading={activeUsers.isLoading}
        />
        <MessagesChart
          data={messages.data?.data ?? []}
          loading={messages.isLoading}
        />
        <IntentsChart
          data={intents.data?.data ?? []}
          loading={intents.isLoading}
        />
      </div>
    </div>
  );
}
