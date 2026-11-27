// The providers every screen needs, kept apart from the routes so tests can use them without the app shell.
import { MutationCache, QueryCache, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { type ReactNode, useEffect } from "react";
import { ApiError, failedWorkspaceRead } from "@/api/client";
import { nativeWorkspaces } from "@/layout/titlebar";
import { splash, unreachableError } from "@/splash/store";

/** A call that couldn't reach hz makes the splash ask whether it is gone (and come back over the app if so). */
const suspect = (error: unknown) => {
  if (unreachableError(error)) splash.send({ type: "suspect" });
};

export function queryClient(): QueryClient {
  return new QueryClient({
    queryCache: new QueryCache({ onError: suspect }),
    mutationCache: new MutationCache({ onError: suspect }),
    defaultOptions: {
      queries: {
        staleTime: 30_000,
        refetchOnWindowFocus: false,
        // A refused token won't get better by retrying.
        retry: (count, error) => !(error instanceof ApiError && error.status < 500) && count < 1,
      },
    },
  });
}

export function Providers({ client, children }: Readonly<{ client: QueryClient; children: ReactNode }>) {
  useEffect(() => {
    if (!nativeWorkspaces()) return;
    const restored = () => {
      void client.refetchQueries({
        type: "active",
        predicate: (query) => query.state.status === "error" && failedWorkspaceRead(query.state.error),
      });
    };
    window.addEventListener("hz:workspace-restored", restored);
    return () => window.removeEventListener("hz:workspace-restored", restored);
  }, [client]);
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
