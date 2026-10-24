import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "@/App";
import { adoptToken } from "@/api/token";
import { applyTheme } from "@/layout/theme";
import "./index.css";

applyTheme();
const root = document.getElementById("root");
if (root) {
  createRoot(root).render(
    <StrictMode>
      <App hasToken={adoptToken() !== null} />
    </StrictMode>,
  );
}
