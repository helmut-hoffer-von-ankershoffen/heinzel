// The routes, and the providers every screen needs.
import { MutationCache, QueryCache, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { createBrowserRouter, Navigate, type RouteObject, RouterProvider, useSearchParams } from "react-router";
import { ApiError } from "@/api/client";
import { CopyCommand } from "@/components/common";
import { t } from "@/i18n";
import { Shell } from "@/layout/Shell";
import { Contract } from "@/pages/Contract";
import { Contracts } from "@/pages/Contracts";
import { Doctor } from "@/pages/Doctor";
import { GraphView } from "@/pages/graph/GraphView";
import { Home } from "@/pages/Home";
import { Assets, AssetTypes } from "@/pages/Inventory";
import { OrgChart, Team } from "@/pages/Organisation";
import { NotFound, Profile } from "@/pages/Profile";
import { Splash, SplashGone } from "@/splash/Splash";
import { splash, unreachableError } from "@/splash/store";

/** `/settings?q=…` from older links: Home with the settings dialog open, searching. */
function SettingsLink() {
  const [params] = useSearchParams();
  const q = params.get("q");
  return <Navigate replace to={`/?settings=${q ? `&q=${encodeURIComponent(q)}` : ""}`} />;
}

export const routes: RouteObject[] = [
  {
    element: <Shell />,
    children: [
      { index: true, element: <Home /> },
      { path: "settings", element: <SettingsLink /> },
      { path: "notifications", element: <Navigate replace to="/?panel=notifications" /> },
      { path: "contracts", element: <Contracts /> },
      { path: "contracts/:name", element: <Contract /> },
      { path: "graph", element: <GraphView /> },
      { path: "org", element: <OrgChart /> },
      { path: "team", element: <Team /> },
      { path: "inventory/types", element: <AssetTypes /> },
      { path: "inventory/assets", element: <Assets /> },
      { path: "doctor", element: <Doctor /> },
      { path: "profile", element: <Profile /> },
      { path: "*", element: <NotFound /> },
    ],
  },
];

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

export function Providers({ client, children }: { client: QueryClient; children: ReactNode }) {
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

/** What the page shows without a token: how to get the link that has one. */
export function NoToken() {
  return (
    <main className="mx-auto flex max-w-xl flex-col items-start gap-3 p-10">
      <h1 className="text-2xl font-semibold">{t("token.missingTitle")}</h1>
      <p className="text-sm text-muted-foreground">{t("token.missing")}</p>
      <CopyCommand command="hz ui" />
    </main>
  );
}

export function App({ hasToken }: { hasToken: boolean }) {
  if (!hasToken) {
    return (
      <>
        <SplashGone />
        <NoToken />
      </>
    );
  }
  return (
    <Providers client={queryClient()}>
      <Splash />
      <RouterProvider router={createBrowserRouter(routes)} />
    </Providers>
  );
}
