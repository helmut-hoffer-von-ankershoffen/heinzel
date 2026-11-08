// What the Updates pane says, decided in one place: one plain line per thing that differs (Heinzel itself, each
// plugin), each with what to do about it. Pure, so every state is tested without rendering. Green ("ok") is claimed
// only when a fresh check confirmed Heinzel and every plugin; anything unknown stays neutral, never green.

import type { State } from "@/components/FooterItem";
import { t } from "@/i18n";
import type { AutoUpdateStatus, PluginHold } from "./autoUpdateApi";
import type { UpdateStatus } from "./controller";

/** A check older than this (four of the server's thirty-minute rounds) no longer confirms anything. */
export const FRESH_MS = 2 * 60 * 60 * 1000;

export type Row = {
  id: string;
  tone: State;
  text: string;
  detail?: string;
  /** A spinner instead of a dot: something is being asked or installed right now. */
  busy?: boolean;
  alert?: boolean;
};

export type Inputs = {
  status: UpdateStatus | undefined;
  /** The release-status request failed, as opposed to not having answered yet. */
  statusFailed: boolean;
  plan: AutoUpdateStatus | undefined;
  planFailed: boolean;
  now: Date;
  /** The installer's own words while it runs, and its last answer. */
  phase: string | null;
  message: string | null;
  problem: string | null;
};

export type View = {
  /** The footer icon's state. */
  tone: State;
  /** Whether any update is known, for the footer icon's name and pulse. */
  available: boolean;
  rows: Row[];
  /** Everything known to be newer, for the button: one thing says "Update now", several "Update all". */
  count: number;
  /** Whether the pane can offer to look again. */
  canRetry: boolean;
  /** True only when a fresh check confirmed Heinzel and all its plugins. */
  allCurrent: boolean;
};

const parse = (value: string | null | undefined): Date | null => {
  const date = value ? new Date(value) : null;
  return date && !Number.isNaN(date.getTime()) ? date : null;
};

/** "10:32" today, "Nov 6, 10:32" on another day. */
export function whenText(date: Date, now: Date): string {
  const time = date.toLocaleTimeString("en", { hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
  if (date.toDateString() === now.toDateString()) return time;
  return `${date.toLocaleDateString("en", { month: "short", day: "numeric" })}, ${time}`;
}

const wherePhrase = (hold: PluginHold): string =>
  t(hold.how === "folder" ? "updates.fromFolder" : hold.how === "link" ? "updates.fromLink" : "updates.fromGit");

/** Every line of the pane for these facts. */
export function describe(input: Inputs): View {
  const { status, plan, now } = input;
  const rows: Row[] = [];
  const lastOk = parse(status?.last_ok_at);
  const fresh = lastOk !== null && now.getTime() - lastOk.getTime() < FRESH_MS && status?.check_failed !== true;
  const coreNews = status?.available ? (status.latest ?? "") : (plan?.core ?? null);
  const plugins = Object.entries(plan?.plugins ?? {});
  const holds = plan?.holds ?? [];
  const held = plan?.held ?? plan?.development ?? null;
  const planOff = plan?.phase === "off";
  const applying = input.phase !== null || plan?.phase === "applying";
  const count = (coreNews ? 1 : 0) + plugins.length;

  // What is being installed, or was just installed or refused.
  if (applying) rows.push({ id: "applying", tone: "warn", text: input.phase ?? t("updates.applying"), busy: true });
  else if (input.message ?? plan?.message)
    rows.push({ id: "message", tone: "off", text: (input.message ?? plan?.message) as string });
  if (input.problem) rows.push({ id: "problem", tone: "bad", text: input.problem, alert: true });

  // Heinzel itself.
  const loading = status === undefined && !input.statusFailed;
  const coreUnknown =
    status === undefined ? input.statusFailed : status.check_failed === true || (!fresh && lastOk !== null);
  const neverAsked = status !== undefined && lastOk === null && status.check_failed !== true;
  if (coreNews) {
    rows.push({ id: "core", tone: "warn", text: t("updates.coreAvailable", { version: coreNews }) });
  } else if (held && plan?.held) {
    rows.push({ id: "core", tone: "off", text: held });
  } else if (loading || neverAsked) {
    rows.push({ id: "core", tone: "off", text: t("updates.checking"), busy: true });
  } else if (coreUnknown) {
    const detail = lastOk ? t("updates.lastGood", { when: whenText(lastOk, now) }) : t("updates.neverChecked");
    rows.push({ id: "core", tone: "off", text: t("updates.coreUnknown"), detail });
  } else if (status) {
    rows.push({
      id: "core",
      tone: "ok",
      text: t("updates.coreCurrent", { version: status.current, when: whenText(lastOk as Date, now) }),
    });
  }

  // Plugins: only the ones that differ from "current" get a line.
  for (const [name, version] of plugins)
    rows.push({ id: `plugin:${name}`, tone: "warn", text: t("updates.pluginAvailable", { name, version }) });
  for (const hold of holds)
    rows.push({
      id: `hold:${hold.name}`,
      tone: "off",
      text:
        hold.why === "pinned"
          ? t("updates.pinned", { name: hold.name, version: hold.version ?? "" })
          : t("updates.bySource", { name: hold.name, where: wherePhrase(hold) }),
    });
  let pluginsUnknown = false;
  if (plan === undefined) {
    pluginsUnknown = status !== undefined;
    if (pluginsUnknown) rows.push({ id: "plugins", tone: "off", text: t("updates.pluginsUnknown") });
  } else if (planOff) {
    pluginsUnknown = true;
    if (!held)
      rows.push({
        id: "plugins",
        tone: "off",
        text: t("updates.pluginsNotChecked"),
        detail: plan.reason ?? undefined,
      });
  } else if (plan.plugins_checked === false) {
    pluginsUnknown = true;
    rows.push({ id: "plugins", tone: "off", text: t("updates.pluginsUnknown") });
  } else if (plan.plugins_checked == null) {
    pluginsUnknown = true;
    if (!loading) rows.push({ id: "plugins", tone: "off", text: t("updates.pluginsChecking"), busy: true });
  }

  const allCurrent =
    !applying &&
    !input.problem &&
    status !== undefined &&
    fresh &&
    coreNews === null &&
    plan !== undefined &&
    !planOff &&
    plan.core_checked !== false &&
    plan.plugins_checked === true &&
    plugins.length === 0 &&
    holds.length === 0;
  if (allCurrent && rows.length === 1 && rows[0]?.id === "core") rows[0].tone = "ok";

  // Heinzel is confirmed current but something else is not: the Heinzel line keeps its own, honest colour.
  const tone: State = input.problem ? "bad" : count > 0 ? "warn" : allCurrent ? "ok" : "off";
  const unknownSomewhere = coreUnknown || pluginsUnknown || input.planFailed;
  return { tone, available: count > 0, rows, count, canRetry: unknownSomewhere && !applying, allCurrent };
}
