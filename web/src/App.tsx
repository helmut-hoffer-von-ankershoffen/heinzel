// The routes and the app root.
import { createBrowserRouter, Navigate, type RouteObject, RouterProvider, useSearchParams } from "react-router";
import { Providers, queryClient } from "@/app/providers";
import { CopyCommand } from "@/components/common";
import { ErrorBoundary, RouteError } from "@/components/ErrorScreen";
import { HeinzelPage } from "@/heinzel/HeinzelPage";
import { t } from "@/i18n";
import { Shell } from "@/layout/Shell";
import { Bookmarks } from "@/pages/Bookmarks";
import { Contract } from "@/pages/Contract";
import { Contracts } from "@/pages/Contracts";
import { Doctor } from "@/pages/Doctor";
import { GraphView } from "@/pages/graph/GraphView";
import { Heinzelmaennchen } from "@/pages/Heinzelmaennchen";
import { Home } from "@/pages/Home";
import { Assets, AssetTypes } from "@/pages/Inventory";
import { OrgChart, Team } from "@/pages/Organisation";
import { NotFound, Profile } from "@/pages/Profile";
import { InstalledPage } from "@/plugins/Views";
import { isRemoteWorkspace, RemoteWorkspaceSignIn, Splash, SplashGone } from "@/splash/Splash";

/** `/settings?q=…` from older links: Home with the settings dialog open, searching. */
function SettingsLink() {
  const [params] = useSearchParams();
  const q = params.get("q");
  return <Navigate replace to={`/?settings=${q ? `&q=${encodeURIComponent(q)}` : ""}`} />;
}

export const routes: RouteObject[] = [
  {
    element: <Shell />,
    errorElement: <RouteError />,
    children: [
      { index: true, element: <Home /> },
      { path: "settings", element: <SettingsLink /> },
      { path: "notifications", element: <Navigate replace to="/?panel=notifications" /> },
      { path: "heinzel", element: <Heinzelmaennchen /> },
      { path: "heinzel/:id", element: <HeinzelPage /> },
      { path: "contracts", element: <Contracts /> },
      { path: "contracts/:name", element: <Contract /> },
      { path: "graph", element: <GraphView /> },
      { path: "org", element: <OrgChart /> },
      { path: "team", element: <Team /> },
      { path: "inventory/types", element: <AssetTypes /> },
      { path: "inventory/assets", element: <Assets /> },
      { path: "doctor", element: <Doctor /> },
      { path: "bookmarks", element: <Bookmarks /> },
      { path: "profile", element: <Profile /> },
      { path: "plugins/*", element: <InstalledPage /> },
      { path: "*", element: <NotFound /> },
    ],
  },
];

/** What the page shows without a token: how to get the link that has one. */
export function NoToken() {
  if (isRemoteWorkspace()) {
    return (
      <main className="mx-auto flex w-full max-w-md flex-col gap-3 p-4 text-sm">
        <RemoteWorkspaceSignIn />
      </main>
    );
  }
  return (
    <main className="mx-auto flex max-w-xl flex-col items-start gap-3 p-10">
      <h1 className="text-2xl font-semibold">{t("token.missingTitle")}</h1>
      <p className="text-sm text-muted-foreground">{t("token.missing")}</p>
      <CopyCommand command="hz ui serve" />
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
      <ErrorBoundary>
        <RouterProvider router={createBrowserRouter(routes)} />
      </ErrorBoundary>
    </Providers>
  );
}
