// The "Today" strip on Home: what needs you, as cards you can open, ask the chat about, or dismiss (ADR 0045).
// The cards come from the notices of the last day (`hz system notify list`, ADR 0043), each with its id as the chat
// context (`event:<id>`, as `hz chat open --context <id>` gives it), and from what the app reads anyway: the doctor's
// report, the daemon's jobs and your profile, unless a notice already says the same.
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import {
  api,
  type SchemaHealthReport,
  type SchemaJobInfo,
  type SchemaNoticeList,
  type SchemaProfile,
  unwrap,
} from "@/api/client";
import { useDoctor, useJobs, useProfile } from "@/api/queries";
import { format, t } from "@/i18n";

export type TodayCard = {
  /** Stable while what it says is the same, so a dismissed card comes back when it changes. */
  id: string;
  tone: "bad" | "warn" | "info";
  title: string;
  detail: string;
  /** The screen that shows it. */
  route: string;
  /** The chat context of "Ask about it": `route:/doctor`, `event:<id>`. */
  context: string;
  /** For a notice, what it is about (`failed`, `doctor`, `draft`), so the app doesn't say it twice. */
  kind?: string;
};

const KEY = "hz.today.dismissed";

export function doctorCards(report: SchemaHealthReport | undefined): TodayCard[] {
  if (!report || report.status === "up") return [];
  const failing = Object.entries(report.checks).flatMap(([module, checks]) =>
    checks.filter((check) => check.status !== "up").map((check) => `${module} ${check.name}`),
  );
  return [
    {
      id: `doctor:${report.status}:${failing.join(",")}`,
      tone: report.status === "down" ? "bad" : "warn",
      title: t("today.doctor", { count: failing.length }),
      detail: failing.slice(0, 3).join(", ") + (failing.length > 3 ? "…" : ""),
      route: "/doctor",
      context: "route:/doctor",
    },
  ];
}

export function jobCards(jobs: SchemaJobInfo[] | undefined): TodayCard[] {
  return (jobs ?? [])
    .filter((job) => job.last_disposition === "failed" || job.problem)
    .map((job) => ({
      id: `job:${job.job}:${job.last_as_of ?? ""}:${job.problem ?? ""}`,
      tone: "bad" as const,
      title: t("today.runFailed", { job: job.job }),
      detail: job.problem ?? (job.last_as_of ? t("today.lastRun", { when: format.dateTime(job.last_as_of) }) : ""),
      route: "/contracts",
      context: `text:the daemon job ${job.job}`,
    }));
}

export function profileCards(profile: SchemaProfile | undefined): TodayCard[] {
  if (!profile || profile.complete) return [];
  return [
    {
      id: `profile:${profile.missing.join(",")}`,
      tone: "info",
      title: t("today.profile"),
      detail: t("home.profileMissing", { missing: profile.missing.join(", ") }),
      route: "/profile",
      context: "route:/profile",
    },
  ];
}

const DAY = 24 * 60 * 60 * 1000;
const NOTICES = 5;
const TONE: Record<string, TodayCard["tone"]> = { failed: "bad", doctor: "warn" };

/** The notices of the last day, newest first, at most five. */
export function eventCards(list: SchemaNoticeList | undefined, now = Date.now()): TodayCard[] {
  return (list?.notices ?? [])
    .filter((notice) => now - new Date(notice.at).getTime() < DAY)
    .slice(0, NOTICES)
    .map((notice) => ({
      id: `event:${notice.id}`,
      kind: notice.kind,
      tone: TONE[notice.kind] ?? "info",
      title: notice.title,
      detail: notice.body,
      route: notice.route,
      context: `event:${notice.id}`,
    }));
}

/** The notices the web app may read; none when the notify module is off or not there. */
const useNotices = () =>
  useQuery({
    queryKey: ["system", "notify", "list"],
    queryFn: () => unwrap(api.GET("/system/notify/list", { params: { query: { limit: 20 } } })),
    retry: false,
  });

function loadDismissed(): Set<string> {
  try {
    return new Set(JSON.parse(localStorage.getItem(KEY) ?? "[]") as string[]);
  } catch {
    return new Set();
  }
}

export function useToday() {
  const doctor = useDoctor();
  const jobs = useJobs();
  const profile = useProfile();
  const notices = useNotices();
  const [dismissed, setDismissed] = useState(loadDismissed);
  const events = eventCards(notices.data);
  const said = new Set(events.map((card) => card.kind));
  const all = [
    ...events,
    ...(said.has("doctor") ? [] : doctorCards(doctor.data)),
    ...(said.has("failed") ? [] : jobCards(jobs.data)),
    ...profileCards(profile.data),
  ];
  const dismiss = (id: string) => {
    const next = new Set(dismissed).add(id);
    setDismissed(next);
    try {
      // Only ids still shown, so the list doesn't grow forever.
      localStorage.setItem(KEY, JSON.stringify(all.map((card) => card.id).filter((card) => next.has(card))));
    } catch {
      // No storage: dismissed for this visit only.
    }
  };
  return {
    cards: all.filter((card) => !dismissed.has(card.id)),
    loading: doctor.isLoading || jobs.isLoading || profile.isLoading,
    dismiss,
  };
}
