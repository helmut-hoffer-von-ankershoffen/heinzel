// Every string the app shows, in one catalogue (ADR 0039): adding a language is a file, not a refactor.
// The English messages live per area in ./messages/; this file assembles them and holds `t` and `format`.
import { calendar } from "./messages/calendar";
import { chat } from "./messages/chat";
import { heinzel } from "./messages/heinzel";
import { notify } from "./messages/notify";
import { onboarding } from "./messages/onboarding";
import { plugins } from "./messages/plugins";
import { runtime } from "./messages/runtime";
import { settings } from "./messages/settings";
import { shell } from "./messages/shell";

const en = {
  ...calendar,
  ...chat,
  ...heinzel,
  ...notify,
  ...onboarding,
  ...plugins,
  ...runtime,
  ...settings,
  ...shell,
} as const;

export type MessageKey = keyof typeof en;

/** The message for `key`, with `{name}` placeholders filled from `values`. */
export function t(key: MessageKey, values: Record<string, string | number> = {}): string {
  return en[key].replace(/\{(\w+)\}/g, (match, name: string) => (name in values ? String(values[name]) : match));
}

const usd = new Intl.NumberFormat("en", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 4,
});
const dateTime = new Intl.DateTimeFormat("en", {
  dateStyle: "medium",
  timeStyle: "short",
  hourCycle: "h23",
});
const date = new Intl.DateTimeFormat("en", { dateStyle: "medium" });

export const format = {
  usd: (value: number) => usd.format(value),
  dateTime: (value: string) => dateTime.format(new Date(value)),
  date: (value: string) => date.format(new Date(value)),
};
