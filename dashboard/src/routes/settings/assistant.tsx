/**
 * Assistant Settings page — edit assistant name and personality notes.
 *
 * Fetches GET /api/v1/me for current assistant_name.
 * PATCH /api/v1/me/assistant on save.
 * Inline validation on blur: "This field is required." if assistant_name empty.
 * "Save changes" button with spinner while submitting; disabled during submission.
 * Success toast: "Changes saved."
 */
import { useState, useEffect } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { fetchWithAuth } from "@/lib/api";

export const Route = createFileRoute("/settings/assistant")({
  component: AssistantSettingsPage,
});

interface MeResponse {
  user_id: string;
  email: string | null;
  phone: string;
  phone_verified: boolean;
  assistant_name: string | null;
  personality_notes: string | null;
}

function AssistantSettingsPage() {
  const queryClient = useQueryClient();

  const { data } = useQuery<MeResponse>({
    queryKey: ["me"],
    queryFn: () =>
      fetchWithAuth("/api/v1/me").then((r) => r.json()) as Promise<MeResponse>,
  });

  const [assistantName, setAssistantName] = useState("");
  const [personality, setPersonality] = useState("");
  const [nameError, setNameError] = useState<string | null>(null);

  // Populate form once data loads
  useEffect(() => {
    if (data?.assistant_name) setAssistantName(data.assistant_name);
    if (data?.personality_notes) setPersonality(data.personality_notes);
  }, [data]);

  const saveMutation = useMutation({
    mutationFn: async () => {
      const res = await fetchWithAuth("/api/v1/me/assistant", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          assistant_name: assistantName.trim(),
          personality_notes: personality.trim() || undefined,
        }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({})) as { detail?: string };
        throw new Error(err.detail ?? "Failed to save changes.");
      }
      return res.json();
    },
    onSuccess: () => {
      toast.success("Changes saved.");
      queryClient.invalidateQueries({ queryKey: ["me"] });
    },
    onError: (err: Error) => {
      toast.error(err.message);
    },
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setNameError(null);
    if (!assistantName.trim()) {
      setNameError("This field is required.");
      return;
    }
    saveMutation.mutate();
  };

  return (
    <div className="mx-auto max-w-2xl px-6 py-8">
      <h1 className="mb-6 text-xl font-semibold text-neutral-900">Assistant Settings</h1>

      <form onSubmit={handleSubmit} className="space-y-6">
        <div className="space-y-2">
          <Label htmlFor="assistant-name">Assistant name</Label>
          <Input
            id="assistant-name"
            type="text"
            placeholder="e.g. Alex"
            value={assistantName}
            onChange={(e) => setAssistantName(e.target.value)}
            onBlur={() => {
              if (!assistantName.trim()) setNameError("This field is required.");
              else setNameError(null);
            }}
            aria-describedby={nameError ? "name-error" : undefined}
          />
          {nameError && (
            <p id="name-error" className="text-sm text-red-600" role="alert">
              {nameError}
            </p>
          )}
        </div>

        <div className="space-y-2">
          <Label htmlFor="personality">Personality notes</Label>
          <Textarea
            id="personality"
            placeholder="Describe how you'd like your assistant to communicate (tone, style, preferences)..."
            value={personality}
            onChange={(e) => setPersonality(e.target.value)}
            rows={4}
          />
          <p className="text-xs text-neutral-500">Optional — guides how your assistant responds to you.</p>
        </div>

        <Button
          type="submit"
          className="bg-blue-600 hover:bg-blue-700"
          disabled={saveMutation.isPending}
        >
          {saveMutation.isPending && (
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
          )}
          Save changes
        </Button>
      </form>
    </div>
  );
}
