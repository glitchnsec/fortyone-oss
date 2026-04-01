import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { Card, CardContent, CardHeader } from "../../components/ui/card";
import { Button } from "../../components/ui/button";
import { Input } from "../../components/ui/input";
import { Label } from "../../components/ui/label";

export const Route = createFileRoute("/auth/register")({ component: RegisterPage });

interface RegisterForm {
  email: string;
  phone: string;
  password: string;
  confirmPassword: string;
}

function RegisterPage() {
  const {
    register,
    handleSubmit,
    watch,
    formState: { errors, isSubmitting },
  } = useForm<RegisterForm>();
  const [serverError, setServerError] = useState<string | null>(null);
  const navigate = useNavigate();

  const password = watch("password");

  const onSubmit = async (data: RegisterForm) => {
    setServerError(null);
    const res = await fetch("/auth/register", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        email: data.email,
        phone: data.phone,
        password: data.password,
      }),
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
    await navigate({ to: "/onboarding" });
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-white">
      <Card className="w-full max-w-[400px] border border-neutral-200 shadow-sm">
        <CardHeader>
          <h1 className="text-[28px] font-semibold leading-[1.2]">Create your account</h1>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit(onSubmit)} className="flex flex-col gap-4">
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
              <Input
                id="phone"
                type="tel"
                placeholder="+1 555 000 0000"
                {...register("phone", {
                  required: "This field is required.",
                  pattern: {
                    value: /^\+?[1-9]\d{1,14}$/,
                    message: "Enter a valid phone number (e.g. +15550001234).",
                  },
                })}
                aria-describedby={errors.phone ? "phone-error" : undefined}
              />
              {errors.phone && (
                <span id="phone-error" className="text-sm text-red-600">
                  {errors.phone.message}
                </span>
              )}
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
