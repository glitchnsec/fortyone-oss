/**
 * Connections overview page -- read-only grouped view by persona (D-04).
 *
 * Shows all connections organized by persona sections. No management actions
 * here; users manage connections from persona settings (/settings/personas).
 *
 * On page load: reads ?connected=<provider>&persona_id=<id> or ?error=<reason>
 * query params for OAuth callback toast.
 */
import { useState, useEffect } from "react";
import { createFileRoute, useSearch } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { toast } from "sonner";
import { Loader2, Link2 } from "lucide-react";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Separator } from "@/components/ui/separator";
import { fetchWithAuth } from "@/lib/api";

export const Route = createFileRoute("/connections/")({
  validateSearch: (search: Record<string, unknown>) => ({
    connected: typeof search.connected === "string" ? search.connected : undefined,
    error: typeof search.error === "string" ? search.error : undefined,
    persona_id: typeof search.persona_id === "string" ? search.persona_id : undefined,
  }),
  component: ConnectionsPage,
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

interface Connection {
  id: string;
  provider: string;
  status: string;
  persona_id: string | null;
  error_message?: string;
}

interface ConnectionsResponse {
  connections: Connection[];
}

interface PersonasResponse {
  personas: Persona[];
}

// ---- Page ----

function ConnectionsPage() {
  const search = useSearch({ from: "/connections/" });

  const { data: connectionsData, isLoading: loadingConns, isError } = useQuery<ConnectionsResponse>({
    queryKey: ["connections"],
    queryFn: () => fetchWithAuth("/api/v1/connections").then((r) => r.json()) as Promise<ConnectionsResponse>,
    retry: 1,
  });

  const { data: personasData, isLoading: loadingPersonas } = useQuery<PersonasResponse>({
    queryKey: ["personas"],
    queryFn: () => fetchWithAuth("/api/v1/personas").then((r) => r.json()) as Promise<PersonasResponse>,
  });

  const personas = personasData?.personas ?? [];
  const connections = connectionsData?.connections ?? [];
  const unassigned = connections.filter((c) => !c.persona_id);
  const getConnsForPersona = (pid: string) => connections.filter((c) => c.persona_id === pid);

  // Show toast on redirect back from OAuth flow (with persona context)
  useEffect(() => {
    if (search.connected) {
      const providerName = search.connected.charAt(0).toUpperCase() + search.connected.slice(1);
      if (search.persona_id && personas.length > 0) {
        const persona = personas.find((p) => p.id === search.persona_id);
        if (persona) {
          toast.success(`${providerName} connected to ${persona.name}.`);
          return;
        }
      }
      toast.success(`${providerName} connected successfully.`);
    } else if (search.error) {
      toast.error("Connection failed. Please try again.");
    }
  }, [search.connected, search.error, search.persona_id, personas]);

  const isLoading = loadingConns || loadingPersonas;

  if (isLoading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <Loader2 className="h-6 w-6 animate-spin text-neutral-400" />
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-5xl px-4 sm:px-6 py-6 sm:py-8">
      <h1 className="mb-1 text-xl font-semibold text-neutral-900">Connections Overview</h1>
      <p className="mb-6 text-sm text-neutral-500">
        Manage connections from your persona settings.
      </p>

      {isError && (
        <Alert className="mb-6 border-red-200 bg-red-50">
          <AlertDescription className="text-red-700">
            Unable to load connections. Please refresh the page.
          </AlertDescription>
        </Alert>
      )}

      {/* Empty state: zero connections total */}
      {!isError && connections.length === 0 && (
        <div className="mb-8 flex flex-col items-center gap-3 py-12 text-center">
          <Link2 className="h-10 w-10 text-neutral-300" />
          <h2 className="text-2xl font-semibold text-neutral-900">No connections yet</h2>
          <p className="max-w-xs text-sm text-neutral-500">
            Connect services from your persona settings so your assistant can take action on your behalf.
          </p>
          <a
            href="/settings/personas"
            className="mt-2 text-sm text-blue-600 hover:underline"
          >
            Go to persona settings
          </a>
        </div>
      )}

      {/* Grouped sections by persona */}
      {!isError && connections.length > 0 && (
        <div className="space-y-6">
          {personas.map((persona) => {
            const personaConns = getConnsForPersona(persona.id);
            if (personaConns.length === 0) return null;
            return (
              <div key={persona.id}>
                <div className="flex items-center justify-between mb-3">
                  <h2 className="text-sm font-semibold text-neutral-900">{persona.name}</h2>
                  <a
                    href="/settings/personas"
                    className="text-xs text-blue-600 hover:underline"
                  >
                    Manage in persona settings
                  </a>
                </div>
                <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
                  {personaConns.map((conn) => (
                    <ReadOnlyConnectionCard key={conn.id} connection={conn} />
                  ))}
                </div>
              </div>
            );
          })}

          {/* Unassigned connections section */}
          {unassigned.length > 0 && (
            <div>
              <Separator className="mb-4" />
              <div className="flex items-center justify-between mb-3">
                <h2 className="text-sm font-semibold text-neutral-900">Unassigned</h2>
                <a
                  href="/settings/personas"
                  className="text-xs text-blue-600 hover:underline"
                >
                  Manage in persona settings
                </a>
              </div>
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
                {unassigned.map((conn) => (
                  <ReadOnlyConnectionCard key={conn.id} connection={conn} />
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ---- Read-only connection card ----

function ReadOnlyConnectionCard({ connection }: { connection: Connection }) {
  const providerName = connection.provider.charAt(0).toUpperCase() + connection.provider.slice(1);

  return (
    <Card className="border border-neutral-200 bg-neutral-50">
      <CardHeader className="flex flex-row items-center justify-between pb-2">
        <div className="flex h-8 w-8 items-center justify-center rounded-md bg-white border border-neutral-200 text-xs font-semibold text-neutral-600">
          {providerName.charAt(0)}
        </div>
        {connection.status === "connected" && (
          <Badge className="bg-green-100 text-green-700 hover:bg-green-100">Connected</Badge>
        )}
        {connection.status === "needs_reauth" && (
          <Badge className="bg-yellow-100 text-yellow-700 hover:bg-yellow-100">Needs reauth</Badge>
        )}
        {connection.status === "error" && (
          <Badge className="bg-red-100 text-red-700 hover:bg-red-100">Error</Badge>
        )}
        {connection.status === "not_connected" && (
          <Badge variant="outline" className="text-neutral-500">Not connected</Badge>
        )}
      </CardHeader>
      <CardContent>
        <p className="text-sm font-medium text-neutral-900">{providerName}</p>
      </CardContent>
    </Card>
  );
}
