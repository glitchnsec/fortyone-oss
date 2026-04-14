import { createFileRoute, Link, useNavigate, useRouter } from "@tanstack/react-router";
import { useState, useEffect, useRef } from "react";
import { useForm } from "react-hook-form";
import { Card, CardContent, CardHeader } from "../../components/ui/card";
import { Button } from "../../components/ui/button";
import { Input } from "../../components/ui/input";
import { Label } from "../../components/ui/label";
import { useAuth } from "../../lib/auth.tsx";

export const Route = createFileRoute("/auth/login")({ component: LoginPage });

interface LoginForm {
  email: string;
  password: string;
}

function LoginPage() {
  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<LoginForm>();
  const [serverError, setServerError] = useState<string | null>(null);
  const { login, isAuthenticated } = useAuth();
  const navigate = useNavigate();
  const router = useRouter();
  const pendingRedirect = useRef(false);

  // Reactive navigation: waits for React to flush auth state before navigating.
  // This avoids the race where router.invalidate() + navigate() fire before
  // isAuthenticated updates, causing beforeLoad to redirect back to /auth/login.
  useEffect(() => {
    if (pendingRedirect.current && isAuthenticated) {
      pendingRedirect.current = false;
      router.invalidate().then(() => navigate({ to: "/connections" }));
    }
  }, [isAuthenticated, navigate, router]);

  const onSubmit = async (data: LoginForm) => {
    setServerError(null);
    const res = await fetch("/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
      credentials: "include",
    });
    if (!res.ok) {
      setServerError("Email or password is incorrect. Try again or reset your password.");
      return;
    }
    const { access_token } = (await res.json()) as { access_token: string };
    const me = await fetch("/api/v1/me", {
      headers: { Authorization: `Bearer ${access_token}` },
      credentials: "include",
    });
    const { user_id } = (await me.json()) as { user_id: string };
    pendingRedirect.current = true;
    login(access_token, user_id);
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-white px-4">
      <Card className="w-full max-w-[400px] border border-neutral-200 shadow-sm">
        <CardHeader>
          <h1 className="text-[28px] font-semibold leading-[1.2]">Sign in to Operator</h1>
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
              <Label htmlFor="password">Password</Label>
              <Input
                id="password"
                type="password"
                {...register("password", { required: "This field is required." })}
                aria-describedby={errors.password ? "pw-error" : undefined}
              />
              {errors.password && (
                <span id="pw-error" className="text-sm text-red-600">
                  {errors.password.message}
                </span>
              )}
            </div>
            <div className="text-right">
              <Link to="/auth/forgot-password" className="text-sm text-blue-600">
                Forgot password?
              </Link>
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
              {isSubmitting ? <span className="animate-spin mr-2">⟳</span> : null}Sign in
            </Button>
            <p className="text-sm text-neutral-500 text-center">
              No account?{" "}
              <Link to="/auth/register" className="text-blue-600">
                Create one
              </Link>
            </p>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
