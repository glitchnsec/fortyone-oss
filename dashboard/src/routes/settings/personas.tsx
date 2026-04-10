/**
 * Personas settings page -- manage identity contexts (work, personal, custom).
 *
 * CRUD operations via /api/v1/personas.
 * Create form for new personas. Inline editing. Delete with confirmation.
 * Primary connection management surface (D-03): each persona card shows its
 * connections with status badges, disconnect action, and "Add Connection" button
 * that initiates OAuth with persona_id.
 */
import { useState } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Loader2, Plus, Trash2, Pencil, X, Check, Server } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
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
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
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

interface Connection {
  id: string;
  provider: string;
  status: string;
  persona_id: string | null;
  capabilities?: Record<string, boolean>;
  display_name?: string;
  mcp_server_url?: string;
  mcp_tools?: Array<{ name: string; description?: string }>;
}

interface ConnectionsResponse {
  connections: Connection[];
}

// ---- Page ----

function PersonasSettingsPage() {
  const queryClient = useQueryClient();
  const [showForm, setShowForm] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [connectingPersonaId, setConnectingPersonaId] = useState<string | null>(null);
  const [mcpDialogPersonaId, setMcpDialogPersonaId] = useState<string | null>(null);

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

  const { data: connectionsData } = useQuery<ConnectionsResponse>({
    queryKey: ["connections"],
    queryFn: () =>
      fetchWithAuth("/api/v1/connections").then((r) => r.json()) as Promise<ConnectionsResponse>,
  });

  const initiateConnectionMutation = useMutation({
    mutationFn: async ({ provider, personaId }: { provider: string; personaId: string }) => {
      const res = await fetchWithAuth("/api/v1/connections/initiate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ provider, persona_id: personaId }),
      });
      if (!res.ok) throw new Error("Failed to initiate connection");
      return res.json() as Promise<{ auth_url?: string }>;
    },
    onMutate: (vars) => setConnectingPersonaId(vars.personaId),
    onSuccess: (data) => {
      if (data.auth_url) window.location.href = data.auth_url;
    },
    onError: () => {
      setConnectingPersonaId(null);
      toast.error("Connection failed. Please try again.");
    },
  });

  const disconnectMutation = useMutation({
    mutationFn: async (connId: string) => {
      const res = await fetchWithAuth(`/api/v1/connections/${connId}`, { method: "DELETE" });
      if (!res.ok && res.status !== 204) throw new Error("Delete failed");
    },
    onSuccess: () => {
      toast.success("Connection disconnected.");
      queryClient.invalidateQueries({ queryKey: ["connections"] });
    },
    onError: () => toast.error("Failed to disconnect. Please try again."),
  });

  const mcpConnectMutation = useMutation({
    mutationFn: async ({
      personaId,
      serverUrl,
      authType,
      apiKey,
      name: mcpName,
    }: {
      personaId: string;
      serverUrl: string;
      authType: string;
      apiKey?: string;
      name?: string;
    }) => {
      const url = authType === "oauth" ? "/api/v1/mcp/oauth/initiate" : "/api/v1/mcp/connect";
      const payload = authType === "oauth"
        ? {
            persona_id: personaId,
            server_url: serverUrl,
            name: mcpName || undefined,
          }
        : {
            persona_id: personaId,
            server_url: serverUrl,
            auth_type: authType,
            api_key: apiKey || undefined,
            name: mcpName || undefined,
          };
      const res = await fetchWithAuth(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({})) as { detail?: string; error?: string };
        const detail = typeof err.detail === "string" ? err.detail : err.error;
        throw new Error(detail ?? "Failed to connect MCP server");
      }
      return res.json() as Promise<{ id?: string; tools?: string[]; warnings?: string[]; auth_url?: string }>;
    },
    onSuccess: (data) => {
      if (data.auth_url) {
        window.location.href = data.auth_url;
        return;
      }
      const toolCount = data.tools?.length ?? 0;
      toast.success(`MCP server connected with ${toolCount} tool${toolCount !== 1 ? "s" : ""}.`);
      if (data.warnings?.length) {
        toast.warning(`Warnings: ${data.warnings.join(", ")}`);
      }
      setMcpDialogPersonaId(null);
      queryClient.invalidateQueries({ queryKey: ["connections"] });
    },
    onError: (err: Error) => toast.error(err.message),
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
  const allConnections = connectionsData?.connections ?? [];

  const getPersonaConnections = (personaId: string) =>
    allConnections.filter((c) => c.persona_id === personaId);

  return (
    <div className="mx-auto max-w-2xl px-4 sm:px-6 py-6 sm:py-8">
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
              {/* Connection management per persona (D-03) */}
              {editingId !== persona.id && (
                <div className="mt-3 border-t border-neutral-100 pt-3">
                  <p className="text-xs font-medium text-neutral-500 mb-2">
                    Connections ({getPersonaConnections(persona.id).length})
                  </p>
                  {getPersonaConnections(persona.id).length === 0 ? (
                    <p className="text-xs text-neutral-400 mb-2">
                      No connections. Add a connection so your assistant can take action for this persona.
                    </p>
                  ) : (
                    <div className="space-y-2 mb-2">
                      {getPersonaConnections(persona.id).map((conn) => (
                        <div key={conn.id} className="flex items-center justify-between py-1.5 px-2 rounded bg-neutral-50">
                          <div className="flex items-center gap-2">
                            <div className="flex h-6 w-6 items-center justify-center rounded bg-white border border-neutral-200 text-xs font-semibold text-neutral-600">
                              {(conn.display_name ?? conn.provider).charAt(0).toUpperCase()}
                            </div>
                            <span className="text-sm text-neutral-700 capitalize">{conn.display_name ?? conn.provider}</span>
                            {conn.provider === "mcp" && conn.mcp_tools && (
                              <span className="text-xs text-neutral-400">{conn.mcp_tools.length} tool{conn.mcp_tools.length !== 1 ? "s" : ""}</span>
                            )}
                            {/* Status badge */}
                            {conn.status === "connected" && (
                              <Badge className="bg-green-100 text-green-700 hover:bg-green-100 text-xs">Connected</Badge>
                            )}
                            {conn.status === "needs_reauth" && (
                              <Badge className="bg-yellow-100 text-yellow-700 hover:bg-yellow-100 text-xs">Needs reauth</Badge>
                            )}
                            {conn.status === "error" && (
                              <Badge className="bg-red-100 text-red-700 hover:bg-red-100 text-xs">Error</Badge>
                            )}
                          </div>
                          {/* Disconnect action */}
                          {conn.status === "connected" && (
                            <AlertDialog>
                              <AlertDialogTrigger asChild>
                                <button className="text-xs text-neutral-400 hover:text-red-600 underline-offset-2 hover:underline">
                                  Disconnect {conn.display_name ?? conn.provider}
                                </button>
                              </AlertDialogTrigger>
                              <AlertDialogContent>
                                <AlertDialogHeader>
                                  <AlertDialogTitle>Disconnect {conn.display_name ?? conn.provider}?</AlertDialogTitle>
                                  <AlertDialogDescription>
                                    Your assistant will lose access to {conn.display_name ?? conn.provider} for your {persona.name} persona. You can reconnect at any time.
                                  </AlertDialogDescription>
                                </AlertDialogHeader>
                                <AlertDialogFooter>
                                  <AlertDialogCancel>Cancel</AlertDialogCancel>
                                  <AlertDialogAction
                                    onClick={() => disconnectMutation.mutate(conn.id)}
                                    className="bg-red-600 hover:bg-red-700"
                                  >
                                    Disconnect {conn.display_name ?? conn.provider}
                                  </AlertDialogAction>
                                </AlertDialogFooter>
                              </AlertDialogContent>
                            </AlertDialog>
                          )}
                          {(conn.status === "needs_reauth" || conn.status === "error") && (
                            <Button
                              size="sm"
                              variant="outline"
                              className="h-7 text-xs"
                              onClick={() => initiateConnectionMutation.mutate({ provider: conn.provider, personaId: persona.id })}
                            >
                              Reconnect
                            </Button>
                          )}
                        </div>
                      ))}
                    </div>
                  )}
                  {/* Add / Reconnect buttons -- per D-01, one connection per provider per persona */}
                  <div className="flex gap-2">
                    {(() => {
                      const existingGoogle = getPersonaConnections(persona.id).find(
                        (c) => c.provider === "google" && c.status === "connected"
                      );
                      return existingGoogle ? (
                        <Button
                          size="sm"
                          variant="outline"
                          className="min-h-[44px] flex-1"
                          onClick={() => initiateConnectionMutation.mutate({ provider: "google", personaId: persona.id })}
                          disabled={connectingPersonaId === persona.id}
                        >
                          {connectingPersonaId === persona.id ? (
                            <><Loader2 className="mr-2 h-4 w-4 animate-spin" />Reconnecting...</>
                          ) : (
                            <>Reconnect Google</>
                          )}
                        </Button>
                      ) : (
                        <Button
                          size="sm"
                          className="bg-blue-600 hover:bg-blue-700 min-h-[44px] flex-1"
                          onClick={() => initiateConnectionMutation.mutate({ provider: "google", personaId: persona.id })}
                          disabled={connectingPersonaId === persona.id}
                        >
                          {connectingPersonaId === persona.id ? (
                            <><Loader2 className="mr-2 h-4 w-4 animate-spin" />Connecting...</>
                          ) : (
                            <><Plus className="mr-2 h-4 w-4" />Add Google</>
                          )}
                        </Button>
                      );
                    })()}
                    <Button
                      size="sm"
                      variant="outline"
                      className="min-h-[44px] flex-1"
                      onClick={() => setMcpDialogPersonaId(persona.id)}
                    >
                      <Server className="mr-2 h-4 w-4" />
                      Add MCP Server
                    </Button>
                  </div>
                </div>
              )}
            </CardContent>
          </Card>
        ))}
      </div>

      {/* MCP Connect Dialog */}
      <MCPConnectDialog
        personaId={mcpDialogPersonaId}
        onClose={() => setMcpDialogPersonaId(null)}
        onConnect={(data) => mcpConnectMutation.mutate(data)}
        isPending={mcpConnectMutation.isPending}
      />
    </div>
  );
}

// ---- MCP Connect Dialog ----

function MCPConnectDialog({
  personaId,
  onClose,
  onConnect,
  isPending,
}: {
  personaId: string | null;
  onClose: () => void;
  onConnect: (data: {
    personaId: string;
    serverUrl: string;
    authType: string;
    apiKey?: string;
    name?: string;
  }) => void;
  isPending: boolean;
}) {
  const [serverUrl, setServerUrl] = useState("");
  const [mcpName, setMcpName] = useState("");
  const [authType, setAuthType] = useState("none");
  const [apiKey, setApiKey] = useState("");

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!personaId || !serverUrl.trim()) return;
    onConnect({
      personaId,
      serverUrl: serverUrl.trim(),
      authType,
      apiKey: authType === "api_key" ? apiKey : undefined,
      name: mcpName.trim() || undefined,
    });
  };

  const handleOpenChange = (open: boolean) => {
    if (!open) {
      setServerUrl("");
      setMcpName("");
      setAuthType("none");
      setApiKey("");
      onClose();
    }
  };

  return (
    <Dialog open={personaId !== null} onOpenChange={handleOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Add MCP Server</DialogTitle>
          <DialogDescription>
            Connect a remote MCP server. Tools will be discovered automatically.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="mcp-server-url">Server URL</Label>
            <Input
              id="mcp-server-url"
              placeholder="https://mcp.example.com/mcp"
              value={serverUrl}
              onChange={(e) => setServerUrl(e.target.value)}
              required
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="mcp-name">Name (optional)</Label>
            <Input
              id="mcp-name"
              placeholder="My MCP Server"
              value={mcpName}
              onChange={(e) => setMcpName(e.target.value)}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="mcp-auth-type">Authentication</Label>
            <Select value={authType} onValueChange={setAuthType}>
              <SelectTrigger id="mcp-auth-type">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="none">No authentication</SelectItem>
                <SelectItem value="api_key">API Key</SelectItem>
                <SelectItem value="oauth">OAuth 2.1</SelectItem>
              </SelectContent>
            </Select>
          </div>
          {authType === "api_key" && (
            <div className="space-y-2">
              <Label htmlFor="mcp-api-key">API Key</Label>
              <Input
                id="mcp-api-key"
                type="password"
                placeholder="Enter your API key"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
              />
            </div>
          )}
          {authType === "oauth" && (
            <p className="text-xs text-neutral-500">
              OAuth will redirect you to the server for authorization after connecting.
            </p>
          )}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => handleOpenChange(false)}>
              Cancel
            </Button>
            <Button
              type="submit"
              className="bg-blue-600 hover:bg-blue-700"
              disabled={isPending || !serverUrl.trim()}
            >
              {isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              Connect
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
