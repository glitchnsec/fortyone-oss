/**
 * OAuth callback route: /connections/callback
 *
 * Rendered when the browser lands back from an OAuth provider mid-flow.
 * The main OAuth callback is handled server-side (/oauth/callback/{provider});
 * this page is a client-side holding page shown while that redirect resolves.
 *
 * Shows centered card with spinner + "Connecting your account..."
 */
import { createFileRoute } from "@tanstack/react-router";
import { Loader2 } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";

export const Route = createFileRoute("/connections/callback")({
  component: ConnectionCallbackPage,
});

function ConnectionCallbackPage() {
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
