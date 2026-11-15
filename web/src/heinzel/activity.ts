// The state of a Heinzel in the one kind of chat. The backend computes it (`SessionSummary.activity`, fixed
// precedence, never the model); this only turns it into a pose for the drawing and a word, and never derives it.
import type { HeinzelState } from "@/components/heinzel";
import type { MessageKey } from "@/i18n";
import { t } from "@/i18n";
import { stateLabel } from "./look";

export const ACTIVITIES = [
  "paused",
  "stuck",
  "waiting_for_you",
  "overloaded",
  "working",
  "waiting_on_others",
  "proud",
  "asleep",
  "idle",
] as const;
export type Activity = (typeof ACTIVITIES)[number];

const POSE: Record<Exclude<Activity, "idle">, HeinzelState> = {
  paused: "paused",
  stuck: "blocked",
  waiting_for_you: "waiting",
  overloaded: "overloaded",
  working: "hammering",
  waiting_on_others: "watching",
  proud: "done",
  asleep: "asleep",
};

/** The drawing for a state. An idle Heinzel looks awake where you are talking to it and dozes everywhere else. */
export function activityPose(activity: Activity | undefined, awake = false): HeinzelState {
  if (!activity || activity === "idle") return awake ? "idle" : "dozing";
  return POSE[activity] ?? "dozing";
}

/** The one word beside the face. An unknown state from a newer service reads as idle, never as an error. */
export function activityWord(activity: Activity | undefined): string {
  const known = activity && (ACTIVITIES as readonly string[]).includes(activity) ? activity : "idle";
  return t(`activity.${known}` as MessageKey);
}

/**
 * The one word on a row: the computed activity, except that a lifecycle fact the activity cannot show wins over idle
 * only (a retired Heinzel is "Retired", a proposal not yet confirmed is "New"); any other activity is the activity.
 */
export function sessionWord(session: { state: string; activity?: Activity }): string {
  if (session.state === "retired") return stateLabel(session.state);
  if (session.state === "proposed" && (!session.activity || session.activity === "idle")) return stateLabel("proposed");
  return activityWord(session.activity);
}
