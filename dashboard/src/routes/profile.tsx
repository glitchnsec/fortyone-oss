/**
 * Profile page -- manage TELOS self-knowledge entries.
 *
 * TELOS sections: problems, mission, goals, challenges, wisdom,
 * ideas, predictions, preferences, narratives, history.
 *
 * CRUD via /api/v1/profile. Entries grouped by section.
 * Users can add labelled notes about themselves.
 */
import { useState } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Loader2, Plus, Trash2, User } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
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

export const Route = createFileRoute("/profile")({
  component: ProfilePage,
});

// ---- Types ----

interface ProfileEntry {
  id: string;
  section: string;
  label: string;
  content: string;
  persona_id: string | null;
  created_at: string;
}

interface ProfileResponse {
  entries: ProfileEntry[];
}

const TELOS_SECTIONS = [
  { value: "preferences", label: "Preferences", description: "How you like things done" },
  { value: "mission", label: "Mission", description: "What drives you" },
  { value: "goals", label: "Goals", description: "What you're working toward" },
  { value: "challenges", label: "Challenges", description: "What you're struggling with" },
  { value: "problems", label: "Problems", description: "Issues to solve" },
  { value: "wisdom", label: "Wisdom", description: "Lessons learned" },
  { value: "ideas", label: "Ideas", description: "Things to explore" },
  { value: "predictions", label: "Predictions", description: "What you expect to happen" },
  { value: "narratives", label: "Narratives", description: "Your story and context" },
  { value: "history", label: "History", description: "Key life events" },
] as const;

const sectionColors: Record<string, string> = {
  preferences: "bg-blue-100 text-blue-800",
  mission: "bg-purple-100 text-purple-800",
  goals: "bg-green-100 text-green-800",
  challenges: "bg-orange-100 text-orange-800",
  problems: "bg-red-100 text-red-800",
  wisdom: "bg-yellow-100 text-yellow-800",
  ideas: "bg-cyan-100 text-cyan-800",
  predictions: "bg-indigo-100 text-indigo-800",
  narratives: "bg-pink-100 text-pink-800",
  history: "bg-gray-100 text-gray-800",
};

// ---- API helpers ----

async function fetchProfile(section?: string): Promise<ProfileEntry[]> {
  const url = section ? `/api/v1/profile?section=${section}` : "/api/v1/profile";
  const res = await fetchWithAuth(url);
  if (!res.ok) throw new Error("Failed to fetch profile");
  const data: ProfileResponse = await res.json();
  return data.entries;
}

async function upsertEntry(body: { section: string; label: string; content: string }) {
  const res = await fetchWithAuth("/api/v1/profile", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error("Failed to save entry");
  return res.json();
}

async function deleteEntry(id: string) {
  const res = await fetchWithAuth(`/api/v1/profile/${id}`, { method: "DELETE" });
  if (!res.ok) throw new Error("Failed to delete entry");
}

// ---- Component ----

function ProfilePage() {
  const qc = useQueryClient();
  const [filterSection, setFilterSection] = useState<string>("all");
  const [showForm, setShowForm] = useState(false);

  // Form state
  const [section, setSection] = useState("preferences");
  const [label, setLabel] = useState("");
  const [content, setContent] = useState("");

  const { data: entries = [], isLoading } = useQuery({
    queryKey: ["profile", filterSection],
    queryFn: () => fetchProfile(filterSection === "all" ? undefined : filterSection),
  });

  const upsertMut = useMutation({
    mutationFn: upsertEntry,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["profile"] });
      resetForm();
      toast.success("Profile entry saved");
    },
    onError: () => toast.error("Failed to save entry"),
  });

  const deleteMut = useMutation({
    mutationFn: deleteEntry,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["profile"] });
      toast.success("Entry deleted");
    },
  });

  function resetForm() {
    setLabel("");
    setContent("");
    setShowForm(false);
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!label.trim() || !content.trim()) return;
    upsertMut.mutate({
      section,
      label: label.trim(),
      content: content.trim(),
    });
  }

  // Group entries by section for display
  const grouped = entries.reduce<Record<string, ProfileEntry[]>>((acc, e) => {
    if (!acc[e.section]) acc[e.section] = [];
    acc[e.section].push(e);
    return acc;
  }, {});

  return (
    <div className="mx-auto max-w-4xl px-4 sm:px-6 py-6 sm:py-8 space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Profile</h1>
          <p className="text-sm text-muted-foreground mt-1">
            Your self-knowledge — what the assistant knows about you
          </p>
        </div>
        <Button
          onClick={() => {
            resetForm();
            setShowForm(!showForm);
          }}
          size="sm"
        >
          <Plus className="mr-1 h-4 w-4" />
          Add Entry
        </Button>
      </div>

      {/* Create Form */}
      {showForm && (
        <Card>
          <CardContent className="pt-6">
            <form onSubmit={handleSubmit} className="space-y-4">
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div className="space-y-2">
                  <Label htmlFor="section">Section</Label>
                  <Select value={section} onValueChange={setSection}>
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {TELOS_SECTIONS.map((s) => (
                        <SelectItem key={s.value} value={s.value}>
                          {s.label} — {s.description}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-2">
                  <Label htmlFor="label">Label</Label>
                  <Input
                    id="label"
                    value={label}
                    onChange={(e) => setLabel(e.target.value)}
                    placeholder="e.g., communication style, career goal"
                    required
                  />
                </div>
              </div>
              <div className="space-y-2">
                <Label htmlFor="content">Content</Label>
                <Textarea
                  id="content"
                  value={content}
                  onChange={(e) => setContent(e.target.value)}
                  placeholder="What should the assistant know?"
                  rows={3}
                  required
                />
              </div>
              <div className="flex gap-2">
                <Button type="submit" disabled={upsertMut.isPending}>
                  {upsertMut.isPending && <Loader2 className="mr-1 h-4 w-4 animate-spin" />}
                  Save
                </Button>
                <Button type="button" variant="outline" onClick={resetForm}>
                  Cancel
                </Button>
              </div>
            </form>
          </CardContent>
        </Card>
      )}

      {/* Section Filter */}
      <div className="flex gap-2 flex-wrap">
        <Button
          variant={filterSection === "all" ? "default" : "outline"}
          size="sm"
          onClick={() => setFilterSection("all")}
        >
          All
        </Button>
        {TELOS_SECTIONS.map((s) => (
          <Button
            key={s.value}
            variant={filterSection === s.value ? "default" : "outline"}
            size="sm"
            onClick={() => setFilterSection(s.value)}
          >
            {s.label}
          </Button>
        ))}
      </div>

      {/* Entries */}
      {isLoading ? (
        <div className="flex justify-center py-12">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        </div>
      ) : entries.length === 0 ? (
        <div className="text-center py-12">
          <User className="mx-auto h-12 w-12 text-muted-foreground/50 mb-3" />
          <p className="text-muted-foreground">
            No profile entries yet. Add what you want the assistant to know about you.
          </p>
        </div>
      ) : filterSection === "all" ? (
        // Grouped view
        <div className="space-y-6">
          {Object.entries(grouped).map(([sectionName, sectionEntries]) => (
            <div key={sectionName}>
              <h2 className="text-lg font-semibold mb-3 capitalize">{sectionName}</h2>
              <div className="space-y-2">
                {sectionEntries.map((e) => (
                  <EntryCard key={e.id} entry={e} onDelete={() => deleteMut.mutate(e.id)} />
                ))}
              </div>
            </div>
          ))}
        </div>
      ) : (
        // Flat view for single section
        <div className="space-y-2">
          {entries.map((e) => (
            <EntryCard key={e.id} entry={e} onDelete={() => deleteMut.mutate(e.id)} />
          ))}
        </div>
      )}
    </div>
  );
}

function EntryCard({ entry, onDelete }: { entry: ProfileEntry; onDelete: () => void }) {
  return (
    <Card>
      <CardContent className="py-3 px-4">
        <div className="flex items-start justify-between">
          <div className="flex-1">
            <div className="flex items-center gap-2 mb-1">
              <Badge className={sectionColors[entry.section] || "bg-gray-100 text-gray-800"}>
                {entry.section}
              </Badge>
              <span className="font-medium text-sm">{entry.label}</span>
            </div>
            <p className="text-sm text-muted-foreground">{entry.content}</p>
          </div>
          <AlertDialog>
            <AlertDialogTrigger asChild>
              <Button size="icon" variant="ghost" className="shrink-0 ml-2" title="Delete">
                <Trash2 className="h-4 w-4 text-red-500" />
              </Button>
            </AlertDialogTrigger>
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>Delete profile entry?</AlertDialogTitle>
                <AlertDialogDescription>
                  This will remove &quot;{entry.label}&quot; from your profile.
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel>Cancel</AlertDialogCancel>
                <AlertDialogAction onClick={onDelete}>Delete</AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
        </div>
      </CardContent>
    </Card>
  );
}
