import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { useForm, Controller } from "react-hook-form";
import PhoneInput, { isValidPhoneNumber } from "react-phone-number-input";
import "react-phone-number-input/style.css";
import { Card, CardContent, CardHeader } from "../../components/ui/card";
import { Button } from "../../components/ui/button";
import { Input } from "../../components/ui/input";
import { Label } from "../../components/ui/label";

export const Route = createFileRoute("/auth/forgot-password")({
  component: ForgotPasswordPage,
});

interface PhoneForm {
  phone: string;
}

interface ResetForm {
  code: string;
  new_password: string;
  confirmPassword: string;
}

function ForgotPasswordPage() {
  const [step, setStep] = useState<"phone" | "reset">("phone");
  const [phone, setPhone] = useState("");
  const [serverError, setServerError] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);
  const navigate = useNavigate();

  // Step 1: Phone input form
  const phoneForm = useForm<PhoneForm>();
  // Step 2: Code + new password form
  const resetForm = useForm<ResetForm>();

  const onSendCode = async (data: PhoneForm) => {
    setServerError(null);
    const res = await fetch("/auth/forgot-password/send-code", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ phone: data.phone }),
    });
    if (!res.ok) {
      setServerError("Failed to send reset code. Please try again.");
      return;
    }
    setPhone(data.phone);
    setStep("reset");
  };

  const onReset = async (data: ResetForm) => {
    setServerError(null);
    const res = await fetch("/auth/forgot-password/reset", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        phone,
        code: data.code,
        new_password: data.new_password,
      }),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => null);
      setServerError(
        body?.detail || "Invalid or expired code. Please try again."
      );
      return;
    }
    setSuccessMessage("Password reset! Redirecting to login...");
    setTimeout(() => navigate({ to: "/auth/login" }), 2000);
  };

  const newPassword = resetForm.watch("new_password");

  return (
    <div className="min-h-screen flex items-center justify-center bg-white px-4">
      <Card className="w-full max-w-[400px] border border-neutral-200 shadow-sm">
        <CardHeader>
          <h1 className="text-[28px] font-semibold leading-[1.2]">
            Reset your password
          </h1>
        </CardHeader>
        <CardContent>
          {successMessage ? (
            <p className="text-sm text-green-600 text-center py-4">
              {successMessage}
            </p>
          ) : step === "phone" ? (
            <form
              onSubmit={phoneForm.handleSubmit(onSendCode)}
              className="flex flex-col gap-4"
            >
              <div className="flex flex-col gap-2">
                <Label htmlFor="phone">Phone number</Label>
                <Controller
                  name="phone"
                  control={phoneForm.control}
                  rules={{
                    required: "This field is required.",
                    validate: (v) =>
                      (v && isValidPhoneNumber(v)) ||
                      "Enter a valid phone number.",
                  }}
                  render={({ field }) => (
                    <PhoneInput
                      {...field}
                      id="phone"
                      defaultCountry="US"
                      international
                      countryCallingCodeEditable={false}
                      className="flex h-10 w-full rounded-md border border-neutral-200 bg-white px-3 py-2 text-sm ring-offset-white focus-within:ring-2 focus-within:ring-neutral-950 focus-within:ring-offset-2"
                      aria-describedby={
                        phoneForm.formState.errors.phone
                          ? "phone-error"
                          : undefined
                      }
                    />
                  )}
                />
                {phoneForm.formState.errors.phone && (
                  <span id="phone-error" className="text-sm text-red-600">
                    {phoneForm.formState.errors.phone.message}
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
                disabled={phoneForm.formState.isSubmitting}
                className="w-full bg-blue-600 hover:bg-blue-700"
              >
                {phoneForm.formState.isSubmitting ? (
                  <span className="animate-spin mr-2">&#x27F3;</span>
                ) : null}
                Send code
              </Button>
              <p className="text-sm text-neutral-500 text-center">
                <Link to="/auth/login" className="text-blue-600">
                  Back to sign in
                </Link>
              </p>
            </form>
          ) : (
            <form
              onSubmit={resetForm.handleSubmit(onReset)}
              className="flex flex-col gap-4"
            >
              <p className="text-sm text-neutral-600">
                Enter the 6-digit code sent to {phone}
              </p>
              <div className="flex flex-col gap-2">
                <Label htmlFor="code">Verification code</Label>
                <Input
                  id="code"
                  type="text"
                  maxLength={6}
                  inputMode="numeric"
                  {...resetForm.register("code", {
                    required: "This field is required.",
                    pattern: {
                      value: /^\d{6}$/,
                      message: "Enter a 6-digit code.",
                    },
                  })}
                  aria-describedby={
                    resetForm.formState.errors.code ? "code-error" : undefined
                  }
                />
                {resetForm.formState.errors.code && (
                  <span id="code-error" className="text-sm text-red-600">
                    {resetForm.formState.errors.code.message}
                  </span>
                )}
              </div>
              <div className="flex flex-col gap-2">
                <Label htmlFor="new_password">New password</Label>
                <Input
                  id="new_password"
                  type="password"
                  {...resetForm.register("new_password", {
                    required: "This field is required.",
                    minLength: {
                      value: 8,
                      message: "Password must be at least 8 characters.",
                    },
                  })}
                  aria-describedby={
                    resetForm.formState.errors.new_password
                      ? "new-pw-error"
                      : undefined
                  }
                />
                {resetForm.formState.errors.new_password && (
                  <span id="new-pw-error" className="text-sm text-red-600">
                    {resetForm.formState.errors.new_password.message}
                  </span>
                )}
              </div>
              <div className="flex flex-col gap-2">
                <Label htmlFor="confirmPassword">Confirm new password</Label>
                <Input
                  id="confirmPassword"
                  type="password"
                  {...resetForm.register("confirmPassword", {
                    required: "This field is required.",
                    validate: (value) =>
                      value === newPassword || "Passwords do not match.",
                  })}
                  aria-describedby={
                    resetForm.formState.errors.confirmPassword
                      ? "confirm-pw-error"
                      : undefined
                  }
                />
                {resetForm.formState.errors.confirmPassword && (
                  <span
                    id="confirm-pw-error"
                    className="text-sm text-red-600"
                  >
                    {resetForm.formState.errors.confirmPassword.message}
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
                disabled={resetForm.formState.isSubmitting}
                className="w-full bg-blue-600 hover:bg-blue-700"
              >
                {resetForm.formState.isSubmitting ? (
                  <span className="animate-spin mr-2">&#x27F3;</span>
                ) : null}
                Reset password
              </Button>
              <p className="text-sm text-neutral-500 text-center">
                <Link to="/auth/login" className="text-blue-600">
                  Back to sign in
                </Link>
              </p>
            </form>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
