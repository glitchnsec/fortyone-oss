/**
 * Activity page -- action log timeline showing all agent actions.
 *
 * Fetches paginated actions from /api/v1/actions.
 * Displays chronological timeline with action_type badges, outcomes, and triggers.
 */
import { useState } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { Loader2, ChevronLeft, ChevronRight, Mail, Calendar, Search, Zap, Bell } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { fetchWithAuth } from "@/lib/api";

export const Route = createFileRoute("/actions")({
  component: ActionsPage,
});

// ---- Types ----

interface ActionEntry {
  id: string;
  action_type: string;
  description: string;
  outcome: string | null;
  trigger: string | null;
  created_at: string;
}

interface ActionsResponse {
  actions: ActionEntry[];
  page: number;
  limit: number;
}

// ---- Helpers ----

const ACTION_TYPE_CONFIG: Record<string, { icon: React.ReactNode; color: string }> = {
  email_sent: { icon: <Mail className="h-4 w-4" />, color: "bg-blue-100 text-blue-700" },
  event_created: { icon: <Calendar className="h-4 w-4" />, color: "bg-purple-100 text-purple-700" },
  search: { icon: <Search className="h-4 w-4" />, color: "bg-amber-100 text-amber-700" },
  briefing: { icon: <Bell className="h-4 w-4" />, color: "bg-green-100 text-green-700" },
};

const OUTCOME_COLORS: Record<string, string> = {
  success: "bg-green-100 text-green-700",
  failed: "bg-red-100 text-red-700",
  cancelled: "bg-neutral-100 text-neutral-600",
  pending: "bg-yellow-100 text-yellow-700",
};

function getActionConfig(actionType: string) {
  return ACTION_TYPE_CONFIG[actionType] ?? { icon: <Zap className="h-4 w-4" />, color: "bg-neutral-100 text-neutral-700" };
}

// ---- Page ----

function ActionsPage() {
  const [page, setPage] = useState(1);
  const limit = 50;

  const { data, isLoading } = useQuery<ActionsResponse>({
    queryKey: ["actions", page],
    queryFn: () =>
      fetchWithAuth(`/api/v1/actions?page=${page}&limit=${limit}`).then((r) => r.json()) as Promise<ActionsResponse>,
  });

  const actions = data?.actions ?? [];
  const hasMore = actions.length === limit;

  return (
    <div className="mx-auto max-w-3xl px-6 py-8">
      <h1 className="mb-6 text-xl font-semibold text-neutral-900">Activity</h1>

      {isLoading && (
        <div className="flex h-32 items-center justify-center">
          <Loader2 className="h-6 w-6 animate-spin text-neutral-400" />
        </div>
      )}

      {!isLoading && actions.length === 0 && (
        <div className="flex flex-col items-center gap-2 py-12 text-center">
          <Zap className="h-10 w-10 text-neutral-300" />
          <h2 className="text-lg font-semibold text-neutral-900">No activity yet</h2>
          <p className="max-w-xs text-sm text-neutral-500">
            Actions taken by your assistant will appear here.
          </p>
        </div>
      )}

      {/* Timeline */}
      {!isLoading && actions.length > 0 && (
        <div className="relative space-y-0">
          {/* Vertical line */}
          <div className="absolute left-5 top-0 bottom-0 w-px bg-neutral-200" />

          {actions.map((action) => {
            const config = getActionConfig(action.action_type);
            const outcomeColor = action.outcome ? OUTCOME_COLORS[action.outcome] ?? "bg-neutral-100 text-neutral-600" : null;
            const ts = new Date(action.created_at);

            return (
              <div key={action.id} className="relative flex gap-4 pb-6">
                {/* Icon dot */}
                <div className={`relative z-10 flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-full border border-neutral-200 bg-white ${config.color}`}>
                  {config.icon}
                </div>

                {/* Content */}
                <div className="flex-1 pt-1">
                  <div className="flex items-center gap-2">
                    <Badge variant="outline" className="text-xs">
                      {action.action_type.replace(/_/g, " ")}
                    </Badge>
                    {action.outcome && outcomeColor && (
                      <Badge className={`${outcomeColor} text-xs hover:${outcomeColor}`}>
                        {action.outcome}
                      </Badge>
                    )}
                    {action.trigger && (
                      <span className="text-xs text-neutral-400">{action.trigger.replace(/_/g, " ")}</span>
                    )}
                  </div>
                  <p className="mt-1 text-sm text-neutral-700">{action.description}</p>
                  <p className="mt-0.5 text-xs text-neutral-400">
                    {ts.toLocaleDateString()} at {ts.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                  </p>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Pagination */}
      {!isLoading && (page > 1 || hasMore) && (
        <div className="mt-6 flex items-center justify-center gap-4">
          <Button
            variant="outline"
            size="sm"
            disabled={page <= 1}
            onClick={() => setPage((p) => Math.max(1, p - 1))}
          >
            <ChevronLeft className="mr-1 h-4 w-4" />
            Previous
          </Button>
          <span className="text-sm text-neutral-500">Page {page}</span>
          <Button
            variant="outline"
            size="sm"
            disabled={!hasMore}
            onClick={() => setPage((p) => p + 1)}
          >
            Next
            <ChevronRight className="ml-1 h-4 w-4" />
          </Button>
        </div>
      )}
    </div>
  );
}
