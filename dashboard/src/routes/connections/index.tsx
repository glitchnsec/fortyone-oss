/**
 * Connections page — card grid showing all provider connections.
 *
 * Layout: 3-col desktop / 2-col tablet / 1-col mobile grid.
 * Handles all 5 UI-SPEC connection states per provider:
 *   not_connected | connecting | connected | needs_reauth | error
 *
 * On page load: reads ?connected=<provider> or ?error=<provider> query params → Toaster.
 * Disconnect: AlertDialog confirmation before DELETE /api/v1/connections/{id}.
 */
import { useState, useEffect } from "react";
import { createFileRoute, useSearch } from "@tanstack/react-router";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Loader2, Link2 } from "lucide-react";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription } from "@/components/ui/alert";
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

export const Route = createFileRoute("/connections/")({
  validateSearch: (search: Record<string, unknown>) => ({
    connected: typeof search.connected === "string" ? search.connected : undefined,
    error: typeof search.error === "string" ? search.error : undefined,
  }),
  component: ConnectionsPage,
});

// ─── Types ────────────────────────────────────────────────────────────────────

type ConnectionStatus = "not_connected" | "connecting" | "connected" | "needs_reauth" | "error";

interface Connection {
  id: string;
  provider: string;
  status: ConnectionStatus;
  error_message?: string;
}

interface ConnectionsResponse {
  connections: Connection[];
}

// ─── Provider metadata ─────────────────────────────────────────────────────────

const PROVIDERS = [
  { key: "google", name: "Google" },
];

// ─── Page ────────────────────────────────────────────────────────────────────

function ConnectionsPage() {
  const search = useSearch({ from: "/connections/" });
  const queryClient = useQueryClient();

  // Show toast on redirect back from OAuth flow
  useEffect(() => {
    if (search.connected) {
      const name = search.connected.charAt(0).toUpperCase() + search.connected.slice(1);
      toast.success(`${name} connected successfully.`);
    } else if (search.error) {
      toast.error(`Connection failed. Please try again.`);
    }
  }, [search.connected, search.error]);

  const { data, isLoading, isError } = useQuery<ConnectionsResponse>({
    queryKey: ["connections"],
    queryFn: () =>
      fetchWithAuth("/api/v1/connections").then((r) => r.json()) as Promise<ConnectionsResponse>,
    retry: 1,
  });

  const connections = data?.connections ?? [];

  // Build a map of provider → connection record
  const connectionMap = new Map<string, Connection>(
    connections.map((c) => [c.provider, c])
  );

  const hasAnyConnection = connections.length > 0;

  if (isLoading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <Loader2 className="h-6 w-6 animate-spin text-neutral-400" />
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-5xl px-4 sm:px-6 py-6 sm:py-8">
      <h1 className="mb-6 text-xl font-semibold text-neutral-900">Your Connections</h1>

      {isError && (
        <Alert className="mb-6 border-red-200 bg-red-50">
          <AlertDescription className="text-red-700">
            Unable to load connections. Please refresh the page.
          </AlertDescription>
        </Alert>
      )}

      {!isError && !hasAnyConnection && connections.length === 0 && !isLoading && (
        <EmptyState />
      )}

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {PROVIDERS.map((provider) => {
          const conn = connectionMap.get(provider.key);
          const status: ConnectionStatus = conn?.status ?? "not_connected";
          return (
            <ConnectionCard
              key={provider.key}
              provider={provider}
              connection={conn}
              status={status}
              onDisconnected={() => {
                queryClient.invalidateQueries({ queryKey: ["connections"] });
              }}
            />
          );
        })}
      </div>
    </div>
  );
}

// ─── Empty state ──────────────────────────────────────────────────────────────

function EmptyState() {
  return (
    <div className="mb-8 flex flex-col items-center gap-3 py-12 text-center">
      <Link2 className="h-10 w-10 text-neutral-300" />
      <h2 className="text-2xl font-semibold text-neutral-900">No connections yet</h2>
      <p className="max-w-xs text-sm text-neutral-500">
        Connect Gmail or Google Calendar so your assistant can take action on your behalf.
      </p>
    </div>
  );
}

// ─── Connection card ──────────────────────────────────────────────────────────

function ConnectionCard({
  provider,
  connection,
  status,
  onDisconnected,
}: {
  provider: { key: string; name: string };
  connection?: Connection;
  status: ConnectionStatus;
  onDisconnected: () => void;
}) {
  const [connecting, setConnecting] = useState(false);

  const initiateMutation = useMutation({
    mutationFn: async () => {
      const res = await fetchWithAuth("/api/v1/connections/initiate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ provider: provider.key }),
      });
      if (!res.ok) throw new Error("Failed to initiate connection");
      return res.json() as Promise<{ auth_url?: string }>;
    },
    onMutate: () => setConnecting(true),
    onSuccess: (data) => {
      if (data.auth_url) {
        window.location.href = data.auth_url;
      }
    },
    onError: () => {
      setConnecting(false);
      toast.error(`Connection failed. Please try again.`);
    },
  });

  const disconnectMutation = useMutation({
    mutationFn: async () => {
      if (!connection?.id) throw new Error("No connection id");
      const res = await fetchWithAuth(`/api/v1/connections/${connection.id}`, {
        method: "DELETE",
      });
      if (!res.ok && res.status !== 204) throw new Error("Delete failed");
    },
    onSuccess: () => {
      toast.success(`${provider.name} disconnected.`);
      onDisconnected();
    },
    onError: () => {
      toast.error("Failed to disconnect. Please try again.");
    },
  });

  const isConnecting = connecting || initiateMutation.isPending;
  const currentStatus: ConnectionStatus = isConnecting ? "connecting" : status;

  return (
    <Card className="relative border border-neutral-200 bg-neutral-50">
      <CardHeader className="flex flex-row items-center justify-between pb-2">
        {/* Provider logo placeholder */}
        <div className="flex h-8 w-8 items-center justify-center rounded-md bg-white border border-neutral-200 text-xs font-semibold text-neutral-600">
          {provider.name.charAt(0)}
        </div>

        {/* Status badge */}
        {currentStatus === "connected" && (
          <Badge className="bg-green-100 text-green-700 hover:bg-green-100">Connected</Badge>
        )}
        {currentStatus === "needs_reauth" && (
          <Badge className="bg-yellow-100 text-yellow-700 hover:bg-yellow-100">Needs reauthorization</Badge>
        )}
        {currentStatus === "error" && (
          <Badge className="bg-red-100 text-red-700 hover:bg-red-100">Connection error</Badge>
        )}
        {(currentStatus === "not_connected" || currentStatus === "connecting") && (
          <Badge variant="outline" className="text-neutral-500">Not connected</Badge>
        )}
      </CardHeader>

      <CardContent className="space-y-3">
        <p className="text-sm font-medium text-neutral-900">{provider.name}</p>

        {/* needs_reauth: yellow alert */}
        {currentStatus === "needs_reauth" && (
          <Alert className="border-yellow-200 bg-yellow-50">
            <AlertDescription className="text-sm text-yellow-800">
              Your {provider.name} connection needs reauthorization. Reconnect to restore full access.
            </AlertDescription>
          </Alert>
        )}

        {/* error: red alert */}
        {currentStatus === "error" && (
          <Alert className="border-red-200 bg-red-50">
            <AlertDescription className="text-sm text-red-700">
              {connection?.error_message ?? "Connection error. Please reconnect."}
            </AlertDescription>
          </Alert>
        )}

        {/* Action buttons per state */}
        {(currentStatus === "not_connected") && (
          <Button
            className="w-full bg-blue-600 hover:bg-blue-700 min-h-[44px]"
            onClick={() => initiateMutation.mutate()}
            disabled={isConnecting}
          >
            Connect {provider.name}
          </Button>
        )}

        {currentStatus === "connecting" && (
          <Button className="w-full bg-blue-600 hover:bg-blue-700 min-h-[44px]" disabled>
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            Connecting...
          </Button>
        )}

        {currentStatus === "connected" && (
          <DisconnectButton
            providerName={provider.name}
            onConfirm={() => disconnectMutation.mutate()}
            loading={disconnectMutation.isPending}
          />
        )}

        {(currentStatus === "needs_reauth" || currentStatus === "error") && (
          <Button
            className="w-full bg-blue-600 hover:bg-blue-700 min-h-[44px]"
            onClick={() => initiateMutation.mutate()}
            disabled={isConnecting}
          >
            {isConnecting && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
            Reconnect
          </Button>
        )}
      </CardContent>
    </Card>
  );
}

// ─── Disconnect confirm dialog ────────────────────────────────────────────────

function DisconnectButton({
  providerName,
  onConfirm,
  loading,
}: {
  providerName: string;
  onConfirm: () => void;
  loading: boolean;
}) {
  return (
    <AlertDialog>
      <AlertDialogTrigger asChild>
        <button
          className="w-full text-sm text-neutral-500 underline-offset-2 hover:underline disabled:opacity-50"
          disabled={loading}
        >
          {loading ? <Loader2 className="inline h-4 w-4 animate-spin" /> : "Disconnect"}
        </button>
      </AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Disconnect {providerName}?</AlertDialogTitle>
          <AlertDialogDescription>
            Your assistant will lose access to {providerName}. You can reconnect at any time.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction
            onClick={onConfirm}
            className="bg-red-600 hover:bg-red-700 focus:ring-red-600"
          >
            Disconnect
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
