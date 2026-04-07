/**
 * Proactive Settings page — manage per-category proactive engagement preferences
 * and global proactive settings (max daily messages, quiet hours).
 *
 * Fetches GET /api/v1/proactive-preferences for current state.
 * PUT /api/v1/proactive-preferences on save.
 */
import { useState, useEffect } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Switch } from "@/components/ui/switch";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { fetchWithAuth } from "@/lib/api";

export const Route = createFileRoute("/settings/proactive")({
  component: ProactiveSettingsPage,
});

// ─── Types ──────────────────────────────────────────────────────────────────

interface CategoryData {
  name: string;
  description: string;
  default_window_start: number;
  default_window_end: number;
  enabled: boolean;
  window_start_hour: number;
  window_end_hour: number;
  has_override: boolean;
}

interface GlobalSettings {
  max_daily_messages: number;
  quiet_hours_start: number;
  quiet_hours_end: number;
  enabled: boolean;
}

interface PreferencesResponse {
  categories: CategoryData[];
  global_settings: GlobalSettings;
}

// ─── Helpers ────────────────────────────────────────────────────────────────

function formatCategoryName(name: string): string {
  return name
    .split("_")
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

function formatHour(h: number): string {
  const hours = Math.floor(h);
  const minutes = Math.round((h - hours) * 60);
  const period = hours >= 12 ? "PM" : "AM";
  const displayHour = hours === 0 ? 12 : hours > 12 ? hours - 12 : hours;
  return `${displayHour}:${minutes.toString().padStart(2, "0")} ${period}`;
}

function formatHourSimple(h: number): string {
  const period = h >= 12 ? "PM" : "AM";
  const displayHour = h === 0 ? 12 : h > 12 ? h - 12 : h;
  return `${displayHour} ${period}`;
}

/** Generate half-hour increment options from 0 to 23.5 */
function generateHourOptions(): { value: string; label: string }[] {
  const options: { value: string; label: string }[] = [];
  for (let h = 0; h < 24; h += 0.5) {
    options.push({ value: h.toString(), label: formatHour(h) });
  }
  return options;
}

/** Generate whole-hour options for quiet hours (0-23) */
function generateWholeHourOptions(): { value: string; label: string }[] {
  const options: { value: string; label: string }[] = [];
  for (let h = 0; h < 24; h++) {
    options.push({ value: h.toString(), label: formatHourSimple(h) });
  }
  return options;
}

const hourOptions = generateHourOptions();
const wholeHourOptions = generateWholeHourOptions();
const maxDailyOptions = [1, 2, 3, 4, 5, 7, 10];

// ─── Component ──────────────────────────────────────────────────────────────

function ProactiveSettingsPage() {
  const queryClient = useQueryClient();

  const { data, isLoading } = useQuery<PreferencesResponse>({
    queryKey: ["proactive-preferences"],
    queryFn: () =>
      fetchWithAuth("/api/v1/proactive-preferences").then((r) =>
        r.json(),
      ) as Promise<PreferencesResponse>,
  });

  const [categories, setCategories] = useState<CategoryData[]>([]);
  const [globalSettings, setGlobalSettings] = useState<GlobalSettings>({
    max_daily_messages: 5,
    quiet_hours_start: 22,
    quiet_hours_end: 7,
    enabled: true,
  });

  useEffect(() => {
    if (data) {
      setCategories(data.categories);
      setGlobalSettings(data.global_settings);
    }
  }, [data]);

  const saveMutation = useMutation({
    mutationFn: async () => {
      const res = await fetchWithAuth("/api/v1/proactive-preferences", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          categories: categories.map((c) => ({
            name: c.name,
            enabled: c.enabled,
            window_start_hour: c.window_start_hour,
            window_end_hour: c.window_end_hour,
          })),
          global_settings: globalSettings,
        }),
      });
      if (!res.ok) {
        const err = (await res.json().catch(() => ({}))) as {
          detail?: string;
        };
        throw new Error(err.detail ?? "Failed to save settings.");
      }
      return res.json();
    },
    onSuccess: () => {
      toast.success("Proactive settings saved.");
      queryClient.invalidateQueries({ queryKey: ["proactive-preferences"] });
    },
    onError: (err: Error) => {
      toast.error(err.message);
    },
  });

  const updateCategory = (name: string, updates: Partial<CategoryData>) => {
    setCategories((prev) =>
      prev.map((c) => (c.name === name ? { ...c, ...updates } : c)),
    );
  };

  const resetCategoryToDefaults = (name: string) => {
    setCategories((prev) =>
      prev.map((c) =>
        c.name === name
          ? {
              ...c,
              window_start_hour: c.default_window_start,
              window_end_hour: c.default_window_end,
            }
          : c,
      ),
    );
  };

  const isCustomWindow = (cat: CategoryData): boolean => {
    return (
      cat.window_start_hour !== cat.default_window_start ||
      cat.window_end_hour !== cat.default_window_end
    );
  };

  if (isLoading) {
    return (
      <div className="mx-auto max-w-2xl px-4 sm:px-6 py-6 sm:py-8">
        <div className="flex items-center gap-2 text-neutral-500">
          <Loader2 className="h-4 w-4 animate-spin" />
          Loading preferences...
        </div>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-2xl px-4 sm:px-6 py-6 sm:py-8">
      <div className="mb-6">
        <h1 className="text-xl font-semibold text-neutral-900">
          Proactive Settings
        </h1>
        <p className="mt-1 text-sm text-neutral-500">
          Control when and how your assistant reaches out proactively.
        </p>
      </div>

      {/* Global Settings */}
      <Card className="mb-6">
        <CardHeader>
          <CardTitle>Global Settings</CardTitle>
          <CardDescription>
            Master controls for all proactive messages.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex items-center justify-between">
            <Label htmlFor="global-enabled">Enable proactive messages</Label>
            <Switch
              id="global-enabled"
              checked={globalSettings.enabled}
              onCheckedChange={(checked: boolean) =>
                setGlobalSettings((prev) => ({ ...prev, enabled: checked }))
              }
            />
          </div>

          <div className="flex items-center justify-between">
            <Label htmlFor="max-daily">Max daily messages</Label>
            <Select
              value={globalSettings.max_daily_messages.toString()}
              onValueChange={(v: string) =>
                setGlobalSettings((prev) => ({
                  ...prev,
                  max_daily_messages: parseInt(v, 10),
                }))
              }
            >
              <SelectTrigger id="max-daily" className="w-20">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {maxDailyOptions.map((n) => (
                  <SelectItem key={n} value={n.toString()}>
                    {n}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="flex items-center justify-between">
            <Label>Quiet hours</Label>
            <div className="flex items-center gap-2">
              <Select
                value={globalSettings.quiet_hours_start.toString()}
                onValueChange={(v: string) =>
                  setGlobalSettings((prev) => ({
                    ...prev,
                    quiet_hours_start: parseInt(v, 10),
                  }))
                }
              >
                <SelectTrigger className="w-24">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {wholeHourOptions.map((o) => (
                    <SelectItem key={o.value} value={o.value}>
                      {o.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <span className="text-sm text-neutral-500">to</span>
              <Select
                value={globalSettings.quiet_hours_end.toString()}
                onValueChange={(v: string) =>
                  setGlobalSettings((prev) => ({
                    ...prev,
                    quiet_hours_end: parseInt(v, 10),
                  }))
                }
              >
                <SelectTrigger className="w-24">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {wholeHourOptions.map((o) => (
                    <SelectItem key={o.value} value={o.value}>
                      {o.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Categories */}
      <div
        className={
          globalSettings.enabled ? "" : "pointer-events-none opacity-50"
        }
      >
        <h2 className="mb-3 text-lg font-medium text-neutral-900">
          Categories
        </h2>
        <div className="space-y-4">
          {categories.map((cat) => (
            <Card key={cat.name}>
              <CardHeader>
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <CardTitle className="text-base">
                      {formatCategoryName(cat.name)}
                    </CardTitle>
                    {isCustomWindow(cat) && (
                      <Badge variant="secondary" className="text-xs">
                        custom
                      </Badge>
                    )}
                  </div>
                  <Switch
                    checked={cat.enabled}
                    onCheckedChange={(checked: boolean) =>
                      updateCategory(cat.name, { enabled: checked })
                    }
                  />
                </div>
                <CardDescription>{cat.description}</CardDescription>
              </CardHeader>
              {cat.enabled && (
                <CardContent>
                  <div className="flex flex-wrap items-center gap-3">
                    <div className="flex items-center gap-2">
                      <Label className="text-xs text-neutral-500">From</Label>
                      <Select
                        value={cat.window_start_hour.toString()}
                        onValueChange={(v: string) =>
                          updateCategory(cat.name, {
                            window_start_hour: parseFloat(v),
                          })
                        }
                      >
                        <SelectTrigger className="w-28">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          {hourOptions.map((o) => (
                            <SelectItem key={o.value} value={o.value}>
                              {o.label}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>
                    <div className="flex items-center gap-2">
                      <Label className="text-xs text-neutral-500">To</Label>
                      <Select
                        value={cat.window_end_hour.toString()}
                        onValueChange={(v: string) =>
                          updateCategory(cat.name, {
                            window_end_hour: parseFloat(v),
                          })
                        }
                      >
                        <SelectTrigger className="w-28">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          {hourOptions.map((o) => (
                            <SelectItem key={o.value} value={o.value}>
                              {o.label}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>
                    {isCustomWindow(cat) && (
                      <button
                        type="button"
                        className="text-xs text-blue-600 hover:text-blue-700"
                        onClick={() => resetCategoryToDefaults(cat.name)}
                      >
                        Reset to defaults
                      </button>
                    )}
                  </div>
                </CardContent>
              )}
            </Card>
          ))}
        </div>
      </div>

      {/* Save button */}
      <div className="mt-6">
        <Button
          className="bg-blue-600 hover:bg-blue-700"
          disabled={saveMutation.isPending}
          onClick={() => saveMutation.mutate()}
        >
          {saveMutation.isPending && (
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
          )}
          Save changes
        </Button>
      </div>
    </div>
  );
}
