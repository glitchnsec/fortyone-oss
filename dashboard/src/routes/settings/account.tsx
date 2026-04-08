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
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel,
  AlertDialogContent, AlertDialogDescription, AlertDialogFooter,
  AlertDialogHeader, AlertDialogTitle, AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { fetchWithAuth } from "@/lib/api";
import { useAuth } from "@/lib/auth.tsx";
import { useState, useMemo } from "react";
import { toast } from "sonner";
import { Copy, Check, MessageSquare } from "lucide-react";
import { Card, CardContent, CardHeader } from "@/components/ui/card";

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
  const [deleting, setDeleting] = useState(false);
  const [linkCode, setLinkCode] = useState<string | null>(null);
  const [generatingCode, setGeneratingCode] = useState(false);
  const [codeCopied, setCodeCopied] = useState(false);

  const { data, isLoading } = useQuery<MeResponse>({
    queryKey: ["me"],
    queryFn: () =>
      fetchWithAuth("/api/v1/me").then((r) => r.json()) as Promise<MeResponse>,
  });

  const handleGenerateLinkCode = async () => {
    setGeneratingCode(true);
    setCodeCopied(false);
    try {
      const res = await fetchWithAuth("/auth/me/slack-link-code", { method: "POST" });
      if (!res.ok) throw new Error("Failed to generate code");
      const data = await res.json();
      setLinkCode(data.code);
    } catch {
      toast.error("Failed to generate linking code. Please try again.");
    } finally {
      setGeneratingCode(false);
    }
  };

  const handleCopyCode = () => {
    if (linkCode) {
      navigator.clipboard.writeText(linkCode);
      setCodeCopied(true);
      toast.success("Code copied to clipboard");
      setTimeout(() => setCodeCopied(false), 2000);
    }
  };

  const handleDeleteAccount = async () => {
    setDeleting(true);
    try {
      const res = await fetchWithAuth("/api/v1/me", { method: "DELETE" });
      if (!res.ok) throw new Error("Delete failed");
    } catch {
      toast.error("Failed to delete account. Please try again.");
      setDeleting(false);
      return;
    }
    logout();
    navigate({ to: "/auth/login" });
  };

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
    <div className="mx-auto max-w-2xl px-4 sm:px-6 py-6 sm:py-8">
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

          {/* Link Slack Account */}
          <div className="border-t border-neutral-200 pt-6">
            <Card>
              <CardHeader className="pb-3">
                <div className="flex items-center gap-2">
                  <MessageSquare className="h-5 w-5 text-neutral-500" />
                  <h2 className="text-sm font-semibold text-neutral-900">Link Slack Account</h2>
                </div>
                <p className="text-xs text-neutral-500">
                  Connect your Slack account to message your assistant from Slack.
                  Generate a code below, then paste it in a DM to the bot.
                </p>
              </CardHeader>
              <CardContent>
                {linkCode ? (
                  <div className="space-y-3">
                    <div className="flex items-center gap-3">
                      <code className="rounded-md bg-neutral-100 px-4 py-2 text-lg font-mono font-semibold tracking-widest text-neutral-900">
                        {linkCode}
                      </code>
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={handleCopyCode}
                      >
                        {codeCopied ? (
                          <Check className="mr-1 h-4 w-4 text-green-600" />
                        ) : (
                          <Copy className="mr-1 h-4 w-4" />
                        )}
                        {codeCopied ? "Copied" : "Copy"}
                      </Button>
                    </div>
                    <p className="text-xs text-neutral-400">
                      Paste this code in a DM to your assistant bot in Slack.
                      The code expires in 10 minutes.
                    </p>
                  </div>
                ) : (
                  <Button
                    variant="outline"
                    onClick={handleGenerateLinkCode}
                    disabled={generatingCode}
                  >
                    {generatingCode && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                    Generate Linking Code
                  </Button>
                )}
              </CardContent>
            </Card>
          </div>

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

          {/* Danger Zone — Delete Account */}
          <div className="border-t border-red-100 pt-6">
            <h2 className="mb-1 text-sm font-medium text-red-600">Danger Zone</h2>
            <p className="mb-4 text-xs text-neutral-400">
              Permanently delete your account and all associated data. This cannot be undone.
            </p>
            <AlertDialog>
              <AlertDialogTrigger asChild>
                <Button variant="destructive" disabled={deleting}>
                  {deleting && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                  Delete Account
                </Button>
              </AlertDialogTrigger>
              <AlertDialogContent>
                <AlertDialogHeader>
                  <AlertDialogTitle>Delete your account?</AlertDialogTitle>
                  <AlertDialogDescription>
                    This will permanently delete your account, all memories, conversations, tasks,
                    and connected services. This action cannot be undone.
                  </AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                  <AlertDialogCancel>Cancel</AlertDialogCancel>
                  <AlertDialogAction
                    onClick={handleDeleteAccount}
                    className="bg-red-600 hover:bg-red-700 focus:ring-red-600"
                  >
                    Yes, delete my account
                  </AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
          </div>
        </div>
      )}
    </div>
  );
}
