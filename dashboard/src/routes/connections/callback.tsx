/**
 * MCP OAuth callback route: /connections/callback
 *
 * The browser lands here after the MCP authorization server redirects back
 * with `code` and `state`. This page exchanges them through the authenticated
 * dashboard API proxy, then forwards the user to /connections with toast params.
 */
import { useEffect } from "react";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { toast } from "sonner";
import { Loader2 } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { fetchWithAuth } from "@/lib/api";

export const Route = createFileRoute("/connections/callback")({
  validateSearch: (search: Record<string, unknown>) => ({
    code: typeof search.code === "string" ? search.code : undefined,
    state: typeof search.state === "string" ? search.state : undefined,
    error: typeof search.error === "string" ? search.error : undefined,
  }),
  component: ConnectionCallbackPage,
});

function ConnectionCallbackPage() {
  const navigate = useNavigate();
  const search = Route.useSearch();

  useEffect(() => {
    let cancelled = false;

    const finish = async () => {
      if (search.error) {
        toast.error("Connection failed. Please try again.");
        await navigate({ to: "/connections", search: { error: search.error } });
        return;
      }
      if (!search.code || !search.state) {
        toast.error("Missing OAuth callback parameters.");
        await navigate({ to: "/connections", search: { error: "missing_callback_params" } });
        return;
      }

      try {
        const res = await fetchWithAuth("/api/v1/mcp/oauth/callback", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ code: search.code, state: search.state }),
        });
        const payload = await res.json().catch(() => ({})) as {
          persona_id?: string;
          detail?: string;
          error?: string;
        };
        if (!res.ok) {
          throw new Error(
            (typeof payload.detail === "string" && payload.detail) ||
            payload.error ||
            "token_exchange_failed",
          );
        }
        if (cancelled) return;
        await navigate({
          to: "/connections",
          search: {
            connected: "mcp",
            persona_id: payload.persona_id,
          },
        });
      } catch (err) {
        if (cancelled) return;
        const message = err instanceof Error ? err.message : "token_exchange_failed";
        toast.error("Connection failed. Please try again.");
        await navigate({ to: "/connections", search: { error: message } });
      }
    };

    void finish();
    return () => {
      cancelled = true;
    };
  }, [navigate, search.code, search.error, search.state]);

  return (
    <div className="flex min-h-screen items-center justify-center bg-white px-4">
      <Card className="w-full max-w-[400px]">
        <CardContent className="flex flex-col items-center gap-4 py-10">
          <Loader2 className="h-8 w-8 animate-spin text-blue-600" />
          <p className="text-sm text-neutral-600">Connecting your account...</p>
        </CardContent>
      </Card>
    </div>
  );
}
