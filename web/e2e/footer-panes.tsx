// Mount the real footer panes (Core's and a plugin's, drawn by Core) and production styles in a phone-sized footer.
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { Providers, queryClient } from "../src/App";
import { StatusItems } from "../src/components/Contributions";
import { DurableHeinzelStatus } from "../src/layout/DurableHeinzelStatus";
import { HealthStatus } from "../src/layout/HealthStatus";
import "../src/index.css";

sessionStorage.setItem("hz.token", "fixture-session");
const root = document.getElementById("root");
if (!root) throw new Error("Missing fixture root");
createRoot(root).render(
  <Providers client={queryClient()}>
    <MemoryRouter>
      <div className="flex min-h-screen flex-col bg-background text-foreground">
        <main className="flex-1 p-3">
          <h1>Footer panes</h1>
        </main>
        <footer className="flex h-12 items-center gap-2 border-t-[0.5px] border-foreground/20 px-4 text-ui text-muted-foreground">
          <DurableHeinzelStatus />
          <HealthStatus />
          <StatusItems slot="footer" />
        </footer>
      </div>
    </MemoryRouter>
  </Providers>,
);
