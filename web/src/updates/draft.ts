import { useEffect, useRef, useState } from "react";
import type { ModelChoice } from "@/api/chat";
import { registerUpdatePreparation } from "./work";

const KEY = "hz.update.composer";
type Draft = { context: string; session: string | null; message: string; picked: ModelChoice | null };

function restored(context: string, session: string | null): Draft | null {
  try {
    const raw: unknown = JSON.parse(sessionStorage.getItem(KEY) ?? "null");
    if (!raw || typeof raw !== "object") return null;
    const draft = raw as Partial<Draft>;
    const current = session ?? sessionStorage.getItem("hz.chat");
    if (draft.context !== context || draft.session !== current || typeof draft.message !== "string") return null;
    if (draft.message.length > 4000) return null;
    if (
      draft.picked !== null &&
      (!draft.picked ||
        typeof draft.picked !== "object" ||
        (draft.picked.model != null && typeof draft.picked.model !== "string") ||
        (draft.picked.effort != null && typeof draft.picked.effort !== "string"))
    )
      return null;
    return draft as Draft;
  } catch {
    return null;
  }
}

/** Only an explicit update persists unsent text; normal typing remains in React and is never uploaded. */
export function useUpdateDraft(context: string, session: string | null, busy = false) {
  const [initial] = useState(() => restored(context, session));
  const [message, setMessage] = useState(initial?.message ?? "");
  const [picked, setPicked] = useState<ModelChoice | null>(initial?.picked ?? null);
  const previous = useRef(session);
  useEffect(() => {
    if (initial) sessionStorage.removeItem(KEY);
  }, [initial]);
  useEffect(() => {
    if (previous.current !== session) {
      previous.current = session;
      setPicked(null);
    }
  }, [session]);
  useEffect(
    () =>
      registerUpdatePreparation(() => {
        if (busy) throw new Error("Wait for the current chat turn before updating; its work will stay here.");
        const draft: Draft = { context, session, message, picked };
        const encoded = JSON.stringify(draft);
        sessionStorage.setItem(KEY, encoded);
        if (sessionStorage.getItem(KEY) !== encoded) throw new Error("The chat draft could not be preserved.");
      }),
    [context, session, message, picked, busy],
  );
  return { message, setMessage, picked, setPicked };
}
