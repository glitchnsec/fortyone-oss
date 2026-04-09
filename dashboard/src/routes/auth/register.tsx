import { createFileRoute, Link, useNavigate, useRouter } from "@tanstack/react-router";
import { useState } from "react";
import { useForm, Controller } from "react-hook-form";
import PhoneInput, { isValidPhoneNumber } from "react-phone-number-input";
import "react-phone-number-input/style.css";
import { Card, CardContent, CardHeader } from "../../components/ui/card";
import { Button } from "../../components/ui/button";
import { Input } from "../../components/ui/input";
import { Label } from "../../components/ui/label";
import { useAuth } from "../../lib/auth";

export const Route = createFileRoute("/auth/register")({ component: RegisterPage });

interface RegisterForm {
  email: string;
  name: string;
  phone: string;
  password: string;
  confirmPassword: string;
  timezone: string;
}

function RegisterPage() {
  const { login } = useAuth();

  // Read URL params for Slack onboarding flow (D-03)
  const searchParams = new URLSearchParams(window.location.search);
  const prefillEmail = searchParams.get("email") || "";
  const prefillPhone = searchParams.get("phone") || "";
  const fromSlack = searchParams.get("from") === "slack";
  const slackId = searchParams.get("slack_id") || "";

  // Auto-detect timezone from browser + build full IANA list
  const detectedTimezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const allTimezones = (() => {
    try {
      return Intl.supportedValuesOf("timeZone");
    } catch {
      // Fallback for older browsers
      return [
        "Pacific/Honolulu", "America/Anchorage", "America/Los_Angeles",
        "America/Denver", "America/Chicago", "America/New_York",
        "America/Toronto", "America/Halifax", "America/St_Johns",
        "America/Sao_Paulo", "Europe/London", "Europe/Paris",
        "Europe/Helsinki", "Asia/Dubai", "Asia/Kolkata", "Asia/Shanghai",
        "Asia/Tokyo", "Australia/Sydney", "Pacific/Auckland",
      ];
    }
  })();

  const {
    register,
    handleSubmit,
    watch,
    control,
    formState: { errors, isSubmitting },
  } = useForm<RegisterForm>({
    defaultValues: {
      email: prefillEmail,
      phone: prefillPhone,
      name: "",
      timezone: detectedTimezone || "America/New_York",
    },
  });
  const [serverError, setServerError] = useState<string | null>(null);
  const navigate = useNavigate();
  const router = useRouter();

  const password = watch("password");

  const onSubmit = async (data: RegisterForm) => {
    setServerError(null);
    const payload: Record<string, string> = {
      email: data.email,
      phone: data.phone,
      password: data.password,
      name: data.name,
      timezone: data.timezone,
    };
    // Pass slack_user_id for auto-linking (D-04)
    if (fromSlack && slackId) {
      payload.slack_user_id = slackId;
    }
    const res = await fetch("/auth/register", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      credentials: "include",
    });
    if (res.status === 409) {
      setServerError("An account with this email already exists. Sign in instead?");
      return;
    }
    if (!res.ok) {
      setServerError("Registration failed. Please try again.");
      return;
    }
    const data2 = await res.json();
    login(data2.access_token, data2.user_id);
    await router.invalidate();
    await navigate({ to: "/onboarding" });
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-white px-4">
      <Card className="w-full max-w-[400px] border border-neutral-200 shadow-sm">
        <CardHeader>
          <h1 className="text-[28px] font-semibold leading-[1.2]">Create your account</h1>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit(onSubmit)} className="flex flex-col gap-4">
            <div className="flex flex-col gap-2">
              <Label htmlFor="name">What should we call you?</Label>
              <Input
                id="name"
                type="text"
                placeholder="e.g. KC, Sarah, Dr. Smith"
                {...register("name", { required: "We need a name to address you by." })}
                aria-describedby={errors.name ? "name-error" : undefined}
              />
              {errors.name && (
                <span id="name-error" className="text-sm text-red-600">
                  {errors.name.message}
                </span>
              )}
            </div>
            <div className="flex flex-col gap-2">
              <Label htmlFor="email">Email</Label>
              <Input
                id="email"
                type="email"
                {...register("email", { required: "This field is required." })}
                aria-describedby={errors.email ? "email-error" : undefined}
              />
              {errors.email && (
                <span id="email-error" className="text-sm text-red-600">
                  {errors.email.message}
                </span>
              )}
            </div>
            <div className="flex flex-col gap-2">
              <Label htmlFor="phone">Phone number</Label>
              <Controller
                name="phone"
                control={control}
                rules={{
                  required: "This field is required.",
                  validate: (v) => (v && isValidPhoneNumber(v)) || "Enter a valid phone number.",
                }}
                render={({ field }) => (
                  <PhoneInput
                    {...field}
                    id="phone"
                    defaultCountry="US"
                    international
                    countryCallingCodeEditable={false}
                    className="flex h-10 w-full rounded-md border border-neutral-200 bg-white px-3 py-2 text-sm ring-offset-white focus-within:ring-2 focus-within:ring-neutral-950 focus-within:ring-offset-2"
                    aria-describedby={errors.phone ? "phone-error" : undefined}
                  />
                )}
              />
              {errors.phone && (
                <span id="phone-error" className="text-sm text-red-600">
                  {errors.phone.message}
                </span>
              )}
            </div>
            <div className="flex flex-col gap-2">
              <Label htmlFor="timezone">Your timezone</Label>
              <select
                id="timezone"
                {...register("timezone", { required: "Timezone is required." })}
                className="flex h-10 w-full rounded-md border border-neutral-200 bg-white px-3 py-2 text-sm ring-offset-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-neutral-950 focus-visible:ring-offset-2"
              >
                {allTimezones.map((tz) => (
                  <option key={tz} value={tz}>
                    {tz.replace(/_/g, " ")}
                  </option>
                ))}
              </select>
              <span className="text-xs text-neutral-400">Auto-detected from your browser</span>
            </div>
            <div className="flex flex-col gap-2">
              <Label htmlFor="password">Password</Label>
              <Input
                id="password"
                type="password"
                {...register("password", {
                  required: "This field is required.",
                  minLength: {
                    value: 8,
                    message: "Password must be at least 8 characters.",
                  },
                })}
                aria-describedby={errors.password ? "pw-error" : undefined}
              />
              {errors.password && (
                <span id="pw-error" className="text-sm text-red-600">
                  {errors.password.message}
                </span>
              )}
            </div>
            <div className="flex flex-col gap-2">
              <Label htmlFor="confirmPassword">Confirm password</Label>
              <Input
                id="confirmPassword"
                type="password"
                {...register("confirmPassword", {
                  required: "This field is required.",
                  validate: (value) =>
                    value === password || "Passwords do not match.",
                })}
                aria-describedby={errors.confirmPassword ? "confirm-pw-error" : undefined}
              />
              {errors.confirmPassword && (
                <span id="confirm-pw-error" className="text-sm text-red-600">
                  {errors.confirmPassword.message}
                </span>
              )}
            </div>
            {serverError && (
              <p className="text-sm text-red-600" role="alert">
                {serverError}
              </p>
            )}
            <Button
              type="submit"
              disabled={isSubmitting}
              className="w-full bg-blue-600 hover:bg-blue-700"
            >
              {isSubmitting ? <span className="animate-spin mr-2">⟳</span> : null}Create your
              account
            </Button>
            <p className="text-sm text-neutral-500 text-center">
              Already have an account?{" "}
              <Link to="/auth/login" className="text-blue-600">
                Sign in
              </Link>
            </p>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
