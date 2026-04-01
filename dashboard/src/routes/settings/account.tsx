/**
 * Account Settings page — view email and phone, logout option.
 *
 * Shows current email and phone (read-only — edits are Phase 3+).
 * "Log out" Button (destructive) calls POST /auth/logout then clears auth state.
 * No confirmation dialog for logout — immediate action per UI-SPEC.
 * Redirects to /auth/login after logout.
 */
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { fetchWithAuth } from "@/lib/api";
import { useAuth } from "@/lib/auth.tsx";
import { useState } from "react";
import { toast } from "sonner";

export const Route = createFileRoute("/settings/account")({
  component: AccountSettingsPage,
});

interface MeResponse {
  user_id: string;
  email: string | null;
  phone: string;
  phone_verified: boolean;
  assistant_name: string | null;
}

function AccountSettingsPage() {
  const { logout } = useAuth();
  const navigate = useNavigate();
  const [loggingOut, setLoggingOut] = useState(false);

  const { data, isLoading } = useQuery<MeResponse>({
    queryKey: ["me"],
    queryFn: () =>
      fetchWithAuth("/api/v1/me").then((r) => r.json()) as Promise<MeResponse>,
  });

  const handleLogout = async () => {
    setLoggingOut(true);
    try {
      await fetchWithAuth("/auth/logout", { method: "POST" });
    } catch {
      // Ignore network errors — clear session regardless
    }
    logout();
    navigate({ to: "/auth/login" });
  };

  return (
    <div className="mx-auto max-w-2xl px-6 py-8">
      <h1 className="mb-6 text-xl font-semibold text-neutral-900">Account Settings</h1>

      {isLoading ? (
        <div className="flex h-32 items-center justify-center">
          <Loader2 className="h-5 w-5 animate-spin text-neutral-400" />
        </div>
      ) : (
        <div className="space-y-6">
          {/* Email (read-only) */}
          <div className="space-y-1">
            <Label className="text-sm text-neutral-500">Email</Label>
            <p className="text-sm text-neutral-900">{data?.email ?? "—"}</p>
          </div>

          {/* Phone (read-only) */}
          <div className="space-y-1">
            <Label className="text-sm text-neutral-500">Phone</Label>
            <p className="text-sm text-neutral-900">
              {data?.phone ?? "—"}
              {data?.phone_verified && (
                <span className="ml-2 rounded-full bg-green-100 px-2 py-0.5 text-xs text-green-700">
                  Verified
                </span>
              )}
            </p>
          </div>

          <p className="text-xs text-neutral-400">
            Email and phone editing will be available in a future update.
          </p>

          {/* Logout */}
          <div className="border-t border-neutral-200 pt-6">
            <Button
              variant="destructive"
              onClick={handleLogout}
              disabled={loggingOut}
            >
              {loggingOut && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              Log out
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
