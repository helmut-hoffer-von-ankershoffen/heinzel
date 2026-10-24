// Settings → Customize → Connectors (ADR 0056): a card per plugin that connects your tools, with whether it is signed
// in and serves something, a live test on demand, what hz can do through it, and Connect, which opens the terminal
// with the vendor's own sign-in command (the tool asks for secrets itself; the app never sees them). Under the cards,
// which connector serves what, one row per capability with a choice of the adapters that can serve it.
import { ChevronDown, ChevronLeft, Plug, RefreshCw } from "lucide-react";
import { type ReactNode, useState } from "react";
import type { SchemaCapabilityChoice, SchemaConnector, SchemaConnectorTest, SchemaSettingInfo } from "@/api/client";
import { useConnectors, useSetSetting, useTestConnector } from "@/api/queries";
import { Button } from "@/components/ui/button";
import { type MessageKey, t } from "@/i18n";
import { offerInTerminal } from "@/lib/terminal";
import { cn } from "@/lib/utils";
import { group, type SettingsNav } from "./settingsSections";

type Entry = SettingsNav["connectors"][number];

type Props = {
  connectors: Entry[];
  open: Entry | undefined;
  settings: SchemaSettingInfo[];
  onOpen: (id: string) => void;
  onFind: (capability: string) => void;
  renderSettings: (settings: SchemaSettingInfo[], heading: string) => ReactNode;
};

type Pill = { label: string; tone: "ok" | "warn" | "muted" | "bad" };

/**
 * What a card's pill says: a failed live test, else the connector's own state, which a passed test can only improve
 * from not signed in or not tested: serving nothing (not bound) stays visible whatever the test found.
 */
export function pill(connector: SchemaConnector, tested?: SchemaConnectorTest): Pill {
  if (tested && !tested.passed) return { label: t("connectors.testFailed"), tone: "bad" };
  const account = tested?.login?.account || connector.login?.account;
  const improves = tested?.passed && ["not-signed-in", "unknown"].includes(connector.state);
  if (improves || connector.state === "connected") {
    return { label: account ? t("connectors.connectedAs", { account }) : t("connectors.connected"), tone: "ok" };
  }
  switch (connector.state) {
    case "not-signed-in":
      return { label: t("connectors.notSignedIn"), tone: "warn" };
    case "not-bound":
      return { label: t("connectors.notBound"), tone: "warn" };
    case "attention":
      return { label: t("connectors.attention"), tone: "warn" };
    default:
      return { label: t("connectors.notTested"), tone: "muted" };
  }
}

const tones = {
  ok: "bg-ok/15 text-ok",
  warn: "bg-warn/15 text-warn",
  bad: "bg-bad/15 text-bad",
  muted: "bg-accent text-muted-foreground",
};

/** The report's connector for a plugin's card: the one whose module the plugin ships. */
const reported = (entry: Entry, connectors: SchemaConnector[]) =>
  connectors.find((connector) => entry.plugin.modules.includes(connector.name));

function CheckAgain() {
  const report = useConnectors();
  return (
    <Button variant="ghost" size="sm" className="ml-auto" disabled={report.isFetching} onClick={() => report.refetch()}>
      <RefreshCw aria-hidden className={cn(report.isFetching && "animate-spin")} />
      {t("connectors.checkAgain")}
    </Button>
  );
}

export function ConnectorsSection({ connectors, open, settings, onOpen, onFind, renderSettings }: Props) {
  const report = useConnectors();
  const all = report.data?.connectors ?? [];
  if (open) {
    const own = settings.filter((setting) => open.groups.includes(group(setting.section)));
    const connector = reported(open, all);
    return (
      <>
        <div className="mb-3 flex items-center">
          <button
            type="button"
            onClick={() => onOpen("connectors")}
            className="-ml-1.5 flex items-center gap-1 rounded-lg px-1.5 py-1 text-ui-sm text-muted-foreground hover:bg-accent/70 hover:text-foreground"
          >
            <ChevronLeft aria-hidden className="size-4 text-icon" />
            {t("connectors.title")}
          </button>
          <CheckAgain />
        </div>
        {connector ? (
          <div className="mb-6">
            <Card entry={open} connector={connector} />
          </div>
        ) : (
          <p className="mb-4 text-sm text-muted-foreground">{open.plugin.summary}</p>
        )}
        {own.length ? (
          renderSettings(own, open.title)
        ) : (
          <>
            <h3 className="mb-2 text-ui font-medium">{open.title}</h3>
            <p className="text-sm text-muted-foreground">{t("connectors.noSettings")}</p>
          </>
        )}
      </>
    );
  }
  return (
    <>
      <div className="mb-1 flex items-center gap-2">
        <h3 className="text-ui font-medium">{t("connectors.title")}</h3>
        <CheckAgain />
      </div>
      <p className="mb-4 text-sm text-muted-foreground">{t("connectors.intro")}</p>
      {connectors.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t("connectors.none")}</p>
      ) : (
        <ul className="grid grid-cols-[repeat(auto-fill,minmax(17rem,1fr))] gap-3">
          {connectors.map((entry) => {
            const connector = reported(entry, all);
            return (
              <li key={entry.id}>
                {connector ? (
                  <Card entry={entry} connector={connector} onOpen={() => onOpen(entry.id)} />
                ) : (
                  <div className="h-full rounded-xl border border-border bg-card p-3">
                    <Title entry={entry} onOpen={() => onOpen(entry.id)} />
                    <p className="mt-1 line-clamp-2 text-ui-sm text-muted-foreground">{entry.plugin.summary}</p>
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
      {report.data && (
        <div className="mt-8">
          <h3 className="mb-1 text-ui font-medium">{t("connectors.bindings")}</h3>
          <ul aria-label={t("connectors.bindings")}>
            {report.data.capabilities.map((choice) => (
              <BindingRow key={choice.capability} choice={choice} connectors={all} onFind={onFind} />
            ))}
          </ul>
        </div>
      )}
    </>
  );
}

function Title({ entry, onOpen }: { entry: Entry; onOpen?: () => void }) {
  const name = <span className="truncate text-ui font-medium">{entry.title}</span>;
  return (
    <span className="flex min-w-0 items-center gap-2">
      <Plug aria-hidden className="size-(--icon) shrink-0 text-icon" />
      {onOpen ? (
        <button type="button" onClick={onOpen} className="truncate text-left hover:underline">
          {name}
        </button>
      ) : (
        name
      )}
      {!entry.plugin.enabled && (
        <span className="ml-auto shrink-0 text-ui-sm text-muted-foreground">{t("plugins.disabled")}</span>
      )}
    </span>
  );
}

function Card({ entry, connector, onOpen }: { entry: Entry; connector: SchemaConnector; onOpen?: () => void }) {
  const test = useTestConnector();
  const report = useConnectors();
  // A test answers for the state it was run against: once the report is read again ("Check again", a binding
  // changed), the fresh state speaks, not the old result.
  const [testedAt, setTestedAt] = useState(0);
  const fresh = testedAt >= report.dataUpdatedAt;
  const tested = fresh && test.data?.name === connector.name ? test.data : undefined;
  const status = pill(connector, tested);
  const serves = connector.capabilities.filter((use) => use.used).map((use) => use.title);
  const unused = connector.capabilities.filter((use) => !use.used);
  const signIn = connector.connect[0];
  const needsSignIn = connector.state === "not-signed-in" || (tested !== undefined && !tested.passed);
  return (
    <article
      aria-label={entry.title}
      className="flex h-full flex-col gap-2 rounded-xl border border-border bg-card p-3 text-left"
    >
      <span className="flex items-center gap-2">
        <Title entry={entry} onOpen={onOpen} />
        <span className={cn("ml-auto shrink-0 rounded-full px-2 py-0.5 text-ui-sm", tones[status.tone])}>
          {status.label}
        </span>
      </span>
      <span className="line-clamp-2 text-ui-sm text-muted-foreground">{entry.plugin.summary}</span>
      <span className={serves.length ? "text-ui-sm text-ok" : "text-ui-sm text-muted-foreground"}>
        {serves.length ? t("connectors.serves", { what: serves.join(", ") }) : t("connectors.unused")}
      </span>
      {tested && (
        <p role="status" className={cn("text-ui-sm", tested.passed ? "text-ok" : "text-bad")}>
          {tested.passed
            ? t("connectors.passed", { ms: tested.latency_ms })
            : t("connectors.failed", {
                ms: tested.latency_ms,
                why: tested.checks.find((check) => check.status !== "up")?.detail ?? "",
              })}
        </p>
      )}
      {test.error && (
        <p role="alert" className="text-ui-sm text-bad">
          {test.error.message}
        </p>
      )}
      <Tools connector={connector} />
      <div className="mt-auto flex flex-wrap gap-2 pt-1">
        {connector.testable && (
          <Button
            variant="outline"
            size="sm"
            disabled={test.isPending}
            onClick={() => test.mutate(connector.name, { onSuccess: () => setTestedAt(Date.now()) })}
          >
            {test.isPending ? t("connectors.testing") : t("connectors.test")}
          </Button>
        )}
        {signIn && needsSignIn && <ConnectButton command={signIn} />}
        {unused.map((use) => (
          <UseFor key={use.capability} connector={entry.title} use={use} />
        ))}
      </div>
    </article>
  );
}

/** Types the sign-in into the terminal for the person to run; "Check again" reads the result. */
function ConnectButton({ command }: { command: string }) {
  return (
    <Button size="sm" title={t("connectors.connectHint", { command })} onClick={() => offerInTerminal(command)}>
      {t("connectors.connect")}
    </Button>
  );
}

function Tools({ connector }: { connector: SchemaConnector }) {
  const [open, setOpen] = useState(false);
  if (!connector.tools.length) return null;
  return (
    <div>
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        className="flex items-center gap-1 text-ui-sm text-muted-foreground hover:text-foreground"
      >
        <ChevronDown aria-hidden className={cn("size-3.5 transition-transform", !open && "-rotate-90")} />
        {t("connectors.tools", { count: connector.tools.length })}
      </button>
      {open && (
        <ul className="mt-1 space-y-0.5 pl-5">
          {connector.tools.map((tool) => (
            <li key={`${tool.capability}.${tool.method}`} className="text-ui-sm" title={tool.summary}>
              <span className="font-mono">
                {tool.capability}.{tool.method}
              </span>{" "}
              <span className={tool.mode === "write" ? "text-warn" : "text-muted-foreground"}>
                {t(tool.mode === "write" ? "connectors.write" : "connectors.read")}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** "Use for Tracker": binds the capability to this connector's adapter, after asking. */
function UseFor({ connector, use }: { connector: string; use: SchemaConnector["capabilities"][number] }) {
  const [asking, setAsking] = useState(false);
  const save = useSetSetting();
  if (!asking) {
    return (
      <Button variant="outline" size="sm" onClick={() => setAsking(true)}>
        {t("connectors.useFor", { what: use.title })}
      </Button>
    );
  }
  return (
    <span className="flex items-center gap-2 text-ui-sm">
      {t("connectors.confirmUse", { connector, what: use.title })}
      <Button
        size="sm"
        disabled={save.isPending}
        onClick={() =>
          save.mutate(
            { key: `capabilities.${use.capability.replaceAll("-", "_")}`, value: use.adapter },
            { onSuccess: () => setAsking(false) },
          )
        }
      >
        {t("connectors.yes")}
      </Button>
      <Button variant="ghost" size="sm" onClick={() => setAsking(false)}>
        {t("plugins.cancel")}
      </Button>
    </span>
  );
}

const AUTOMATIC = "";

/** One capability in plain words, with a choice of the adapters that can serve it; a change saves at once. */
function BindingRow({
  choice,
  connectors,
  onFind,
}: {
  choice: SchemaCapabilityChoice;
  connectors: SchemaConnector[];
  onFind: (capability: string) => void;
}) {
  const save = useSetSetting();
  // Until the report is read again after a change, `choice` is the old one: no second change, so Undo goes back
  // exactly one step.
  const report = useConnectors();
  const [undo, setUndo] = useState<{ text: string; previous: string | null } | null>(null);
  const titleOf = (adapter: string | null | undefined) =>
    choice.candidates.find((candidate) => candidate.adapter === adapter)?.title ?? adapter ?? "";
  const current = choice.how === "configured" ? (choice.bound ?? AUTOMATIC) : choice.how === "off" ? "none" : AUTOMATIC;
  const unset =
    choice.how === "automatic"
      ? t("bindings.automatic", { title: titleOf(choice.bound) })
      : choice.how === "default"
        ? t("bindings.default", { title: titleOf(choice.bound) })
        : choice.how === "unbound"
          ? t("bindings.choose")
          : t("bindings.unset");
  // A binding to an adapter nobody installs any more (its plugin removed) stays visible, so it can be changed.
  const stale = current !== AUTOMATIC && current !== "none" && !choice.candidates.some((c) => c.adapter === current);
  const chosen = connectors.find((connector) =>
    connector.capabilities.some((use) => use.capability === choice.capability && use.adapter === choice.bound),
  );
  const change = (next: string) => {
    const previous = choice.how === "configured" || choice.how === "off" ? current || null : null;
    save.mutate(
      { key: choice.key, value: next === AUTOMATIC ? null : next },
      {
        onSuccess: () =>
          setUndo({
            text:
              next === AUTOMATIC
                ? t("bindings.cleared", { capability: choice.title })
                : t("bindings.changed", {
                    capability: choice.title,
                    adapter: next === "none" ? t("bindings.none") : titleOf(next),
                  }),
            previous,
          }),
      },
    );
  };
  const id = `binding-${choice.capability}`;
  return (
    <li className="border-b border-border/70 py-3 last:border-b-0">
      <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
        {choice.candidates.length === 0 && !stale ? (
          <span className="min-w-0 flex-1">
            <span className="text-ui">{choice.title}</span>
            <span className="text-sm text-muted-foreground"> — {choice.purpose}</span>
          </span>
        ) : (
          <label htmlFor={id} className="min-w-0 flex-1">
            <span className="text-ui">{choice.title}</span>
            <span className="text-sm text-muted-foreground"> — {choice.purpose}</span>
          </label>
        )}
        {choice.candidates.length === 0 && !stale ? (
          <span className="flex items-center gap-2 text-sm text-muted-foreground">
            {t("bindings.nothing")}
            <Button variant="outline" size="sm" onClick={() => onFind(choice.capability)}>
              {t("bindings.find")}
            </Button>
          </span>
        ) : (
          <span className="flex items-center gap-2">
            {chosen && <Dot state={chosen.state} />}
            <select
              id={id}
              value={current}
              disabled={save.isPending || report.isFetching}
              onChange={(event) => change(event.target.value)}
              className="h-8 max-w-64 rounded-lg border border-border bg-input px-2 text-sm outline-none focus:border-muted-foreground/50 disabled:opacity-50"
            >
              <option value={AUTOMATIC}>{unset}</option>
              {stale && <option value={current}>{current}</option>}
              {choice.candidates.map((candidate) => (
                <option key={`${candidate.connector}:${candidate.adapter}`} value={candidate.adapter}>
                  {candidate.title} · {t(`bindings.state.${candidate.state}` as MessageKey)}
                </option>
              ))}
              <option value="none">{t("bindings.none")}</option>
            </select>
            {chosen && chosen.state === "not-signed-in" && chosen.connect[0] && (
              <ConnectButton command={chosen.connect[0]} />
            )}
          </span>
        )}
      </div>
      {choice.problem && <p className="mt-1 text-xs text-warn">{t("bindings.problem", { problem: choice.problem })}</p>}
      {save.error && (
        <p role="alert" className="mt-1 text-xs text-bad">
          {save.error.message}
        </p>
      )}
      {undo && (
        <p role="status" className="mt-1 flex items-center gap-2 text-xs text-muted-foreground">
          {undo.text}
          <button
            type="button"
            className="underline hover:text-foreground"
            onClick={() => save.mutate({ key: choice.key, value: undo.previous }, { onSuccess: () => setUndo(null) })}
          >
            {t("bindings.undo")}
          </button>
        </p>
      )}
    </li>
  );
}

function Dot({ state }: { state: SchemaConnector["state"] }) {
  const color = state === "connected" ? "bg-ok" : state === "unknown" ? "bg-muted-foreground/50" : "bg-warn";
  return (
    <span
      role="img"
      aria-label={t(`bindings.state.${state}` as MessageKey)}
      className={cn("size-2 shrink-0 rounded-full", color)}
    />
  );
}
