/**
 * Goals page -- manage user goals with framework selection (OKR/SMART/custom).
 *
 * CRUD operations via /api/v1/goals.
 * Filter by status: Active | Completed | Archived | All.
 * Create form with title, framework, description, target date.
 */
import { useState, useEffect } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Loader2, Plus, Trash2, CheckCircle2, Archive, Pencil } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
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
import { fetchWithAuth } from "@/lib/api";

export const Route = createFileRoute("/goals")({
  component: GoalsPage,
});

// ---- Types ----

interface Goal {
  id: string;
  title: string;
  framework: string;
  description: string | null;
  target_date: string | null;
  status: string;
  persona_id: string | null;
  parent_goal_id: string | null;
  version: number;
  created_at: string;
  updated_at: string;
}

interface GoalsResponse {
  goals: Goal[];
}

const FRAMEWORK_OPTIONS = [
  { value: "custom", label: "Custom" },
  { value: "okr", label: "OKR" },
  { value: "smart", label: "SMART" },
];

const STATUS_TABS = ["active", "completed", "archived", "all"] as const;

// ---- Page ----

function GoalsPage() {
  const queryClient = useQueryClient();
  const [statusFilter, setStatusFilter] = useState<string>("active");
  const [showForm, setShowForm] = useState(false);

  // Form state
  const [title, setTitle] = useState("");
  const [framework, setFramework] = useState("custom");
  const [description, setDescription] = useState("");
  const [targetDate, setTargetDate] = useState("");

  const { data, isLoading } = useQuery<GoalsResponse>({
    queryKey: ["goals", statusFilter],
    queryFn: () =>
      fetchWithAuth(`/api/v1/goals?status=${statusFilter}`).then((r) => r.json()) as Promise<GoalsResponse>,
  });

  const createMutation = useMutation({
    mutationFn: async () => {
      const res = await fetchWithAuth("/api/v1/goals", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title: title.trim(),
          framework,
          description: description.trim() || undefined,
          target_date: targetDate || undefined,
        }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({})) as { detail?: string };
        throw new Error(err.detail ?? "Failed to create goal.");
      }
      return res.json();
    },
    onSuccess: () => {
      toast.success("Goal created.");
      setTitle("");
      setFramework("custom");
      setDescription("");
      setTargetDate("");
      setShowForm(false);
      queryClient.invalidateQueries({ queryKey: ["goals"] });
    },
    onError: (err: Error) => toast.error(err.message),
  });

  const updateMutation = useMutation({
    mutationFn: async ({ goalId, updates }: { goalId: string; updates: Record<string, unknown> }) => {
      const res = await fetchWithAuth(`/api/v1/goals/${goalId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(updates),
      });
      if (!res.ok) throw new Error("Failed to update goal.");
      return res.json();
    },
    onSuccess: () => {
      toast.success("Goal updated.");
      queryClient.invalidateQueries({ queryKey: ["goals"] });
    },
    onError: (err: Error) => toast.error(err.message),
  });

  const deleteMutation = useMutation({
    mutationFn: async (goalId: string) => {
      const res = await fetchWithAuth(`/api/v1/goals/${goalId}`, { method: "DELETE" });
      if (!res.ok && res.status !== 204) throw new Error("Failed to delete goal.");
    },
    onSuccess: () => {
      toast.success("Goal deleted.");
      queryClient.invalidateQueries({ queryKey: ["goals"] });
    },
    onError: (err: Error) => toast.error(err.message),
  });

  const handleCreate = (e: React.FormEvent) => {
    e.preventDefault();
    if (!title.trim()) {
      toast.error("Title is required.");
      return;
    }
    createMutation.mutate();
  };

  const goals = data?.goals ?? [];

  return (
    <div className="mx-auto max-w-4xl px-4 sm:px-6 py-6 sm:py-8">
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-xl font-semibold text-neutral-900">Goals</h1>
        <Button
          className="bg-blue-600 hover:bg-blue-700"
          onClick={() => setShowForm(!showForm)}
        >
          <Plus className="mr-2 h-4 w-4" />
          New Goal
        </Button>
      </div>

      {/* Create form */}
      {showForm && (
        <Card className="mb-6 border border-neutral-200">
          <CardContent className="pt-6">
            <form onSubmit={handleCreate} className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="goal-title">Title</Label>
                <Input
                  id="goal-title"
                  placeholder="What do you want to achieve?"
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                />
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div className="space-y-2">
                  <Label>Framework</Label>
                  <Select value={framework} onValueChange={setFramework}>
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {FRAMEWORK_OPTIONS.map((opt) => (
                        <SelectItem key={opt.value} value={opt.value}>
                          {opt.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>

                <div className="space-y-2">
                  <Label htmlFor="goal-date">Target date</Label>
                  <Input
                    id="goal-date"
                    type="date"
                    value={targetDate}
                    onChange={(e) => setTargetDate(e.target.value)}
                  />
                </div>
              </div>

              <div className="space-y-2">
                <Label htmlFor="goal-desc">Description</Label>
                <Textarea
                  id="goal-desc"
                  placeholder="Optional details..."
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  rows={3}
                />
              </div>

              <div className="flex gap-2">
                <Button
                  type="submit"
                  className="bg-blue-600 hover:bg-blue-700"
                  disabled={createMutation.isPending}
                >
                  {createMutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                  Create Goal
                </Button>
                <Button type="button" variant="outline" onClick={() => setShowForm(false)}>
                  Cancel
                </Button>
              </div>
            </form>
          </CardContent>
        </Card>
      )}

      {/* Status filter tabs */}
      <div className="mb-4 flex gap-1 rounded-lg bg-neutral-100 p-1">
        {STATUS_TABS.map((tab) => (
          <button
            key={tab}
            onClick={() => setStatusFilter(tab)}
            className={[
              "rounded-md px-3 py-1.5 text-sm font-medium transition-colors",
              statusFilter === tab
                ? "bg-white text-neutral-900 shadow-sm"
                : "text-neutral-600 hover:text-neutral-900",
            ].join(" ")}
          >
            {tab.charAt(0).toUpperCase() + tab.slice(1)}
          </button>
        ))}
      </div>

      {/* Goals list */}
      {isLoading && (
        <div className="flex h-32 items-center justify-center">
          <Loader2 className="h-6 w-6 animate-spin text-neutral-400" />
        </div>
      )}

      {!isLoading && goals.length === 0 && (
        <div className="flex flex-col items-center gap-2 py-12 text-center">
          <p className="text-sm text-neutral-500">No {statusFilter !== "all" ? statusFilter : ""} goals yet.</p>
        </div>
      )}

      <div className="space-y-3">
        {goals.map((goal) => (
          <Card key={goal.id} className="border border-neutral-200">
            <CardHeader className="flex flex-row items-start justify-between gap-2 pb-2">
              <div className="flex-1 min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <h3 className="text-sm font-medium text-neutral-900">{goal.title}</h3>
                  <Badge variant="outline" className="text-xs">
                    {goal.framework.toUpperCase()}
                  </Badge>
                  <Badge
                    className={
                      goal.status === "active"
                        ? "bg-blue-100 text-blue-700 hover:bg-blue-100"
                        : goal.status === "completed"
                          ? "bg-green-100 text-green-700 hover:bg-green-100"
                          : "bg-neutral-100 text-neutral-600 hover:bg-neutral-100"
                    }
                  >
                    {goal.status}
                  </Badge>
                </div>
                {goal.description && (
                  <p className="mt-1 text-xs text-neutral-500">{goal.description}</p>
                )}
                {goal.target_date && (
                  <p className="mt-1 text-xs text-neutral-400">
                    Target: {new Date(goal.target_date).toLocaleDateString()}
                  </p>
                )}
              </div>
              <div className="flex gap-1">
                {goal.status === "active" && (
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-8 w-8 p-0 text-green-600 hover:text-green-700"
                    onClick={() =>
                      updateMutation.mutate({ goalId: goal.id, updates: { status: "completed" } })
                    }
                    title="Complete"
                  >
                    <CheckCircle2 className="h-4 w-4" />
                  </Button>
                )}
                {goal.status === "active" && (
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-8 w-8 p-0 text-neutral-500 hover:text-neutral-700"
                    onClick={() =>
                      updateMutation.mutate({ goalId: goal.id, updates: { status: "archived" } })
                    }
                    title="Archive"
                  >
                    <Archive className="h-4 w-4" />
                  </Button>
                )}
                <AlertDialog>
                  <AlertDialogTrigger asChild>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-8 w-8 p-0 text-red-500 hover:text-red-700"
                      title="Delete"
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </AlertDialogTrigger>
                  <AlertDialogContent>
                    <AlertDialogHeader>
                      <AlertDialogTitle>Delete goal?</AlertDialogTitle>
                      <AlertDialogDescription>
                        This will permanently delete "{goal.title}". This action cannot be undone.
                      </AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                      <AlertDialogCancel>Cancel</AlertDialogCancel>
                      <AlertDialogAction
                        onClick={() => deleteMutation.mutate(goal.id)}
                        className="bg-red-600 hover:bg-red-700"
                      >
                        Delete
                      </AlertDialogAction>
                    </AlertDialogFooter>
                  </AlertDialogContent>
                </AlertDialog>
              </div>
            </CardHeader>
          </Card>
        ))}
      </div>
    </div>
  );
}
