// The first-run wizard: a dialog over the app that walks through what `hz setup` asks, plus what only the app can do
// (theme, browser notifications). Every step can be skipped; the step reached is remembered in this browser, and every
// answer is written through hz at once (`hz system config set`, `hz system theme use`), so nothing is lost on closing.
// It opens by itself while hz doesn't know your role or time zone and you haven't closed it, and from the account
// menu ("Set up hz", `?onboarding=`) at any time. Commands that change this machine (joining a team, installing the
// daemon, writing a contract) are confirmed first, then typed into the terminal panel, where the CLI runs them.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, X } from "lucide-react";
import { type KeyboardEvent, type ReactNode, useEffect, useId, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { api, type SchemaProfile, unwrap } from "@/api/client";
import { keys, useConfig, usePlaybooks, useProfile, useSetSetting } from "@/api/queries";
import { Gnome } from "@/components/Gnome";
import { Button } from "@/components/ui/button";
import { type MessageKey, t } from "@/i18n";
import { ThemePreviews } from "@/layout/ThemePreviews";
import { TimeZonePicker } from "@/layout/TimeZonePicker";
import { runInTerminal } from "@/lib/terminal";
import { cn } from "@/lib/utils";
import { setDesktop, useDesktopState } from "@/notifications/desktop";
import { NotificationTest } from "@/notifications/NotificationTest";
import {
  contractName,
  loadProgress,
  type Progress,
  ROLES,
  STEPS,
  type StepId,
  saveProgress,
  setupIncomplete,
} from "./progress";

/** The wizard where the Shell mounts it: open while `?onboarding` is in the URL, or by itself on a first run. */
export function Onboarding() {
  const [params, setParams] = useSearchParams();
  const profile = useProfile();
  const [progress, setProgress] = useState(loadProgress);
  const asked = params.has("onboarding");
  const first = setupIncomplete(profile.data) && !progress.closed;
  // Once open by itself it stays open: answering the first step completes setup, which must not close the wizard.
  const [auto, setAuto] = useState(false);
  useEffect(() => {
    if (first) setAuto(true);
  }, [first]);
  if (!asked && !first && !auto) return null;
  const update = (next: Progress) => {
    saveProgress(next);
    setProgress(next);
  };
  const close = () => {
    update({ ...progress, closed: true });
    setAuto(false);
    if (asked)
      setParams(
        (current) => {
          const next = new URLSearchParams(current);
          next.delete("onboarding");
          return next;
        },
        { replace: true },
      );
  };
  return <OnboardingWizard progress={progress} onProgress={update} onClose={close} />;
}

type Props = { progress: Progress; onProgress: (progress: Progress) => void; onClose: () => void };

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/** Whether `element` takes part in Tab order now: not inside something hidden (the step list on a narrow window). */
const shown = (element: HTMLElement) =>
  typeof element.checkVisibility === "function" ? element.checkVisibility() : true;

export function OnboardingWizard({ progress, onProgress, onClose }: Props) {
  const titleId = useId();
  const dialog = useRef<HTMLDivElement>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const index = STEPS.indexOf(progress.step);
  const last = index === STEPS.length - 1;
  // Each step starts with its heading focused, so a screen reader reads where you are and Tab goes on from there.
  // biome-ignore lint/correctness/useExhaustiveDependencies: the step changing is what moves the focus
  useEffect(() => heading.current?.focus(), [progress.step]);
  // Closing gives the focus back to where it was before the wizard opened (the account menu, or the page).
  useEffect(() => {
    const before = document.activeElement;
    return () => {
      if (before instanceof HTMLElement && before.isConnected) before.focus();
    };
  }, []);
  // Next marks a step done; Skip and the step list only move.
  const go = (step: StepId, done = false) =>
    onProgress({
      ...progress,
      step,
      visited: done ? [...new Set([...progress.visited, progress.step])] : progress.visited,
    });
  const next = () => (last ? finish() : go(STEPS[index + 1] as StepId, true));
  const skip = () => go(STEPS[index + 1] as StepId);
  const finish = () => {
    onProgress({ step: "you", visited: [...STEPS], closed: true });
    onClose();
  };
  // Escape closes; Tab stays inside the dialog.
  const keys = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Escape" && !event.defaultPrevented) {
      event.preventDefault();
      onClose();
    }
    if (event.key !== "Tab" || !dialog.current) return;
    const items = [...dialog.current.querySelectorAll<HTMLElement>(FOCUSABLE)].filter(shown);
    const [first, end] = [items[0], items.at(-1)];
    const inside = items.includes(document.activeElement as HTMLElement);
    if (event.shiftKey && (document.activeElement === first || !inside)) {
      event.preventDefault();
      end?.focus();
    } else if (!event.shiftKey && (document.activeElement === end || !inside)) {
      event.preventDefault();
      first?.focus();
    }
  };
  return (
    <div className="viewport-overlay fixed z-(--layer-modal) flex items-center justify-center bg-black/50 p-4">
      <div
        ref={dialog}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onKeyDown={keys}
        className="flex h-[min(44rem,100%)] w-[min(56rem,100%)] overflow-hidden rounded-xl border border-border bg-popover text-popover-foreground shadow-2xl"
      >
        <nav
          aria-label={t("onboarding.steps")}
          className="hidden w-56 shrink-0 border-r border-border bg-sidebar p-3 sm:block"
        >
          <p id={titleId} className="px-2 pt-1 pb-3 text-ui font-medium">
            {t("onboarding.title")}
          </p>
          <ol className="flex flex-col gap-0.5">
            {STEPS.map((step, position) => (
              <li key={step}>
                <button
                  type="button"
                  onClick={() => go(step)}
                  aria-current={step === progress.step ? "step" : undefined}
                  className={cn(
                    "flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-ui-sm transition-colors hover:bg-accent/70",
                    step === progress.step ? "bg-accent text-foreground" : "text-muted-foreground",
                  )}
                >
                  <span
                    aria-hidden
                    className="flex size-5 shrink-0 items-center justify-center rounded-full border border-border text-xs"
                  >
                    {progress.visited.includes(step) && step !== progress.step ? (
                      <Check className="size-3" />
                    ) : (
                      position + 1
                    )}
                  </span>
                  {t(`onboarding.${step}.title`)}
                  {progress.visited.includes(step) && <span className="sr-only">{t("onboarding.visited")}</span>}
                </button>
              </li>
            ))}
          </ol>
        </nav>
        <div className="flex min-w-0 flex-1 flex-col">
          <header className="flex h-12 shrink-0 items-center border-b border-border pr-2 pl-5">
            <Gnome age="grown" view="bust" className="mr-2" />
            <p className="text-ui-sm text-muted-foreground">
              {t("onboarding.position", { step: index + 1, count: STEPS.length })}
            </p>
            <button
              type="button"
              onClick={onClose}
              aria-label={t("onboarding.close")}
              title={t("onboarding.closeHint")}
              className="ml-auto flex size-8 items-center justify-center rounded-lg text-muted-foreground hover:bg-accent hover:text-foreground"
            >
              <X aria-hidden className="size-(--icon)" />
            </button>
          </header>
          <section aria-labelledby={`${titleId}-step`} className="min-h-0 flex-1 overflow-y-auto px-6 pt-5 pb-8">
            <h2 id={`${titleId}-step`} ref={heading} tabIndex={-1} className="text-lg font-medium focus:outline-none">
              {t(`onboarding.${progress.step}.title`)}
            </h2>
            <p className="mt-1 mb-5 text-sm text-muted-foreground">{t(`onboarding.${progress.step}.intro`)}</p>
            <Step id={progress.step} onClose={onClose} />
          </section>
          <footer className="flex shrink-0 items-center gap-2 border-t border-border px-5 py-3">
            <Button variant="ghost" disabled={index === 0} onClick={() => go(STEPS[index - 1] as StepId)}>
              {t("onboarding.back")}
            </Button>
            <div className="ml-auto flex gap-2">
              {!last && (
                <Button variant="outline" onClick={skip}>
                  {t("onboarding.skip")}
                </Button>
              )}
              <Button onClick={next}>{last ? t("onboarding.finish") : t("onboarding.next")}</Button>
            </div>
          </footer>
        </div>
      </div>
    </div>
  );
}

function Step({ id, onClose }: { id: StepId; onClose: () => void }) {
  switch (id) {
    case "you":
      return <You />;
    case "team":
      return <Team onClose={onClose} />;
    case "look":
      return <ThemePreviews />;
    case "ai":
      return <SettingsLink section="ai" label="onboarding.ai.open" onClose={onClose} />;
    case "connectors":
      return <SettingsLink section="connectors" label="onboarding.connectors.open" onClose={onClose} />;
    case "notifications":
      return <Notifications onClose={onClose} />;
    case "first":
      return <FirstHeinzel />;
  }
}

/** A link into Settings: it closes the wizard (which remembers the step) and opens that section. */
function SettingsLink({ section, label, onClose }: { section: string; label: MessageKey; onClose: () => void }) {
  return (
    <Link
      to={`?settings=${section}`}
      onClick={onClose}
      className="inline-flex h-9 items-center rounded-lg border border-border bg-card px-3 text-sm font-medium hover:bg-accent"
    >
      {t(label)}
    </Link>
  );
}

/** `hz system config set` for one of the wizard's answers; the profile is read again, so the fields show it. */
function useSave() {
  const client = useQueryClient();
  const set = useSetSetting();
  const save = (key: string, value: string | null) =>
    set.mutate({ key, value }, { onSuccess: () => client.invalidateQueries({ queryKey: keys.profile }) });
  return { save, status: set.status, error: set.error?.message };
}

/** A setting's current value from `hz system config show`, as text; "" when unset. */
function useSetting(section: string, key: string): string {
  const config = useConfig();
  const value = config.data?.settings.find((setting) => setting.section === section && setting.key === key)?.value;
  return typeof value === "string" ? value : "";
}

function Saved({ state, error }: { state: "idle" | "pending" | "success" | "error"; error?: string }) {
  return (
    <p role="status" className="flex min-h-5 items-center gap-1 text-xs text-muted-foreground">
      {state === "success" && <Gnome state="done" view="bust" className="size-5" />}
      {state === "pending" ? t("onboarding.saving") : state === "success" ? t("onboarding.saved") : (error ?? "")}
    </p>
  );
}

const field = "h-8 w-full rounded-lg border border-border bg-input px-2.5 text-sm";
const selectField = cn(field, "min-w-0 max-w-full pr-8");

function You() {
  const profile = useProfile();
  // The name field starts from what hz knows, so it waits for the profile.
  return profile.data ? <YouForm me={profile.data} /> : null;
}

function YouForm({ me }: { me: SchemaProfile }) {
  const { save, status, error } = useSave();
  const [name, setName] = useState(me.name ?? "");
  const ids = { name: useId(), role: useId(), zone: useId() };
  const browserZone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  return (
    <div className="flex max-w-md flex-col gap-4">
      <div className="flex flex-col gap-1">
        <label htmlFor={ids.name} className="text-sm">
          {t("onboarding.you.name")}
        </label>
        <input
          id={ids.name}
          className={field}
          value={name}
          placeholder={t("onboarding.you.namePlaceholder")}
          autoComplete="name"
          onChange={(event) => setName(event.target.value)}
          onBlur={() => name.trim() && name.trim() !== me.name && save("org.name", name.trim())}
        />
      </div>
      <div className="flex flex-col gap-1">
        <label htmlFor={ids.role} className="text-sm">
          {t("onboarding.you.role")}
        </label>
        <select
          id={ids.role}
          className={selectField}
          value={me.role ?? ""}
          onChange={(event) => event.target.value && save("org.role", event.target.value)}
        >
          <option value="">{t("settings.notSet")}</option>
          {ROLES.map(([role, short]) => (
            <option key={role} value={role}>
              {short} · {role.replaceAll("-", " ")}
            </option>
          ))}
        </select>
      </div>
      <div className="flex flex-col gap-1">
        <span className="text-sm">{t("onboarding.you.zone")}</span>
        <TimeZonePicker
          id={ids.zone}
          value={me.timezone ?? ""}
          label={t("onboarding.you.zone")}
          onChange={(zone) => save("org.timezone", zone)}
        />
        {!me.timezone && browserZone && (
          <Button variant="ghost" size="sm" className="self-start" onClick={() => save("org.timezone", browserZone)}>
            {t("onboarding.you.useZone", { zone: browserZone })}
          </Button>
        )}
      </div>
      <Saved state={status} error={error} />
    </div>
  );
}

/** A command that changes this machine, typed into the terminal only after the person confirms it here. */
function Confirmed({
  command,
  label,
  disabled,
  onRun,
}: {
  command: string;
  label: string;
  disabled?: boolean;
  onRun?: () => void;
}) {
  // The command as it was when asked, so editing the field behind the question can't change what runs.
  const [asking, setAsking] = useState<string | null>(null);
  const [sent, setSent] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  const done = (run: boolean) => {
    if (run && asking) {
      runInTerminal(asking);
      onRun?.();
    }
    setSent(run);
    setAsking(null);
    // The question unmounts; the focus goes back to the button that asked it.
    window.setTimeout(() => trigger.current?.focus());
  };
  if (asking !== null)
    return (
      <fieldset
        aria-label={label}
        onKeyDown={(event) => {
          if (event.key !== "Escape") return;
          event.stopPropagation();
          event.preventDefault();
          done(false);
        }}
        className="flex flex-wrap items-center gap-2 rounded-lg border border-border bg-card p-2"
      >
        <p className="text-sm">
          {t("onboarding.confirm")} <code className="font-mono text-xs">{asking}</code>
        </p>
        <Button size="sm" autoFocus onClick={() => done(true)}>
          {t("onboarding.run")}
        </Button>
        <Button size="sm" variant="ghost" onClick={() => done(false)}>
          {t("onboarding.cancel")}
        </Button>
      </fieldset>
    );
  return (
    <div className="flex items-center gap-2">
      <Button ref={trigger} variant="outline" disabled={disabled} onClick={() => setAsking(command)}>
        {label}
      </Button>
      {sent && (
        <span role="status" className="text-xs text-muted-foreground">
          {t("onboarding.sent")}
        </span>
      )}
    </div>
  );
}

/** `hz system layer list`: the layers installed, to pick your team's. */
const useLayers = () =>
  useQuery({ queryKey: ["system", "layer", "list"], queryFn: () => unwrap(api.GET("/system/layer/list")) });

function Team({ onClose }: { onClose: () => void }) {
  const config = useConfig();
  // The repository field starts from the configured one, so it waits for the configuration.
  return config.data ? <TeamForm onClose={onClose} /> : null;
}

function TeamForm({ onClose }: { onClose: () => void }) {
  const saved = useSetting("team", "repository");
  const layer = useSetting("team", "layer");
  const [url, setUrl] = useState(saved);
  const layers = useLayers();
  const set = useSetSetting();
  const ids = { url: useId(), layer: useId() };
  const chosen = layers.data?.layers.find((entry) => entry.name === layer);
  const valid = /^(https:\/\/|git@)[\w./:@+-]+$/.test(url.trim());
  return (
    <div className="flex max-w-lg flex-col gap-5">
      <div className="flex flex-col gap-1">
        <label htmlFor={ids.url} className="text-sm">
          {t("onboarding.team.repository")}
        </label>
        <input
          id={ids.url}
          className={field}
          value={url}
          placeholder="https://github.com/acme/payments-team"
          onChange={(event) => setUrl(event.target.value)}
        />
        <p className="text-xs text-muted-foreground">{t("onboarding.team.repositoryHint")}</p>
        <Confirmed command={`hz team join ${url.trim()}`} label={t("onboarding.team.join")} disabled={!valid} />
      </div>
      <div className="flex flex-col gap-1">
        <label htmlFor={ids.layer} className="text-sm">
          {t("onboarding.team.layer")}
        </label>
        <select
          id={ids.layer}
          className={selectField}
          value={layer}
          disabled={!layers.data?.layers.length}
          onChange={(event) => set.mutate({ key: "team.layer", value: event.target.value || null })}
        >
          <option value="">
            {layers.data?.layers.length ? t("onboarding.team.noLayer") : t("onboarding.team.none")}
          </option>
          {layers.data?.layers.map((entry) => (
            <option key={entry.name} value={entry.name}>
              {entry.name} · {entry.label}
            </option>
          ))}
        </select>
        <Saved state={set.status} error={set.error?.message} />
      </div>
      {chosen && (
        <div className="rounded-lg border border-border bg-card p-3 text-sm">
          <p className="font-medium">{t("onboarding.team.brings", { layer: chosen.name })}</p>
          <ul className="mt-1 list-disc pl-5 text-muted-foreground">
            {chosen.playbook && <li>{chosen.playbook}</li>}
            {chosen.adapters.map((adapter) => (
              <li key={adapter}>{adapter}</li>
            ))}
          </ul>
        </div>
      )}
      <SettingsLink section="plugins" label="onboarding.team.plugins" onClose={onClose} />
    </div>
  );
}

/** `hz system daemon status`, re-read while the step is open: the terminal install is seen when it finishes. */
const useDaemonStatus = () =>
  useQuery({
    queryKey: ["system", "daemon", "status"],
    queryFn: () => unwrap(api.GET("/system/daemon/status")),
    refetchInterval: 3000,
  });

function Notifications({ onClose }: { onClose: () => void }) {
  const state = useDesktopState();
  const daemon = useDaemonStatus().data;
  const [busy, setBusy] = useState(false);
  return (
    <div className="flex max-w-lg flex-col gap-5">
      <Item title={t("onboarding.notifications.browser")} hint={t(`notify.state.${state}`)}>
        <Button
          variant="outline"
          disabled={busy || state === "on" || state === "unsupported" || state === "denied"}
          onClick={() => {
            setBusy(true);
            void setDesktop(true).finally(() => setBusy(false));
          }}
        >
          {state === "on" ? t("onboarding.notifications.on") : t("onboarding.notifications.allow")}
        </Button>
      </Item>
      <Item title={t("onboarding.notifications.native")} hint={t("onboarding.notifications.nativeHint")}>
        <NotificationTest label={t("onboarding.notifications.test")} onTerminal={onClose} />
      </Item>
      <Item title={t("onboarding.notifications.daemon")} hint={t("onboarding.notifications.daemonHint")}>
        {daemon?.installed && daemon.loaded ? (
          <span role="status" className="text-sm text-ok">
            {t("onboarding.notifications.installed")}
          </span>
        ) : (
          <>
            <Confirmed
              command="hz system daemon install"
              label={t("onboarding.notifications.install")}
              onRun={onClose}
            />
            {daemon?.installed && (
              <span role="status" className="text-xs text-warn">
                {t("onboarding.notifications.notLoaded")}
              </span>
            )}
          </>
        )}
      </Item>
    </div>
  );
}

function Item({ title, hint, children }: { title: string; hint: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-1.5">
      <p className="text-sm font-medium">{title}</p>
      <p className="text-xs text-muted-foreground">{hint}</p>
      <div className="flex flex-wrap items-center gap-2">{children}</div>
    </div>
  );
}

function FirstHeinzel() {
  const playbooks = usePlaybooks();
  const templates = playbooks.data?.playbooks.flatMap((playbook) => playbook.templates) ?? [];
  const [sentence, setSentence] = useState("");
  const [template, setTemplate] = useState("");
  const id = useId();
  const group = useId();
  const name = contractName(sentence);
  const chosen = templates.find((entry) => entry.template === template);
  const command = chosen?.command ?? (name ? `hz heinzel new ${name}` : "");
  return (
    <div className="flex max-w-lg flex-col gap-5">
      {templates.length > 0 && (
        <fieldset className="flex flex-col gap-1.5">
          <legend className="mb-1 text-sm">{t("onboarding.first.template")}</legend>
          {templates.map((entry) => (
            <label key={entry.template} className="flex items-center gap-2 text-sm">
              <input
                type="radio"
                name={group}
                checked={template === entry.template}
                onChange={() => {
                  setTemplate(entry.template);
                  setSentence("");
                }}
              />
              <code className="font-mono text-xs">{entry.template}</code>
            </label>
          ))}
        </fieldset>
      )}
      <div className="flex flex-col gap-1">
        <label htmlFor={id} className="text-sm">
          {t("onboarding.first.sentence")}
        </label>
        <input
          id={id}
          className={field}
          value={sentence}
          placeholder={t("onboarding.first.placeholder")}
          onChange={(event) => {
            setSentence(event.target.value);
            setTemplate("");
          }}
        />
        {name && !template && <p className="text-xs text-muted-foreground">{t("onboarding.first.name", { name })}</p>}
      </div>
      <Confirmed command={command} label={t("onboarding.first.create")} disabled={!command} />
    </div>
  );
}
