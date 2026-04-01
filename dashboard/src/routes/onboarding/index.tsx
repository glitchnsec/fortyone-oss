/**
 * Onboarding wizard — 4-step flow: Account created → Verify phone → Name assistant → Connect Google
 *
 * Full-screen layout (no sidebar), centered Card max-w-[520px], Progress bar at top.
 *
 * Steps:
 *   1. Account created confirmation (registration already done via /auth/register)
 *   2. Phone OTP verification — fetches phone from /api/v1/me, sends OTP via POST /auth/send-otp,
 *      and verifies 6-digit code via POST /auth/verify-otp (D-03, AUTH-02).
 *      "Skip" option available so onboarding is not blocked in dev.
 *   3. Name your assistant — calls PATCH /api/v1/me/assistant
 *   4. Connect Google (optional) — initiates OAuth via POST /api/v1/connections/initiate
 *      Final CTA: "Start using Operator" navigates to /connections
 */
import { useState, useEffect } from "react";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { toast } from "sonner";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Progress } from "@/components/ui/progress";
import { Loader2, CheckCircle2 } from "lucide-react";
import { fetchWithAuth } from "@/lib/api";

export const Route = createFileRoute("/onboarding/")({
  component: OnboardingPage,
});

const TOTAL_STEPS = 4;

function OnboardingPage() {
  const navigate = useNavigate();
  const [step, setStep] = useState(1);

  const progressValue = ((step - 1) / (TOTAL_STEPS - 1)) * 100;

  const goNext = () => setStep((s) => Math.min(s + 1, TOTAL_STEPS));

  return (
    <div className="flex min-h-screen items-center justify-center bg-white px-4 py-8">
      <div className="w-full max-w-[520px]">
        {/* Step progress */}
        <div className="mb-6">
          <div className="mb-2 flex justify-between text-xs text-neutral-500">
            <span>Step {step} of {TOTAL_STEPS}</span>
            <span>{Math.round(progressValue)}%</span>
          </div>
          <Progress value={progressValue} className="h-2" />
        </div>

        {/* Step cards */}
        {step === 1 && <Step1AccountCreated onNext={goNext} />}
        {step === 2 && <Step2VerifyPhone onNext={goNext} />}
        {step === 3 && <Step3NameAssistant onNext={goNext} />}
        {step === 4 && (
          <Step4ConnectGoogle onDone={() => navigate({ to: "/connections" })} />
        )}
      </div>
    </div>
  );
}

// ─── Step 1: Account created ──────────────────────────────────────────────────

function Step1AccountCreated({ onNext }: { onNext: () => void }) {
  return (
    <Card>
      <CardHeader>
        <div className="mb-2 flex justify-center">
          <CheckCircle2 className="h-12 w-12 text-green-500" />
        </div>
        <CardTitle className="text-center text-xl">Account created</CardTitle>
        <CardDescription className="text-center">
          Your Operator account is ready. Let's set up your assistant.
        </CardDescription>
      </CardHeader>
      <CardFooter>
        <Button className="w-full bg-blue-600 hover:bg-blue-700" onClick={onNext}>
          Continue
        </Button>
      </CardFooter>
    </Card>
  );
}

// ─── Step 2: Verify phone OTP ─────────────────────────────────────────────────

function Step2VerifyPhone({ onNext }: { onNext: () => void }) {
  const [phone, setPhone] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [loading, setLoading] = useState(false);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // On mount: fetch the user's phone number then send OTP
  useEffect(() => {
    let cancelled = false;

    const sendCode = async () => {
      setSending(true);
      try {
        // Fetch the authenticated user's phone number
        const meRes = await fetchWithAuth("/api/v1/me");
        if (!meRes.ok) return;
        const meData = await meRes.json() as { phone?: string };
        const userPhone = meData.phone ?? null;
        if (cancelled) return;
        setPhone(userPhone);

        if (userPhone) {
          // Send OTP to the user's registered phone
          await fetchWithAuth("/auth/send-otp", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ phone: userPhone }),
          });
          toast.success("Code sent to your phone.");
        }
      } catch {
        // Non-fatal — user can still attempt to verify or skip
      } finally {
        if (!cancelled) setSending(false);
      }
    };

    void sendCode();
    return () => { cancelled = true; };
  }, []);

  const handleVerify = async () => {
    setError(null);
    if (!/^\d{6}$/.test(code)) {
      setError("Please enter a 6-digit verification code.");
      return;
    }
    setLoading(true);
    try {
      const res = await fetchWithAuth("/auth/verify-otp", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ phone: phone ?? "", code }),
      });
      if (res.ok) {
        onNext();
        return;
      }
      if (res.status === 400) {
        const data = await res.json().catch(() => ({})) as { detail?: string };
        setError(data.detail ?? "Invalid or expired verification code. Try again.");
        return;
      }
      setError("Verification failed. Please try again.");
    } catch {
      setError("Verification failed. Please try again.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-xl">Verify your phone</CardTitle>
        <CardDescription>
          {sending
            ? "Sending a 6-digit code to your phone…"
            : "We sent a 6-digit code to your phone number. Enter it below to verify your account."}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="space-y-2">
          <Label htmlFor="otp-code">Verification code</Label>
          <Input
            id="otp-code"
            type="text"
            inputMode="numeric"
            pattern="\d{6}"
            maxLength={6}
            placeholder="123456"
            value={code}
            onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))}
            aria-describedby={error ? "otp-error" : undefined}
          />
          {error && (
            <p id="otp-error" className="text-sm text-red-600" role="alert">
              {error}
            </p>
          )}
        </div>
      </CardContent>
      <CardFooter className="flex flex-col gap-2">
        <Button
          className="w-full bg-blue-600 hover:bg-blue-700"
          onClick={handleVerify}
          disabled={loading || sending || code.length !== 6}
        >
          {loading && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
          Verify phone
        </Button>
        <Button
          variant="ghost"
          className="w-full text-neutral-500"
          onClick={onNext}
          disabled={loading}
        >
          Skip for now
        </Button>
      </CardFooter>
    </Card>
  );
}

// ─── Step 3: Name your assistant ──────────────────────────────────────────────

function Step3NameAssistant({ onNext }: { onNext: () => void }) {
  const [name, setName] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSave = async () => {
    setError(null);
    if (!name.trim()) {
      setError("This field is required.");
      return;
    }
    setLoading(true);
    try {
      const res = await fetchWithAuth("/api/v1/me/assistant", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ assistant_name: name.trim() }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({})) as { detail?: string };
        setError(data.detail ?? "Failed to save. Please try again.");
        return;
      }
      onNext();
    } catch {
      setError("Network error. Please try again.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-xl">Name your assistant</CardTitle>
        <CardDescription>
          Give your assistant a name. You can change this any time in settings.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="space-y-2">
          <Label htmlFor="assistant-name">Assistant name</Label>
          <Input
            id="assistant-name"
            type="text"
            placeholder="e.g. Alex"
            value={name}
            onChange={(e) => setName(e.target.value)}
            onBlur={() => {
              if (!name.trim()) setError("This field is required.");
              else setError(null);
            }}
            aria-describedby={error ? "name-error" : undefined}
          />
          {error && (
            <p id="name-error" className="text-sm text-red-600" role="alert">
              {error}
            </p>
          )}
        </div>
      </CardContent>
      <CardFooter>
        <Button
          className="w-full bg-blue-600 hover:bg-blue-700"
          onClick={handleSave}
          disabled={loading}
        >
          {loading && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
          Continue
        </Button>
      </CardFooter>
    </Card>
  );
}

// ─── Step 4: Connect Google (optional) ───────────────────────────────────────

function Step4ConnectGoogle({ onDone }: { onDone: () => void }) {
  const [loading, setLoading] = useState(false);

  const handleConnect = async () => {
    setLoading(true);
    try {
      const res = await fetchWithAuth("/api/v1/connections/initiate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ provider: "google" }),
      });
      if (res.ok) {
        const data = await res.json() as { auth_url?: string };
        if (data.auth_url) {
          window.location.href = data.auth_url;
          return;
        }
      }
      toast.error("Connection failed. Please try again.");
    } catch {
      toast.error("Network error. Please try again.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-xl">Connect Google</CardTitle>
        <CardDescription>
          Connect Gmail and Google Calendar so your assistant can read your email and manage your schedule.
          You can skip this and connect later from the Connections page.
        </CardDescription>
      </CardHeader>
      <CardFooter className="flex flex-col gap-2">
        <Button
          className="w-full bg-blue-600 hover:bg-blue-700"
          onClick={handleConnect}
          disabled={loading}
        >
          {loading && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
          Connect Google
        </Button>
        <Button
          variant="ghost"
          className="w-full text-neutral-500"
          onClick={onDone}
          disabled={loading}
        >
          Start using Operator
        </Button>
      </CardFooter>
    </Card>
  );
}
