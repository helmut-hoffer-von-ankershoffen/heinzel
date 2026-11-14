import { useEffect, useState } from "react";
import { registerReloadHold, registerUpdatePreparation } from "./work";

const KEY = "hz.update.composer";
type Draft = { context: string; session: string | null; message: string };

function restored(context: string, session: string | null): Draft | null {
  try {
    const raw: unknown = JSON.parse(sessionStorage.getItem(KEY) ?? "null");
    if (!raw || typeof raw !== "object") return null;
    const draft = raw as Partial<Draft>;
    const current = session ?? sessionStorage.getItem("hz.chat");
    if (draft.context !== context || draft.session !== current || typeof draft.message !== "string") return null;
    if (draft.message.length > 4000) return null;
    // A draft saved by an older release may carry its model pick: only the text comes back.
    return { context: draft.context, session: draft.session ?? null, message: draft.message };
  } catch {
    return null;
  }
}

/** Only an explicit update persists unsent text; normal typing remains in React and is never uploaded. */
export function useUpdateDraft(context: string, session: string | null, busy = false) {
  const [initial] = useState(() => restored(context, session));
  const [message, setMessage] = useState(initial?.message ?? "");
  useEffect(() => {
    if (initial) sessionStorage.removeItem(KEY);
  }, [initial]);
  useEffect(
    () =>
      registerUpdatePreparation(() => {
        if (busy) throw new Error("Wait for the current chat turn before updating; its work will stay here.");
        const draft: Draft = { context, session, message };
        const encoded = JSON.stringify(draft);
        sessionStorage.setItem(KEY, encoded);
        if (sessionStorage.getItem(KEY) !== encoded) throw new Error("The chat draft could not be preserved.");
      }),
    [context, session, message, busy],
  );
  // Unsent text or a running turn holds off the reload an automatic update needs.
  useEffect(() => registerReloadHold(() => busy || message.trim() !== ""), [busy, message]);
  return { message, setMessage };
}
