import { createRoot } from "react-dom/client";
import { Providers, queryClient } from "../src/App";
import { OperationDialog } from "../src/components/OperationDialog";
import { PluginsSection } from "../src/layout/PluginsSection";
import "../src/index.css";

const root = document.getElementById("root");
if (!root) throw new Error("Missing fixture root");
createRoot(root).render(
  <Providers client={queryClient()}>
    <PluginsSection restartInterval={1} reload={() => document.body.setAttribute("data-fixture-reloaded", "yes")} />
    <OperationDialog />
  </Providers>,
);
