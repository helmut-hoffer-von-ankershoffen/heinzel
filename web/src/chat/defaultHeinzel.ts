// Which Heinzel the chat opens when no link names one (ADR 0157): the one you talked to last in this browser,
// else your first Heinzel that is not retired. A retired or removed Heinzel is never the default.
import type { SchemaSessionSummary } from "@/api/client";

const LAST = "hz.chat.heinzel";

/** The Heinzel last opened in this browser, or null; storage that throws counts as empty. */
export function rememberedHeinzel(): string | null {
  try {
    return localStorage.getItem(LAST);
  } catch {
    return null;
  }
}

/** Remember the Heinzel just opened, so the next chat opens it again. */
export function rememberHeinzel(sessionId: string) {
  try {
    localStorage.setItem(LAST, sessionId);
  } catch {
    // Without storage the default is the first Heinzel; nothing else depends on it.
  }
}

/** The Heinzel to open: the remembered one if it is still listed and active, else the oldest active one. */
export function defaultHeinzel(sessions: readonly SchemaSessionSummary[], remembered: string | null): string | null {
  const active = sessions.filter((session) => session.state !== "retired");
  if (remembered && active.some((session) => session.session_id === remembered)) return remembered;
  // The list is newest first, so the first Heinzel you made is the last one.
  return active.at(-1)?.session_id ?? null;
}
