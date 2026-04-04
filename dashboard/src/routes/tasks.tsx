/**
 * Tasks page -- manage reminders, follow-ups, and scheduled items.
 *
 * CRUD operations via /api/v1/tasks.
 * Filter by status: Active | Completed | All.
 * Create form with title, type, description, due date.
 */
import { useState } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Loader2, Plus, Trash2, CheckCircle2, Clock, Pencil, AlertTriangle } from "lucide-react";
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

export const Route = createFileRoute("/tasks")({
  component: TasksPage,
});

// ---- Types ----

interface Task {
  id: string;
  title: string;
  task_type: string;
  description: string | null;
  due_at: string | null;
  completed: boolean;
  created_at: string;
  updated_at: string | null;
}

interface TasksResponse {
  tasks: Task[];
}

// ---- API helpers ----

async function fetchTasks(status: string): Promise<Task[]> {
  const res = await fetchWithAuth(`/api/v1/tasks?status=${status}`);
  if (!res.ok) throw new Error("Failed to fetch tasks");
  const data: TasksResponse = await res.json();
  return data.tasks;
}

async function createTask(body: {
  title: string;
  task_type: string;
  description?: string;
  due_at?: string;
}) {
  const res = await fetchWithAuth("/api/v1/tasks", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error("Failed to create task");
  return res.json();
}

async function completeTask(id: string) {
  const res = await fetchWithAuth(`/api/v1/tasks/${id}/complete`, {
    method: "POST",
  });
  if (!res.ok) throw new Error("Failed to complete task");
  return res.json();
}

async function updateTask(id: string, body: Record<string, unknown>) {
  const res = await fetchWithAuth(`/api/v1/tasks/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error("Failed to update task");
  return res.json();
}

async function deleteTask(id: string) {
  const res = await fetchWithAuth(`/api/v1/tasks/${id}`, { method: "DELETE" });
  if (!res.ok) throw new Error("Failed to delete task");
}

// ---- Component ----

function TasksPage() {
  const qc = useQueryClient();
  const [filter, setFilter] = useState<string>("active");
  const [showForm, setShowForm] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);

  // Form state
  const [title, setTitle] = useState("");
  const [taskType, setTaskType] = useState("reminder");
  const [description, setDescription] = useState("");
  const [dueAt, setDueAt] = useState("");

  const { data: tasks = [], isLoading } = useQuery({
    queryKey: ["tasks", filter],
    queryFn: () => fetchTasks(filter),
  });

  const createMut = useMutation({
    mutationFn: createTask,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["tasks"] });
      resetForm();
      toast.success("Task created");
    },
    onError: () => toast.error("Failed to create task"),
  });

  const completeMut = useMutation({
    mutationFn: completeTask,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["tasks"] });
      toast.success("Task completed");
    },
  });

  const updateMut = useMutation({
    mutationFn: ({ id, body }: { id: string; body: Record<string, unknown> }) =>
      updateTask(id, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["tasks"] });
      setEditingId(null);
      resetForm();
      toast.success("Task updated");
    },
  });

  const deleteMut = useMutation({
    mutationFn: deleteTask,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["tasks"] });
      toast.success("Task deleted");
    },
  });

  function resetForm() {
    setTitle("");
    setTaskType("reminder");
    setDescription("");
    setDueAt("");
    setShowForm(false);
  }

  function startEdit(t: Task) {
    setEditingId(t.id);
    setTitle(t.title);
    setTaskType(t.task_type);
    setDescription(t.description || "");
    setDueAt(t.due_at ? t.due_at.slice(0, 16) : "");
    setShowForm(true);
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!title.trim()) return;

    if (editingId) {
      updateMut.mutate({
        id: editingId,
        body: {
          title: title.trim(),
          task_type: taskType,
          description: description.trim() || undefined,
          due_at: dueAt || undefined,
        },
      });
    } else {
      createMut.mutate({
        title: title.trim(),
        task_type: taskType,
        description: description.trim() || undefined,
        due_at: dueAt || undefined,
      });
    }
  }

  const typeBadgeColor: Record<string, string> = {
    reminder: "bg-blue-100 text-blue-800",
    follow_up: "bg-purple-100 text-purple-800",
    schedule: "bg-green-100 text-green-800",
  };

  return (
    <div className="mx-auto max-w-4xl px-4 sm:px-6 py-6 sm:py-8 space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold tracking-tight">Tasks</h1>
        <Button
          onClick={() => {
            setEditingId(null);
            resetForm();
            setShowForm(!showForm);
          }}
          size="sm"
        >
          <Plus className="mr-1 h-4 w-4" />
          New Task
        </Button>
      </div>

      {/* Create/Edit Form */}
      {showForm && (
        <Card>
          <CardContent className="pt-6">
            <form onSubmit={handleSubmit} className="space-y-4">
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div className="space-y-2">
                  <Label htmlFor="title">Title</Label>
                  <Input
                    id="title"
                    value={title}
                    onChange={(e) => setTitle(e.target.value)}
                    placeholder="What needs to be done?"
                    required
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="taskType">Type</Label>
                  <Select value={taskType} onValueChange={setTaskType}>
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="reminder">Reminder</SelectItem>
                      <SelectItem value="follow_up">Follow-up</SelectItem>
                      <SelectItem value="schedule">Schedule</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
              </div>
              <div className="space-y-2">
                <Label htmlFor="description">Description</Label>
                <Textarea
                  id="description"
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  placeholder="Optional details..."
                  rows={2}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="dueAt">Due Date</Label>
                <Input
                  id="dueAt"
                  type="datetime-local"
                  value={dueAt}
                  onChange={(e) => setDueAt(e.target.value)}
                />
              </div>
              <div className="flex gap-2">
                <Button type="submit" disabled={createMut.isPending || updateMut.isPending}>
                  {(createMut.isPending || updateMut.isPending) && (
                    <Loader2 className="mr-1 h-4 w-4 animate-spin" />
                  )}
                  {editingId ? "Update" : "Create"}
                </Button>
                <Button type="button" variant="outline" onClick={resetForm}>
                  Cancel
                </Button>
              </div>
            </form>
          </CardContent>
        </Card>
      )}

      {/* Filter Tabs */}
      <div className="flex gap-2">
        {["active", "completed", "all"].map((s) => (
          <Button
            key={s}
            variant={filter === s ? "default" : "outline"}
            size="sm"
            onClick={() => setFilter(s)}
          >
            {s.charAt(0).toUpperCase() + s.slice(1)}
          </Button>
        ))}
      </div>

      {/* Task List */}
      {isLoading ? (
        <div className="flex justify-center py-12">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        </div>
      ) : tasks.length === 0 ? (
        <p className="text-center py-12 text-muted-foreground">
          No {filter === "all" ? "" : filter} tasks yet
        </p>
      ) : (
        <div className="space-y-3">
          {tasks.map((t) => (
            <Card
              key={t.id}
              className={
                t.completed
                  ? "opacity-60"
                  : t.due_at && new Date(t.due_at) < new Date()
                    ? "border-red-300 bg-red-50/50"
                    : ""
              }
            >
              <CardHeader className="pb-2">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <span className={`font-medium ${t.completed ? "line-through" : ""}`}>
                      {t.title}
                    </span>
                    <Badge className={typeBadgeColor[t.task_type] || "bg-gray-100 text-gray-800"}>
                      {t.task_type}
                    </Badge>
                    {!t.completed && t.due_at && new Date(t.due_at) < new Date() && (
                      <Badge className="bg-red-100 text-red-800">
                        <AlertTriangle className="h-3 w-3 mr-1" />
                        Overdue
                      </Badge>
                    )}
                  </div>
                  <div className="flex items-center gap-1">
                    {!t.completed && (
                      <>
                        <Button
                          size="icon"
                          variant="ghost"
                          onClick={() => startEdit(t)}
                          title="Edit"
                        >
                          <Pencil className="h-4 w-4" />
                        </Button>
                        <Button
                          size="icon"
                          variant="ghost"
                          onClick={() => completeMut.mutate(t.id)}
                          title="Complete"
                        >
                          <CheckCircle2 className="h-4 w-4 text-green-600" />
                        </Button>
                      </>
                    )}
                    <AlertDialog>
                      <AlertDialogTrigger asChild>
                        <Button size="icon" variant="ghost" title="Delete">
                          <Trash2 className="h-4 w-4 text-red-500" />
                        </Button>
                      </AlertDialogTrigger>
                      <AlertDialogContent>
                        <AlertDialogHeader>
                          <AlertDialogTitle>Delete task?</AlertDialogTitle>
                          <AlertDialogDescription>
                            This will permanently delete &quot;{t.title}&quot;.
                          </AlertDialogDescription>
                        </AlertDialogHeader>
                        <AlertDialogFooter>
                          <AlertDialogCancel>Cancel</AlertDialogCancel>
                          <AlertDialogAction onClick={() => deleteMut.mutate(t.id)}>
                            Delete
                          </AlertDialogAction>
                        </AlertDialogFooter>
                      </AlertDialogContent>
                    </AlertDialog>
                  </div>
                </div>
              </CardHeader>
              <CardContent className="pt-0">
                {t.description && (
                  <p className="text-sm text-muted-foreground mb-1">{t.description}</p>
                )}
                <div className="flex items-center gap-3 text-xs text-muted-foreground">
                  {t.due_at && (
                    <span className="flex items-center gap-1">
                      <Clock className="h-3 w-3" />
                      {new Date(t.due_at).toLocaleString()}
                    </span>
                  )}
                  <span>Created {new Date(t.created_at).toLocaleDateString()}</span>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
