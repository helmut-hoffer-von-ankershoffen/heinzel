// Mount the real private skill panel and production styles in a phone-sized chat surface.
import { createRoot } from "react-dom/client";
import { SkillDraftPanel } from "../src/chat/SkillDraftPanel";
import "../src/index.css";

const root = document.getElementById("root");
if (!root) throw new Error("Missing fixture root");
createRoot(root).render(
  <div className="flex min-h-screen min-w-0 flex-col bg-background p-3 text-foreground">
    <h1>Personal conversation</h1>
    <SkillDraftPanel sessionId="hz-01ARZ3NDEKTSV4RRFFQ69G5FAV" />
  </div>,
);
