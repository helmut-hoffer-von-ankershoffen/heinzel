import { createRoot } from "react-dom/client";
import { Providers, queryClient } from "../src/App";
import { DaemonSettings } from "../src/layout/DaemonSettings";
import "../src/index.css";

const root = document.getElementById("root");
if (!root) throw new Error("Missing fixture root");
createRoot(root).render(
  <Providers client={queryClient()}>
    <DaemonSettings />
  </Providers>,
);
