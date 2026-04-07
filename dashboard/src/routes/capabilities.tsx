/**
 * Capabilities page -- view built-in subagent capabilities (read-only,
 * persona-aware status) and manage custom agents (create/edit/delete).
 *
 * Two sections:
 *   1. Built-in Capabilities -- read-only cards for each subagent with tool
 *      lists and persona connection status badges.
 *   2. My Custom Agents -- CRUD via dialog form. Supports webhook, prompt,
 *      and YAML/script agent types.
 *
 * Data:
 *   GET /api/v1/capabilities   -- built-in subagent list
 *   GET /api/v1/custom-agents  -- user's custom agents
 *   POST/PUT/PATCH/DELETE /api/v1/custom-agents -- CRUD mutations
 */
import { useState } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  Loader2,
  Plus,
  Pencil,
  Trash2,
  AlertCircle,
  Info,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardHeader,
} from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Switch } from "@/components/ui/switch";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { fetchWithAuth } from "@/lib/api";

export const Route = createFileRoute("/capabilities")({
  component: CapabilitiesPage,
});

// ---- Types ----

interface Tool {
  name: string;
  description: string;
  risk_level: "low" | "medium" | "high";
}

interface PersonaStatus {
  persona_id: string;
  persona_name: string;
  status: "connected" | "not_connected" | "no_connection_needed";
}

interface Capability {
  name: string;
  description: string;
  tools: Tool[];
  persona_status: PersonaStatus[];
}

interface CapabilitiesResponse {
  capabilities: Capability[];
}

interface CustomAgent {
  id: string;
  name: string;
  description: string;
  agent_type: "webhook" | "prompt" | "yaml_script";
  config: Record<string, unknown>;
  parameters_schema: Record<string, unknown> | null;
  risk_level: "low" | "medium" | "high";
  enabled: boolean;
  created_at: string;
  updated_at: string;
}

interface CustomAgentsResponse {
  agents: CustomAgent[];
}

interface AgentFormData {
  name: string;
  description: string;
  agent_type: "webhook" | "prompt" | "yaml_script";
  webhook_url: string;
  system_prompt: string;
  yaml_content: string;
  parameters_schema: string;
  risk_level: "low" | "medium" | "high";
}

const INITIAL_FORM: AgentFormData = {
  name: "",
  description: "",
  agent_type: "webhook",
  webhook_url: "",
  system_prompt: "",
  yaml_content: "",
  parameters_schema: "",
  risk_level: "low",
};

// ---- Helpers ----

function formatAgentName(name: string): string {
  return name.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function tryParseJSON(value: string): boolean {
  if (!value.trim()) return true; // empty is valid (optional)
  try {
    JSON.parse(value);
    return true;
  } catch {
    return false;
  }
}

function isValidHttpsUrl(value: string): boolean {
  try {
    const url = new URL(value);
    return url.protocol === "https:";
  } catch {
    return false;
  }
}

function RiskBadge({ level }: { level: string }) {
  if (level === "high") {
    return (
      <Badge className="bg-red-50 text-red-700 border-red-200">
        High risk
      </Badge>
    );
  }
  if (level === "medium") {
    return (
      <Badge className="bg-amber-50 text-amber-700 border-amber-200">
        Medium risk
      </Badge>
    );
  }
  return <Badge variant="secondary">Low risk</Badge>;
}

function typeLabel(t: string): string {
  if (t === "yaml_script") return "Script";
  return t.charAt(0).toUpperCase() + t.slice(1);
}

// ---- CapabilityCard ----

function CapabilityCard({ cap }: { cap: Capability }) {
  const visiblePersonas = cap.persona_status.filter(
    (p) => p.status !== "no_connection_needed",
  );

  return (
    <Card>
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between">
          <span className="text-sm font-semibold">{formatAgentName(cap.name)}</span>
          <Badge variant="secondary">
            {cap.tools.length} {cap.tools.length === 1 ? "tool" : "tools"}
          </Badge>
        </div>
        <p className="text-xs text-muted-foreground mt-1">{cap.description}</p>
      </CardHeader>
      <CardContent>
        <ul className="space-y-1.5">
          {cap.tools.map((tool) => (
            <li key={tool.name} className="flex items-start gap-2 text-xs">
              <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-neutral-400" />
              <span className="flex-1">
                <span className="font-medium">{tool.name}</span>
                {tool.description && (
                  <span className="text-muted-foreground"> &mdash; {tool.description}</span>
                )}
              </span>
              <RiskBadge level={tool.risk_level} />
            </li>
          ))}
        </ul>
        {visiblePersonas.length > 0 && (
          <>
            <Separator className="my-4" />
            <div className="flex flex-wrap gap-2">
              {visiblePersonas.map((p) =>
                p.status === "connected" ? (
                  <Badge
                    key={p.persona_id}
                    className="bg-emerald-50 text-emerald-600"
                    aria-label={`${p.persona_name}: Connected`}
                  >
                    {p.persona_name}: Connected
                  </Badge>
                ) : (
                  <Badge
                    key={p.persona_id}
                    variant="outline"
                    className="text-neutral-500"
                    aria-label={`${p.persona_name}: Not connected`}
                  >
                    {p.persona_name}: Not connected
                  </Badge>
                ),
              )}
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
}

// ---- CustomAgentCard ----

function CustomAgentCard({
  agent,
  onEdit,
  onDelete,
  onToggle,
}: {
  agent: CustomAgent;
  onEdit: () => void;
  onDelete: () => void;
  onToggle: () => void;
}) {
  const isYaml = agent.agent_type === "yaml_script";

  return (
    <Card>
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between">
          <span className="text-sm font-semibold">{agent.name}</span>
          {isYaml ? (
            <TooltipProvider>
              <Tooltip>
                <TooltipTrigger asChild>
                  <div>
                    <Switch
                      checked={agent.enabled}
                      disabled
                      aria-label={`Enable ${agent.name}`}
                    />
                  </div>
                </TooltipTrigger>
                <TooltipContent>Sandbox required for execution</TooltipContent>
              </Tooltip>
            </TooltipProvider>
          ) : (
            <Switch
              checked={agent.enabled}
              onCheckedChange={() => onToggle()}
              aria-label={`Enable ${agent.name}`}
            />
          )}
        </div>
        <div className="flex flex-wrap gap-2 mt-1">
          <Badge variant="secondary">{typeLabel(agent.agent_type)}</Badge>
          <RiskBadge level={agent.risk_level} />
          {isYaml && (
            <Badge className="bg-amber-50 text-amber-700">Requires sandbox</Badge>
          )}
        </div>
      </CardHeader>
      <CardContent>
        {agent.description && (
          <p className="text-xs text-muted-foreground mb-4">{agent.description}</p>
        )}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" size="sm" onClick={onEdit}>
            <Pencil className="mr-1 h-3.5 w-3.5" />
            Edit
          </Button>
          <Button
            variant="ghost"
            size="sm"
            className="text-red-600 hover:text-red-700"
            onClick={onDelete}
          >
            <Trash2 className="mr-1 h-3.5 w-3.5" />
            Delete
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

// ---- CustomAgentDialog ----

function CustomAgentDialog({
  open,
  onOpenChange,
  editAgent,
  onSuccess,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  editAgent: CustomAgent | null;
  onSuccess: () => void;
}) {
  const isEdit = editAgent !== null;
  const [form, setForm] = useState<AgentFormData>(INITIAL_FORM);
  const [errors, setErrors] = useState<Partial<Record<keyof AgentFormData, string>>>({});

  // Populate form when editing
  const populateForm = (agent: CustomAgent | null) => {
    if (!agent) {
      setForm(INITIAL_FORM);
      setErrors({});
      return;
    }
    setForm({
      name: agent.name,
      description: agent.description || "",
      agent_type: agent.agent_type,
      webhook_url: (agent.config?.url as string) || "",
      system_prompt: (agent.config?.system_prompt as string) || "",
      yaml_content: (agent.config?.yaml as string) || "",
      parameters_schema: agent.parameters_schema
        ? JSON.stringify(agent.parameters_schema, null, 2)
        : "",
      risk_level: agent.risk_level,
    });
    setErrors({});
  };

  // Reset form when dialog opens/closes or editAgent changes
  const handleOpenChange = (nextOpen: boolean) => {
    if (nextOpen) {
      populateForm(editAgent);
    }
    onOpenChange(nextOpen);
  };

  // Also populate when editAgent changes while open
  useState(() => {
    if (open) populateForm(editAgent);
  });

  const validate = (): boolean => {
    const errs: Partial<Record<keyof AgentFormData, string>> = {};
    if (!form.name.trim()) errs.name = "Name is required.";
    if (form.description.length > 200)
      errs.description = "Description must be 200 characters or less.";
    if (form.agent_type === "webhook" && !isValidHttpsUrl(form.webhook_url))
      errs.webhook_url = "A valid HTTPS URL is required.";
    if (form.agent_type === "prompt" && form.system_prompt.trim().length < 10)
      errs.system_prompt = "Prompt must be at least 10 characters.";
    if (form.parameters_schema.trim() && !tryParseJSON(form.parameters_schema))
      errs.parameters_schema = "Must be valid JSON.";
    setErrors(errs);
    return Object.keys(errs).length === 0;
  };

  const mutation = useMutation({
    mutationFn: async () => {
      if (!validate()) throw new Error("Validation failed");

      const config: Record<string, string> = {};
      if (form.agent_type === "webhook") config.url = form.webhook_url;
      if (form.agent_type === "prompt") config.system_prompt = form.system_prompt;
      if (form.agent_type === "yaml_script") config.yaml = form.yaml_content;

      const body = {
        name: form.name.trim(),
        description: form.description.trim(),
        agent_type: form.agent_type,
        config,
        parameters_schema: form.parameters_schema.trim()
          ? JSON.parse(form.parameters_schema)
          : null,
        risk_level: form.risk_level,
      };

      const url = isEdit
        ? `/api/v1/custom-agents/${editAgent!.id}`
        : "/api/v1/custom-agents";
      const method = isEdit ? "PUT" : "POST";

      const res = await fetchWithAuth(url, {
        method,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const err = (await res.json().catch(() => ({}))) as { detail?: string };
        throw new Error(err.detail ?? "Failed to save agent.");
      }
      return res.json();
    },
    onSuccess: () => {
      toast.success(isEdit ? "Agent updated" : "Agent created");
      onOpenChange(false);
      onSuccess();
    },
    onError: (err: Error) => {
      if (err.message !== "Validation failed") {
        toast.error("Failed to save agent. Please try again.");
      }
    },
  });

  const setField = <K extends keyof AgentFormData>(key: K, value: AgentFormData[K]) => {
    setForm((prev) => ({ ...prev, [key]: value }));
    if (errors[key]) setErrors((prev) => ({ ...prev, [key]: undefined }));
  };

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>{isEdit ? "Edit Agent" : "Create Agent"}</DialogTitle>
          <DialogDescription>
            {isEdit
              ? "Update your custom agent configuration."
              : "Configure a new custom agent for your assistant."}
          </DialogDescription>
        </DialogHeader>

        <form
          onSubmit={(e) => {
            e.preventDefault();
            mutation.mutate();
          }}
          className="space-y-4"
        >
          {/* Name */}
          <div className="space-y-2">
            <Label htmlFor="agent-name">Name</Label>
            <Input
              id="agent-name"
              value={form.name}
              onChange={(e) => setField("name", e.target.value)}
              placeholder="My Agent"
            />
            {errors.name && (
              <p className="text-xs text-red-600">{errors.name}</p>
            )}
          </div>

          {/* Description */}
          <div className="space-y-2">
            <Label htmlFor="agent-desc">Description</Label>
            <Textarea
              id="agent-desc"
              value={form.description}
              onChange={(e) => setField("description", e.target.value)}
              placeholder="What does this agent do?"
              rows={2}
              maxLength={200}
            />
            <p className="text-xs text-muted-foreground">
              {form.description.length}/200
            </p>
            {errors.description && (
              <p className="text-xs text-red-600">{errors.description}</p>
            )}
          </div>

          {/* Agent Type */}
          <div className="space-y-2">
            <Label>Type</Label>
            <Select
              value={form.agent_type}
              onValueChange={(v) =>
                setField("agent_type", v as AgentFormData["agent_type"])
              }
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="webhook">Webhook</SelectItem>
                <SelectItem value="prompt">Prompt</SelectItem>
                <SelectItem value="yaml_script">YAML/Script</SelectItem>
              </SelectContent>
            </Select>
          </div>

          {/* Type-specific fields */}
          {form.agent_type === "webhook" && (
            <div className="space-y-2">
              <Label htmlFor="agent-url">Webhook URL</Label>
              <Input
                id="agent-url"
                value={form.webhook_url}
                onChange={(e) => setField("webhook_url", e.target.value)}
                placeholder="https://example.com/hook"
                type="url"
              />
              {errors.webhook_url && (
                <p className="text-xs text-red-600">{errors.webhook_url}</p>
              )}
            </div>
          )}

          {form.agent_type === "prompt" && (
            <div className="space-y-2">
              <Label htmlFor="agent-prompt">System Prompt</Label>
              <Textarea
                id="agent-prompt"
                value={form.system_prompt}
                onChange={(e) => setField("system_prompt", e.target.value)}
                placeholder="You are a helpful assistant that..."
                rows={4}
              />
              {errors.system_prompt && (
                <p className="text-xs text-red-600">{errors.system_prompt}</p>
              )}
            </div>
          )}

          {form.agent_type === "yaml_script" && (
            <div className="space-y-2">
              <Label htmlFor="agent-yaml">YAML Content</Label>
              <Textarea
                id="agent-yaml"
                value={form.yaml_content}
                onChange={(e) => setField("yaml_content", e.target.value)}
                placeholder="steps:&#10;  - name: step1&#10;    action: ..."
                rows={4}
                className="font-mono text-sm"
              />
              <Alert>
                <Info className="h-4 w-4" />
                <AlertDescription>
                  Script execution requires the sandbox feature (coming soon).
                  Your agent configuration will be saved.
                </AlertDescription>
              </Alert>
            </div>
          )}

          {/* Parameters Schema */}
          <div className="space-y-2">
            <Label htmlFor="agent-params">Parameters Schema (optional)</Label>
            <Textarea
              id="agent-params"
              value={form.parameters_schema}
              onChange={(e) => setField("parameters_schema", e.target.value)}
              placeholder='{"type": "object", "properties": {...}}'
              rows={3}
              className="font-mono text-sm"
            />
            {errors.parameters_schema && (
              <p className="text-xs text-red-600">{errors.parameters_schema}</p>
            )}
          </div>

          {/* Risk Level */}
          <div className="space-y-2">
            <Label>Risk Level</Label>
            <Select
              value={form.risk_level}
              onValueChange={(v) =>
                setField("risk_level", v as AgentFormData["risk_level"])
              }
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="low">Low</SelectItem>
                <SelectItem value="medium">Medium</SelectItem>
                <SelectItem value="high">High</SelectItem>
              </SelectContent>
            </Select>
          </div>

          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => onOpenChange(false)}
            >
              Cancel
            </Button>
            <Button type="submit" disabled={mutation.isPending}>
              {mutation.isPending && (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              )}
              {mutation.isPending
                ? isEdit
                  ? "Updating..."
                  : "Creating..."
                : isEdit
                  ? "Update Agent"
                  : "Create Agent"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

// ---- Page ----

function CapabilitiesPage() {
  const queryClient = useQueryClient();

  // Dialog state
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editAgent, setEditAgent] = useState<CustomAgent | null>(null);

  // Delete confirmation state
  const [deleteTarget, setDeleteTarget] = useState<CustomAgent | null>(null);

  // Fetch built-in capabilities
  const capabilitiesQuery = useQuery<CapabilitiesResponse>({
    queryKey: ["capabilities"],
    queryFn: () =>
      fetchWithAuth("/api/v1/capabilities").then((r) => r.json()) as Promise<CapabilitiesResponse>,
  });

  // Fetch custom agents
  const customAgentsQuery = useQuery<CustomAgentsResponse>({
    queryKey: ["custom-agents"],
    queryFn: () =>
      fetchWithAuth("/api/v1/custom-agents").then((r) => r.json()) as Promise<CustomAgentsResponse>,
  });

  // Toggle enabled mutation
  const toggleMutation = useMutation({
    mutationFn: async ({ id, enabled }: { id: string; enabled: boolean }) => {
      const res = await fetchWithAuth(`/api/v1/custom-agents/${id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled }),
      });
      if (!res.ok) throw new Error("Failed to toggle agent.");
      return res.json();
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["custom-agents"] });
    },
    onError: () => toast.error("Failed to update agent. Please try again."),
  });

  // Delete mutation
  const deleteMutation = useMutation({
    mutationFn: async (id: string) => {
      const res = await fetchWithAuth(`/api/v1/custom-agents/${id}`, {
        method: "DELETE",
      });
      if (!res.ok && res.status !== 204) throw new Error("Failed to delete agent.");
    },
    onSuccess: () => {
      toast.success("Agent deleted");
      setDeleteTarget(null);
      queryClient.invalidateQueries({ queryKey: ["custom-agents"] });
    },
    onError: () => toast.error("Failed to delete agent. Please try again."),
  });

  const openCreateDialog = () => {
    setEditAgent(null);
    setDialogOpen(true);
  };

  const openEditDialog = (agent: CustomAgent) => {
    setEditAgent(agent);
    setDialogOpen(true);
  };

  const capabilities = capabilitiesQuery.data?.capabilities ?? [];
  const customAgents = customAgentsQuery.data?.agents ?? [];

  return (
    <div className="mx-auto max-w-5xl px-4 sm:px-6 py-6 sm:py-8">
      {/* Page header */}
      <div className="mb-12">
        <h1 className="text-2xl font-semibold text-neutral-900">Capabilities</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Your assistant's tools and custom agents
        </p>
      </div>

      {/* Built-in Capabilities section */}
      <section className="mb-12">
        <div className="mb-4">
          <h2 className="text-xl font-semibold text-neutral-900">
            Built-in Capabilities
          </h2>
          <p className="text-sm text-muted-foreground mt-1">
            System tools available to your assistant
          </p>
        </div>

        {capabilitiesQuery.isLoading && (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {[1, 2, 3].map((i) => (
              <Card key={i}>
                <CardHeader className="pb-2">
                  <Skeleton className="h-4 w-32" />
                  <Skeleton className="h-3 w-full mt-2" />
                </CardHeader>
                <CardContent>
                  <Skeleton className="h-3 w-full mb-2" />
                  <Skeleton className="h-3 w-2/3 mb-2" />
                  <Skeleton className="h-3 w-1/2" />
                </CardContent>
              </Card>
            ))}
          </div>
        )}

        {capabilitiesQuery.isError && (
          <Alert variant="destructive">
            <AlertCircle className="h-4 w-4" />
            <AlertDescription className="flex items-center justify-between">
              <span>Could not load capabilities. Check your connection and try again.</span>
              <Button
                variant="outline"
                size="sm"
                onClick={() => capabilitiesQuery.refetch()}
              >
                Retry
              </Button>
            </AlertDescription>
          </Alert>
        )}

        {!capabilitiesQuery.isLoading && !capabilitiesQuery.isError && (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {capabilities.map((cap) => (
              <CapabilityCard key={cap.name} cap={cap} />
            ))}
          </div>
        )}
      </section>

      {/* My Custom Agents section */}
      <section>
        <div className="mb-4 flex items-center justify-between">
          <div>
            <h2 className="text-xl font-semibold text-neutral-900">
              My Custom Agents
            </h2>
          </div>
          <Button onClick={openCreateDialog}>
            <Plus className="mr-2 h-4 w-4" />
            Create Agent
          </Button>
        </div>

        {customAgentsQuery.isLoading && (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {[1, 2, 3].map((i) => (
              <Card key={i}>
                <CardHeader className="pb-2">
                  <Skeleton className="h-4 w-24" />
                  <Skeleton className="h-3 w-16 mt-2" />
                </CardHeader>
                <CardContent>
                  <Skeleton className="h-3 w-full mb-2" />
                  <Skeleton className="h-3 w-1/2" />
                </CardContent>
              </Card>
            ))}
          </div>
        )}

        {customAgentsQuery.isError && (
          <Alert variant="destructive">
            <AlertCircle className="h-4 w-4" />
            <AlertDescription className="flex items-center justify-between">
              <span>Could not load custom agents. Check your connection and try again.</span>
              <Button
                variant="outline"
                size="sm"
                onClick={() => customAgentsQuery.refetch()}
              >
                Retry
              </Button>
            </AlertDescription>
          </Alert>
        )}

        {!customAgentsQuery.isLoading &&
          !customAgentsQuery.isError &&
          customAgents.length === 0 && (
            <div className="flex flex-col items-center gap-3 py-16 text-center">
              <h3 className="text-lg font-medium text-neutral-900">
                No custom agents yet
              </h3>
              <p className="max-w-md text-sm text-muted-foreground">
                Create your first custom agent to extend your assistant's
                capabilities. Agents can call webhooks, run prompts, or execute
                scripts.
              </p>
              <Button onClick={openCreateDialog}>
                <Plus className="mr-2 h-4 w-4" />
                Create Agent
              </Button>
            </div>
          )}

        {!customAgentsQuery.isLoading &&
          !customAgentsQuery.isError &&
          customAgents.length > 0 && (
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              {customAgents.map((agent) => (
                <CustomAgentCard
                  key={agent.id}
                  agent={agent}
                  onEdit={() => openEditDialog(agent)}
                  onDelete={() => setDeleteTarget(agent)}
                  onToggle={() =>
                    toggleMutation.mutate({
                      id: agent.id,
                      enabled: !agent.enabled,
                    })
                  }
                />
              ))}
            </div>
          )}
      </section>

      {/* Create/Edit Dialog */}
      <CustomAgentDialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        editAgent={editAgent}
        onSuccess={() =>
          queryClient.invalidateQueries({ queryKey: ["custom-agents"] })
        }
      />

      {/* Delete Confirmation */}
      <AlertDialog
        open={deleteTarget !== null}
        onOpenChange={(open) => {
          if (!open) setDeleteTarget(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete Agent</AlertDialogTitle>
            <AlertDialogDescription>
              Are you sure you want to delete &ldquo;{deleteTarget?.name}&rdquo;?
              This action cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => deleteTarget && deleteMutation.mutate(deleteTarget.id)}
              className="bg-red-600 hover:bg-red-700"
            >
              Delete Agent
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
