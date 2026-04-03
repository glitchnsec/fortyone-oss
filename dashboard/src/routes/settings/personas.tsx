/**
 * Personas settings page -- manage identity contexts (work, personal, custom).
 *
 * CRUD operations via /api/v1/personas.
 * Create form for new personas. Inline editing. Delete with confirmation.
 */
import { useState, useEffect } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Loader2, Plus, Trash2, Pencil, X, Check } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
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

export const Route = createFileRoute("/settings/personas")({
  component: PersonasSettingsPage,
});

// ---- Types ----

interface Persona {
  id: string;
  name: string;
  description: string | null;
  tone_notes: string | null;
  is_active: boolean;
  created_at: string;
}

interface PersonasResponse {
  personas: Persona[];
}

// ---- Page ----

function PersonasSettingsPage() {
  const queryClient = useQueryClient();
  const [showForm, setShowForm] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);

  // Create form state
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [toneNotes, setToneNotes] = useState("");

  // Edit form state
  const [editName, setEditName] = useState("");
  const [editDescription, setEditDescription] = useState("");
  const [editToneNotes, setEditToneNotes] = useState("");

  const { data, isLoading } = useQuery<PersonasResponse>({
    queryKey: ["personas"],
    queryFn: () =>
      fetchWithAuth("/api/v1/personas").then((r) => r.json()) as Promise<PersonasResponse>,
  });

  const createMutation = useMutation({
    mutationFn: async () => {
      const res = await fetchWithAuth("/api/v1/personas", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: name.trim(),
          description: description.trim() || undefined,
          tone_notes: toneNotes.trim() || undefined,
        }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({})) as { detail?: string };
        throw new Error(err.detail ?? "Failed to create persona.");
      }
      return res.json();
    },
    onSuccess: () => {
      toast.success("Persona created.");
      setName("");
      setDescription("");
      setToneNotes("");
      setShowForm(false);
      queryClient.invalidateQueries({ queryKey: ["personas"] });
    },
    onError: (err: Error) => toast.error(err.message),
  });

  const updateMutation = useMutation({
    mutationFn: async ({ personaId, updates }: { personaId: string; updates: Record<string, unknown> }) => {
      const res = await fetchWithAuth(`/api/v1/personas/${personaId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(updates),
      });
      if (!res.ok) throw new Error("Failed to update persona.");
      return res.json();
    },
    onSuccess: () => {
      toast.success("Persona updated.");
      setEditingId(null);
      queryClient.invalidateQueries({ queryKey: ["personas"] });
    },
    onError: (err: Error) => toast.error(err.message),
  });

  const deleteMutation = useMutation({
    mutationFn: async (personaId: string) => {
      const res = await fetchWithAuth(`/api/v1/personas/${personaId}`, { method: "DELETE" });
      if (!res.ok && res.status !== 204) throw new Error("Failed to delete persona.");
    },
    onSuccess: () => {
      toast.success("Persona deleted.");
      queryClient.invalidateQueries({ queryKey: ["personas"] });
    },
    onError: (err: Error) => toast.error(err.message),
  });

  const handleCreate = (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) {
      toast.error("Name is required.");
      return;
    }
    createMutation.mutate();
  };

  const startEditing = (persona: Persona) => {
    setEditingId(persona.id);
    setEditName(persona.name);
    setEditDescription(persona.description ?? "");
    setEditToneNotes(persona.tone_notes ?? "");
  };

  const saveEdit = (personaId: string) => {
    updateMutation.mutate({
      personaId,
      updates: {
        name: editName.trim(),
        description: editDescription.trim() || undefined,
        tone_notes: editToneNotes.trim() || undefined,
      },
    });
  };

  const personas = data?.personas ?? [];

  return (
    <div className="mx-auto max-w-2xl px-6 py-8">
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-xl font-semibold text-neutral-900">Personas</h1>
        <Button
          className="bg-blue-600 hover:bg-blue-700"
          onClick={() => setShowForm(!showForm)}
        >
          <Plus className="mr-2 h-4 w-4" />
          New Persona
        </Button>
      </div>

      <p className="mb-6 text-sm text-neutral-500">
        Personas define different identity contexts (e.g. work, personal) that shape how your assistant communicates.
      </p>

      {/* Create form */}
      {showForm && (
        <Card className="mb-6 border border-neutral-200">
          <CardContent className="pt-6">
            <form onSubmit={handleCreate} className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="persona-name">Name</Label>
                <Input
                  id="persona-name"
                  placeholder='e.g. "Work" or "Personal"'
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="persona-desc">Description</Label>
                <Textarea
                  id="persona-desc"
                  placeholder="Brief description of this persona context..."
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  rows={2}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="persona-tone">Tone notes</Label>
                <Textarea
                  id="persona-tone"
                  placeholder='e.g. "Formal and concise in work emails"'
                  value={toneNotes}
                  onChange={(e) => setToneNotes(e.target.value)}
                  rows={2}
                />
              </div>
              <div className="flex gap-2">
                <Button
                  type="submit"
                  className="bg-blue-600 hover:bg-blue-700"
                  disabled={createMutation.isPending}
                >
                  {createMutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                  Create Persona
                </Button>
                <Button type="button" variant="outline" onClick={() => setShowForm(false)}>
                  Cancel
                </Button>
              </div>
            </form>
          </CardContent>
        </Card>
      )}

      {/* Loading */}
      {isLoading && (
        <div className="flex h-32 items-center justify-center">
          <Loader2 className="h-6 w-6 animate-spin text-neutral-400" />
        </div>
      )}

      {/* Empty state */}
      {!isLoading && personas.length === 0 && (
        <div className="flex flex-col items-center gap-2 py-12 text-center">
          <p className="text-sm text-neutral-500">No personas yet. Create one to get started.</p>
        </div>
      )}

      {/* Personas list */}
      <div className="space-y-3">
        {personas.map((persona) => (
          <Card key={persona.id} className="border border-neutral-200">
            <CardContent className="pt-4 pb-4">
              {editingId === persona.id ? (
                /* Inline edit form */
                <div className="space-y-3">
                  <Input
                    value={editName}
                    onChange={(e) => setEditName(e.target.value)}
                    placeholder="Persona name"
                  />
                  <Textarea
                    value={editDescription}
                    onChange={(e) => setEditDescription(e.target.value)}
                    placeholder="Description"
                    rows={2}
                  />
                  <Textarea
                    value={editToneNotes}
                    onChange={(e) => setEditToneNotes(e.target.value)}
                    placeholder="Tone notes"
                    rows={2}
                  />
                  <div className="flex gap-2">
                    <Button
                      size="sm"
                      className="bg-blue-600 hover:bg-blue-700"
                      onClick={() => saveEdit(persona.id)}
                      disabled={updateMutation.isPending}
                    >
                      {updateMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="mr-1 h-4 w-4" />}
                      Save
                    </Button>
                    <Button size="sm" variant="outline" onClick={() => setEditingId(null)}>
                      <X className="mr-1 h-4 w-4" />
                      Cancel
                    </Button>
                  </div>
                </div>
              ) : (
                /* Display mode */
                <div className="flex items-start justify-between">
                  <div className="flex-1">
                    <div className="flex items-center gap-2">
                      <h3 className="text-sm font-medium text-neutral-900">{persona.name}</h3>
                      <Badge className={persona.is_active ? "bg-green-100 text-green-700 hover:bg-green-100" : "bg-neutral-100 text-neutral-500 hover:bg-neutral-100"}>
                        {persona.is_active ? "Active" : "Inactive"}
                      </Badge>
                    </div>
                    {persona.description && (
                      <p className="mt-1 text-xs text-neutral-500">{persona.description}</p>
                    )}
                    {persona.tone_notes && (
                      <p className="mt-1 text-xs text-neutral-400 italic">{persona.tone_notes}</p>
                    )}
                  </div>
                  <div className="flex gap-1">
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-8 w-8 p-0 text-neutral-500 hover:text-neutral-700"
                      onClick={() => startEditing(persona)}
                      title="Edit"
                    >
                      <Pencil className="h-4 w-4" />
                    </Button>
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
                          <AlertDialogTitle>Delete persona?</AlertDialogTitle>
                          <AlertDialogDescription>
                            This will permanently delete the "{persona.name}" persona.
                          </AlertDialogDescription>
                        </AlertDialogHeader>
                        <AlertDialogFooter>
                          <AlertDialogCancel>Cancel</AlertDialogCancel>
                          <AlertDialogAction
                            onClick={() => deleteMutation.mutate(persona.id)}
                            className="bg-red-600 hover:bg-red-700"
                          >
                            Delete
                          </AlertDialogAction>
                        </AlertDialogFooter>
                      </AlertDialogContent>
                    </AlertDialog>
                  </div>
                </div>
              )}
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  );
}
