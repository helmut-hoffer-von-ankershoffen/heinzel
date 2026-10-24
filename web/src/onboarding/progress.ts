// The onboarding wizard's steps and how far this browser got: the step it was on, the steps finished or skipped,
// and whether the person closed it. Answers themselves are written through hz (`hz system config set`), not here.
import type { SchemaProfile } from "@/api/client";

export const STEPS = ["you", "team", "look", "ai", "connectors", "notifications", "first"] as const;
export type StepId = (typeof STEPS)[number];

export type Progress = { step: StepId; visited: StepId[]; closed: boolean };

const KEY = "hz.onboarding";
const START: Progress = { step: "you", visited: [], closed: false };

export function loadProgress(): Progress {
  try {
    const saved = JSON.parse(localStorage.getItem(KEY) ?? "null") as Partial<Progress> | null;
    if (!saved) return START;
    const step = STEPS.includes(saved.step as StepId) ? (saved.step as StepId) : "you";
    const visited = (saved.visited ?? []).filter((id): id is StepId => STEPS.includes(id as StepId));
    return { step, visited, closed: saved.closed === true };
  } catch {
    return START;
  }
}

export function saveProgress(progress: Progress): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(progress));
  } catch {
    // Not remembered: the wizard starts at the first step next time.
  }
}

/** Setup is incomplete while hz doesn't know your role or time zone, the answers `hz setup` asks first. */
export const setupIncomplete = (profile: SchemaProfile | undefined) =>
  profile !== undefined && (!profile.role || !profile.timezone);

/** The roles `org.role` accepts, with the short names people use. */
export const ROLES = [
  ["engineering-manager", "EM"],
  ["engineer", "IC"],
  ["principal", "Principal"],
  ["product-manager", "PM"],
  ["program-manager", "TPM"],
  ["engineering-leadership", "Eng Lead"],
  ["product-leadership", "Product Lead"],
] as const;

/** "Send the weekly status to my team" → `send-the-weekly-status`: a contract name `hz workflow new` takes. */
export function contractName(sentence: string): string {
  return sentence
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/[^a-z0-9]+/g, " ")
    .trim()
    .split(" ")
    .filter(Boolean)
    .slice(0, 4)
    .join("-");
}
