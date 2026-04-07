/**
 * Migration dialog for legacy unscoped connections (D-05).
 *
 * Shows automatically when user has connections with persona_id = null.
 * Lets user assign each connection to a persona via Select dropdown.
 * "Assign Later" dismisses temporarily (reappears on next page load).
 * "Save Assignments" patches each connection and closes permanently.
 */
import { useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Loader2 } from "lucide-react";
import { fetchWithAuth } from "@/lib/api";
import { toast } from "sonner";
import { useMutation, useQueryClient } from "@tanstack/react-query";

// ---- Types ----

interface Persona {
  id: string;
  name: string;
  description: string | null;
  tone_notes: string | null;
  is_active: boolean;
  created_at: string;
}

interface Connection {
  id: string;
  provider: string;
  status: string;
  persona_id: string | null;
  capabilities?: Record<string, boolean>;
}

interface MigrationDialogProps {
  connections: Connection[]; // unassigned connections (persona_id === null)
  personas: Persona[];
}

// ---- Component ----

export function MigrationDialog({ connections, personas }: MigrationDialogProps) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(true);
  const [assignments, setAssignments] = useState<Record<string, string>>({});

  const allAssigned = connections.length > 0 && connections.every((c) => assignments[c.id]);

  const saveMutation = useMutation({
    mutationFn: async () => {
      const promises = connections
        .filter((c) => assignments[c.id])
        .map((c) =>
          fetchWithAuth(`/api/v1/connections/${c.id}`, {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ persona_id: assignments[c.id] }),
          }).then((res) => {
            if (!res.ok) throw new Error(`Failed to assign ${c.provider}`);
          })
        );
      await Promise.all(promises);
    },
    onSuccess: () => {
      toast.success("Connections assigned.");
      queryClient.invalidateQueries({ queryKey: ["connections"] });
      setOpen(false);
    },
    onError: (err: Error) => {
      toast.error(err.message);
    },
  });

  if (connections.length === 0) return null;

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Assign Your Connections</DialogTitle>
          <DialogDescription>
            You have connections that aren't linked to a persona. Assign each one so your assistant
            knows which context to use.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 py-4">
          {connections.map((conn) => (
            <div key={conn.id} className="flex items-center justify-between gap-4">
              <div className="flex items-center gap-2 min-w-0">
                <Badge variant="outline" className="capitalize shrink-0">
                  {conn.provider}
                </Badge>
                <span className="text-sm text-neutral-500 truncate">
                  {conn.status === "connected" ? "Connected" : conn.status}
                </span>
              </div>
              <Select
                value={assignments[conn.id] ?? ""}
                onValueChange={(value) =>
                  setAssignments((prev) => ({ ...prev, [conn.id]: value }))
                }
              >
                <SelectTrigger className="w-[160px]">
                  <SelectValue placeholder="Select persona" />
                </SelectTrigger>
                <SelectContent>
                  {personas.map((p) => (
                    <SelectItem key={p.id} value={p.id}>
                      {p.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          ))}
        </div>

        <DialogFooter className="gap-2 sm:gap-0">
          <Button variant="outline" onClick={() => setOpen(false)}>
            Assign Later
          </Button>
          <Button
            className="bg-blue-600 hover:bg-blue-700"
            disabled={!allAssigned || saveMutation.isPending}
            onClick={() => saveMutation.mutate()}
          >
            {saveMutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
            Save Assignments
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
