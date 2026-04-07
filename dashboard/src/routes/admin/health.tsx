/**
 * System Health page -- shows Redis, Database, and Worker status cards.
 *
 * Fetches GET /api/v1/admin/health with 30-second auto-refresh.
 * Each service card shows status badge, key metric, and last-checked timestamp.
 */
import { createFileRoute } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { Database, HardDrive, Cpu } from "lucide-react";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import { fetchWithAuth } from "@/lib/api";

export const Route = createFileRoute("/admin/health")({
  component: SystemHealthPage,
});

// --- Types -------------------------------------------------------------------

interface HealthResponse {
  redis: {
    status: string;
    queue_depth: number;
  };
  database: {
    status: string;
  };
  worker: {
    pending_jobs: number;
  };
}

// --- Page --------------------------------------------------------------------

function SystemHealthPage() {
  const health = useQuery<HealthResponse>({
    queryKey: ["admin", "health"],
    queryFn: () =>
      fetchWithAuth("/api/v1/admin/health").then((r) => {
        if (!r.ok) throw new Error("health-check-failed");
        return r.json();
      }) as Promise<HealthResponse>,
    refetchInterval: 30000,
  });

  return (
    <div className="mx-auto max-w-4xl px-4 sm:px-6 py-6 sm:py-8">
      <h1 className="mb-6 text-xl font-semibold text-neutral-900">
        System Health
      </h1>

      {health.isLoading ? (
        <LoadingSkeleton />
      ) : health.isError ? (
        <ErrorState />
      ) : health.data ? (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
          <RedisCard
            status={health.data.redis.status}
            queueDepth={health.data.redis.queue_depth}
            lastChecked={health.dataUpdatedAt}
          />
          <DatabaseCard
            status={health.data.database.status}
            lastChecked={health.dataUpdatedAt}
          />
          <WorkerCard
            pendingJobs={health.data.worker.pending_jobs}
            lastChecked={health.dataUpdatedAt}
          />
        </div>
      ) : null}
    </div>
  );
}

// --- Service Cards -----------------------------------------------------------

function RedisCard({
  status,
  queueDepth,
  lastChecked,
}: {
  status: string;
  queueDepth: number;
  lastChecked: number;
}) {
  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <HardDrive className="h-5 w-5 text-neutral-500" />
            <CardTitle className="text-base">Redis</CardTitle>
          </div>
          <HealthBadge status={status} />
        </div>
      </CardHeader>
      <CardContent>
        <div className="space-y-3">
          <div>
            <span className="text-sm text-neutral-500">Queue Depth</span>
            <div className="flex items-center gap-2 mt-1">
              <Progress value={Math.min(queueDepth, 100)} className="flex-1" />
              <span className="text-sm font-medium text-neutral-700">
                {queueDepth}
              </span>
            </div>
          </div>
          <LastChecked timestamp={lastChecked} />
        </div>
      </CardContent>
    </Card>
  );
}

function DatabaseCard({
  status,
  lastChecked,
}: {
  status: string;
  lastChecked: number;
}) {
  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Database className="h-5 w-5 text-neutral-500" />
            <CardTitle className="text-base">Database</CardTitle>
          </div>
          <HealthBadge status={status} />
        </div>
      </CardHeader>
      <CardContent>
        <div className="space-y-3">
          <div>
            <span className="text-sm text-neutral-500">Status</span>
            <p className="text-sm font-medium text-neutral-700 mt-0.5">
              {status === "ok" ? "Connected" : "Error"}
            </p>
          </div>
          <LastChecked timestamp={lastChecked} />
        </div>
      </CardContent>
    </Card>
  );
}

function WorkerCard({
  pendingJobs,
  lastChecked,
}: {
  pendingJobs: number;
  lastChecked: number;
}) {
  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Cpu className="h-5 w-5 text-neutral-500" />
            <CardTitle className="text-base">Worker</CardTitle>
          </div>
          <HealthBadge status={pendingJobs > 50 ? "degraded" : "ok"} />
        </div>
      </CardHeader>
      <CardContent>
        <div className="space-y-3">
          <div>
            <span className="text-sm text-neutral-500">Pending Jobs</span>
            <p className="text-sm font-medium text-neutral-700 mt-0.5">
              {pendingJobs}
            </p>
          </div>
          <LastChecked timestamp={lastChecked} />
        </div>
      </CardContent>
    </Card>
  );
}

// --- Shared Components -------------------------------------------------------

function HealthBadge({ status }: { status: string }) {
  switch (status) {
    case "ok":
      return (
        <Badge className="bg-green-100 text-green-700 hover:bg-green-100">
          Healthy
        </Badge>
      );
    case "degraded":
      return (
        <Badge className="bg-amber-100 text-amber-700 hover:bg-amber-100">
          Degraded
        </Badge>
      );
    case "error":
    case "down":
      return <Badge variant="destructive">Down</Badge>;
    default:
      return (
        <Badge className="bg-neutral-100 text-neutral-500 hover:bg-neutral-100">
          Unable to check
        </Badge>
      );
  }
}

function LastChecked({ timestamp }: { timestamp: number }) {
  return (
    <p className="text-xs text-neutral-400">
      Last checked:{" "}
      {timestamp
        ? new Intl.DateTimeFormat("en-US", {
            hour: "numeric",
            minute: "2-digit",
            second: "2-digit",
          }).format(new Date(timestamp))
        : "---"}
    </p>
  );
}

function LoadingSkeleton() {
  return (
    <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
      {Array.from({ length: 3 }).map((_, i) => (
        <Card key={i}>
          <CardHeader>
            <Skeleton className="h-6 w-24" />
          </CardHeader>
          <CardContent>
            <div className="space-y-3">
              <Skeleton className="h-4 w-32" />
              <Skeleton className="h-2 w-full" />
              <Skeleton className="h-3 w-20" />
            </div>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

function ErrorState() {
  return (
    <div className="flex flex-col items-center gap-3 py-16 text-center">
      <Database className="h-10 w-10 text-neutral-300" />
      <p className="text-sm text-neutral-500">
        Unable to check service status. The admin API may be unreachable.
      </p>
    </div>
  );
}
