/**
 * Admin Proactivity Controls page -- platform-wide defaults for proactive messaging.
 *
 * Fetches GET /api/v1/admin/proactivity/settings to display current values.
 * Saves changes via PUT /api/v1/admin/proactivity/settings.
 * Per-user settings (set via dashboard or text commands) override these defaults.
 */
import { createFileRoute } from "@tanstack/react-router";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useState, useEffect } from "react";
import { Zap, Info } from "lucide-react";
import { toast } from "sonner";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  CardDescription,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { fetchWithAuth } from "@/lib/api";

export const Route = createFileRoute("/admin/proactivity")({
  component: ProactivityPage,
});

// --- Types -------------------------------------------------------------------

interface ProactivitySettings {
  max_daily_messages: number;
  max_per_hour: number;
  max_categories_per_day: number;
  quiet_hours_start: number;
  quiet_hours_end: number;
  content_suppression: boolean;
}

// --- Page --------------------------------------------------------------------

function ProactivityPage() {
  const queryClient = useQueryClient();

  const settingsQuery = useQuery<ProactivitySettings>({
    queryKey: ["admin", "proactivity", "settings"],
    queryFn: () =>
      fetchWithAuth("/api/v1/admin/proactivity/settings").then((r) => {
        if (!r.ok) throw new Error("Failed to load proactivity settings");
        return r.json();
      }) as Promise<ProactivitySettings>,
  });

  const [form, setForm] = useState<ProactivitySettings>({
    max_daily_messages: 3,
    max_per_hour: 10,
    max_categories_per_day: 3,
    quiet_hours_start: 22,
    quiet_hours_end: 7,
    content_suppression: true,
  });

  // Sync form with fetched data
  useEffect(() => {
    if (settingsQuery.data) {
      setForm(settingsQuery.data);
    }
  }, [settingsQuery.data]);

  const saveMutation = useMutation({
    mutationFn: async (values: Partial<ProactivitySettings>) => {
      const resp = await fetchWithAuth("/api/v1/admin/proactivity/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(values),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({ detail: "Save failed" }));
        throw new Error(err.detail || "Save failed");
      }
      return resp.json() as Promise<ProactivitySettings>;
    },
    onSuccess: (data) => {
      queryClient.setQueryData(["admin", "proactivity", "settings"], data);
      toast.success("Proactivity settings saved");
    },
    onError: (err: Error) => {
      toast.error(err.message || "Failed to save settings");
    },
  });

  const handleSave = () => {
    saveMutation.mutate(form);
  };

  const updateField = (field: keyof ProactivitySettings, value: string) => {
    const num = parseInt(value, 10);
    if (!isNaN(num)) {
      setForm((prev) => ({ ...prev, [field]: num }));
    }
  };

  if (settingsQuery.isLoading) {
    return <LoadingSkeleton />;
  }

  if (settingsQuery.isError) {
    return (
      <div className="mx-auto max-w-2xl px-4 sm:px-6 py-6 sm:py-8">
        <h1 className="mb-6 text-xl font-semibold text-neutral-900">
          Proactivity Controls
        </h1>
        <div className="flex flex-col items-center gap-3 py-16 text-center">
          <Zap className="h-10 w-10 text-neutral-300" />
          <p className="text-sm text-neutral-500">
            Unable to load proactivity settings. The admin API may be
            unreachable.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-2xl px-4 sm:px-6 py-6 sm:py-8">
      <div className="mb-6">
        <h1 className="text-xl font-semibold text-neutral-900">
          Proactivity Controls
        </h1>
        <p className="mt-1 text-sm text-neutral-500">
          Platform-wide defaults for proactive messaging. Per-user settings
          override these values.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Message Limits</CardTitle>
          <CardDescription>
            Control how many proactive messages users receive per day and per
            hour.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-5">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-5">
            <div className="space-y-1.5">
              <Label htmlFor="max_daily_messages">Max Daily Messages</Label>
              <Input
                id="max_daily_messages"
                type="number"
                min={1}
                max={50}
                value={form.max_daily_messages}
                onChange={(e) =>
                  updateField("max_daily_messages", e.target.value)
                }
              />
              <p className="text-xs text-neutral-400">1-50 messages per user per day</p>
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="max_per_hour">Max Per Hour</Label>
              <Input
                id="max_per_hour"
                type="number"
                min={1}
                max={20}
                value={form.max_per_hour}
                onChange={(e) => updateField("max_per_hour", e.target.value)}
              />
              <p className="text-xs text-neutral-400">1-20 messages per user per hour</p>
            </div>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="max_categories_per_day">
              Max Categories Per Day
            </Label>
            <Input
              id="max_categories_per_day"
              type="number"
              min={1}
              max={9}
              value={form.max_categories_per_day}
              onChange={(e) =>
                updateField("max_categories_per_day", e.target.value)
              }
            />
            <p className="text-xs text-neutral-400">
              1-9 categories selected per daily plan (upper bound for random selection)
            </p>
          </div>
        </CardContent>
      </Card>

      <Card className="mt-6">
        <CardHeader>
          <CardTitle className="text-base">Content Delta Suppression</CardTitle>
          <CardDescription>
            When enabled, proactive messages are only sent when new content is
            detected (new tasks, goals, calendar events). Disable to send all
            scheduled messages regardless of content changes.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <label className="flex items-center gap-3 cursor-pointer">
            <input
              type="checkbox"
              checked={form.content_suppression}
              onChange={() =>
                setForm((prev) => ({
                  ...prev,
                  content_suppression: !prev.content_suppression,
                }))
              }
              className="h-4 w-4 rounded border-neutral-300 text-blue-600 focus:ring-blue-500"
            />
            <span className="text-sm text-neutral-700">
              Enable suppression
            </span>
          </label>
        </CardContent>
      </Card>

      <Card className="mt-6">
        <CardHeader>
          <CardTitle className="text-base">Default Quiet Hours</CardTitle>
          <CardDescription>
            Proactive messages are deferred during quiet hours. Users can
            override these in their settings.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-2 gap-5">
            <div className="space-y-1.5">
              <Label htmlFor="quiet_hours_start">Start Hour</Label>
              <Input
                id="quiet_hours_start"
                type="number"
                min={0}
                max={23}
                value={form.quiet_hours_start}
                onChange={(e) =>
                  updateField("quiet_hours_start", e.target.value)
                }
              />
              <p className="text-xs text-neutral-400">0-23 (local time)</p>
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="quiet_hours_end">End Hour</Label>
              <Input
                id="quiet_hours_end"
                type="number"
                min={0}
                max={23}
                value={form.quiet_hours_end}
                onChange={(e) =>
                  updateField("quiet_hours_end", e.target.value)
                }
              />
              <p className="text-xs text-neutral-400">0-23 (local time)</p>
            </div>
          </div>
        </CardContent>
      </Card>

      <Alert className="mt-6">
        <Info className="h-4 w-4" />
        <AlertDescription>
          Changes apply immediately to the API process. For the scheduler
          process, restart the scheduler service or update environment variables.
        </AlertDescription>
      </Alert>

      <div className="mt-6 flex justify-end">
        <Button
          onClick={handleSave}
          disabled={saveMutation.isPending}
        >
          {saveMutation.isPending ? "Saving..." : "Save Settings"}
        </Button>
      </div>
    </div>
  );
}

// --- Loading Skeleton --------------------------------------------------------

function LoadingSkeleton() {
  return (
    <div className="mx-auto max-w-2xl px-4 sm:px-6 py-6 sm:py-8">
      <Skeleton className="h-7 w-48 mb-2" />
      <Skeleton className="h-4 w-80 mb-6" />
      <Card>
        <CardHeader>
          <Skeleton className="h-5 w-32" />
        </CardHeader>
        <CardContent className="space-y-4">
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-10 w-full" />
        </CardContent>
      </Card>
    </div>
  );
}
