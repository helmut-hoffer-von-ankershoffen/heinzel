import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router";
import { AgentSchedules } from "../src/chat/AgentSchedules";
import "../src/index.css";

const root = document.getElementById("root");
if (!root) throw new Error("Missing fixture root");
createRoot(root).render(
  <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <BrowserRouter>
      <div className="min-h-screen bg-background p-3 text-foreground">
        <h1>Agent schedule</h1>
        <AgentSchedules sessionId="hz-01ARZ3NDEKTSV4RRFFQ69G5FAV" />
      </div>
    </BrowserRouter>
  </QueryClientProvider>,
);
