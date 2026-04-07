import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useEffect } from "react";
import { useAuth } from "@/lib/auth.tsx";

export const Route = createFileRoute("/")({
  component: IndexRedirect,
});

function IndexRedirect() {
  const { isAuthenticated, initialized } = useAuth();
  const navigate = useNavigate();

  useEffect(() => {
    if (!initialized) return;
    if (isAuthenticated) {
      navigate({ to: "/connections", replace: true });
    } else {
      navigate({ to: "/auth/login", replace: true });
    }
  }, [initialized, isAuthenticated, navigate]);

  return null;
}
