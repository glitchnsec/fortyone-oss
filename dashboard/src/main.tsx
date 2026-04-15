import "@/lib/theme";
import React, { useState } from "react";
import ReactDOM from "react-dom/client";
import { RouterProvider, createRouter } from "@tanstack/react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { routeTree } from "./routeTree.gen";
import { AuthProvider, useAuth } from "./lib/auth.tsx";
import "./index.css";

const queryClient = new QueryClient();

function InnerApp() {
  const auth = useAuth();
  // Create router once — re-creating on every render causes navigate() calls
  // to target a stale router instance that gets discarded on re-render.
  const [router] = useState(() =>
    createRouter({ routeTree, context: { queryClient, auth } }),
  );
  return <RouterProvider router={router} context={{ queryClient, auth }} />;
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <InnerApp />
      </AuthProvider>
    </QueryClientProvider>
  </React.StrictMode>,
);
