// `hz ai access use` as one visible choice next to the theme (ADR 0057): one card per option with its title, a status
// dot and one status line, an "In use" marker on the chosen one, a Set up button where hz can set it up here, and a
// test button that runs `hz ai access test` only when pressed, since a live check may cost a little. Picking an option
// applies at once: the running hz takes up the agent runner before its next call, so nothing asks for a restart.
// Commands are for people who want them: only inside each card's Details.
import { type QueryClient, useQueryClient } from "@tanstack/react-query";
import { FlaskConical, RotateCw, Settings2, Trash2, X } from "lucide-react";
import { type ReactNode, useId, useState } from "react";
import type { SchemaAccessOption, SchemaAccessTestReport, SchemaOperationCall } from "@/api/client";
import { api, unwrap } from "@/api/client";
import {
  useAiAccess,
  useAiKey,
  useRemoveAiKey,
  useSetAiAccess,
  useSetAiKey,
  useSetSetting,
  useTestAiAccess,
} from "@/api/queries";
import { CopyCommand } from "@/components/common";
import { PaneAction, PaneActions } from "@/components/PaneActions";
import { type MessageKey, t } from "@/i18n";
import {
  acknowledgePluginRestart,
  acquirePluginChange,
  completePluginRestart,
  forgetPluginRestart,
  retainPluginRestart,
} from "@/layout/pluginActivation";
import { PLANS } from "@/lib/operationPlans";
import { OperationRecoveryRequired, type Phase, requestDrivenOperation } from "@/lib/operationRun";
import { cn } from "@/lib/utils";
import { ApplySteps, applyWithSteps } from "@/lifecycle/applySteps";
import { RestartApplyError, type RestartObservation } from "@/lifecycle/controller";

type Tone = "ok" | "idle" | "warn" | "bad";
const tone: Record<SchemaAccessOption["status"], Tone> = {
  "signed in": "ok",
  tested: "ok",
  "not signed in": "warn",
  "not tested": "idle",
  "test failed": "bad",
  "needs setup": "idle",
  error: "bad",
};
const DOT: Record<Tone, string> = { ok: "bg-ok", idle: "bg-muted-foreground/60", warn: "bg-warn", bad: "bg-bad" };
const WORD: Record<SchemaAccessOption["status"], MessageKey> = {
  "signed in": "aiAccess.state.signedIn",
  tested: "aiAccess.state.tested",
  "not signed in": "aiAccess.state.notSignedIn",
  "not tested": "aiAccess.state.notTested",
  "test failed": "aiAccess.state.testFailed",
  "needs setup": "aiAccess.state.needsSetup",
  error: "aiAccess.state.error",
};
/** A few models Pydantic AI names, offered in the Model API's set-up dialog; any other one can be typed. */
export const SUGGESTED_MODELS = ["anthropic:claude-sonnet-4-5", "anthropic:claude-haiku-4-5", "openai:gpt-5"];

export function AiAccessSetting() {
  const client = useQueryClient();
  const access = useAiAccess();
  const change = useSetAiAccess();
  const check = useTestAiAccess();
  const labelId = useId();
  const [setting, setSetting] = useState<SchemaAccessOption | null>(null);
  const options = access.data?.options ?? [];
  const note = change.error?.message ?? change.data?.note ?? access.data?.note;
  const tested = check.data;
  return (
    <div className="border-b border-border/70 py-3">
      <p id={labelId} className="text-ui">
        {t("aiAccess.title")}
      </p>
      <p className="mt-0.5 text-sm text-muted-foreground">{t("aiAccess.intro")}</p>
      {note && <p className="mt-1 text-sm text-warn">{note}</p>}
      <div role="radiogroup" aria-labelledby={labelId} className="mt-3 flex flex-col gap-2">
        {options.map((option) => (
          <Option
            key={option.name}
            option={option}
            busy={change.isPending}
            testing={check.isPending && check.variables === option.name}
            result={tested?.name === option.name ? tested : undefined}
            onSelect={() => !option.in_use && change.mutate(option.name)}
            onTest={() => check.mutate(option.name)}
            onSetUp={() => {
              if (option.setup === "client") requestClientInstall(option, client);
              else setSetting(option);
            }}
          />
        ))}
      </div>
      {setting && <SetUpDialog option={setting} onClose={() => setSetting(null)} />}
    </div>
  );
}

type OptionProps = {
  option: SchemaAccessOption;
  busy: boolean;
  testing: boolean;
  result?: SchemaAccessTestReport;
  onSelect: () => void;
  onTest: () => void;
  onSetUp: () => void;
};

/** The one status line: the state first, the one missing thing after it; never a command. */
export function statusLine(option: SchemaAccessOption): string {
  // A head older or newer than this page may send a state it doesn't know: show it as it came.
  const key = WORD[option.status] as MessageKey | undefined;
  const word = key ? t(key) : option.status;
  return option.needs ? t("aiAccess.needs", { state: word, needs: option.needs }) : word;
}

function Option({ option, busy, testing, result, onSelect, onTest, onSetUp }: OptionProps) {
  const detailId = useId();
  const usable = option.status !== "needs setup" && option.status !== "error";
  const settable = option.setup != null;
  return (
    <div
      data-option={option.name}
      className={cn("rounded-lg border border-border px-3 py-2", option.in_use && "border-primary bg-accent/40")}
    >
      {/* biome-ignore lint/a11y/useSemanticElements: a card of a radio group, with a status and a fix an input can't hold */}
      <button
        type="button"
        role="radio"
        aria-checked={option.in_use}
        aria-describedby={detailId}
        disabled={busy || (!usable && !option.in_use)}
        onClick={onSelect}
        className="w-full min-w-0 text-left [overflow-wrap:anywhere] disabled:opacity-60"
      >
        <span className="flex flex-wrap items-baseline gap-x-2">
          <span className="text-ui font-medium">{option.title}</span>
          {option.in_use && <span className="text-ui-sm text-primary">{t("aiAccess.inUse")}</span>}
        </span>
        <span className="mt-0.5 flex items-center gap-1.5 text-ui-sm">
          <span
            aria-hidden="true"
            data-tone={tone[option.status]}
            className={cn("size-2 rounded-full", DOT[tone[option.status]])}
          />
          <span data-status-line="">{statusLine(option)}</span>
        </span>
        <span id={detailId} className="block text-sm text-muted-foreground">
          {option.summary}
        </span>
        {result && <TestResult result={result} />}
      </button>
      {(option.detail || option.fix) && (
        <details className="mt-1 text-ui-sm text-muted-foreground">
          <summary className="cursor-pointer select-none">{t("aiAccess.details")}</summary>
          {option.detail && <p className="mt-0.5 [overflow-wrap:anywhere]">{option.detail}</p>}
          {option.fix && <p className="mt-0.5 font-mono [overflow-wrap:anywhere]">{option.fix}</p>}
        </details>
      )}
      <PaneActions>
        {settable && (
          <PaneAction key="setup" icon={Settings2} onClick={onSetUp}>
            {t("aiAccess.setUpOne", { title: option.title })}
          </PaneAction>
        )}
        <PaneAction key="test" icon={FlaskConical} disabled={testing || !usable} onClick={onTest}>
          {testing ? t("aiAccess.testing") : t("aiAccess.testOne", { title: option.title })}
        </PaneAction>
      </PaneActions>
    </div>
  );
}

function TestResult({ result }: { result: SchemaAccessTestReport }) {
  return (
    <span role="status" className={cn("mt-0.5 block text-sm", result.ok ? "text-ok" : "text-bad")}>
      {result.ok ? t("aiAccess.answered", { detail: result.detail }) : t("aiAccess.failed", { detail: result.detail })}
    </span>
  );
}

/** The fields one option's set-up writes, as configuration keys; credentials never go here. */
const FIELDS: Record<"model" | "gateway", { key: string; label: MessageKey; placeholder: string; list?: string }[]> = {
  model: [
    { key: "ai.access.model", label: "aiAccess.setup.model", placeholder: SUGGESTED_MODELS[0] ?? "", list: "models" },
  ],
  gateway: [
    {
      key: "ai.access.gateway_status_command",
      label: "aiAccess.setup.statusCommand",
      placeholder: '["acme", "ai", "status", "--json"]',
    },
    {
      key: "ai.access.gateway_test_command",
      label: "aiAccess.setup.testCommand",
      placeholder: '["acme", "ai", "test"]',
    },
  ],
};

/** One small dialog over the settings: a title, a close button and its content. */
function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  const titleId = useId();
  return (
    // biome-ignore lint/a11y/noStaticElementInteractions: a click on the backdrop closes it; Escape and the close button do for keyboards
    <div
      className="viewport-overlay fixed z-(--layer-modal) flex items-center justify-center bg-black/50 p-4"
      onMouseDown={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onKeyDown={(event) => {
          if (event.key === "Escape") {
            event.stopPropagation();
            onClose();
          }
        }}
        onMouseDown={(event) => event.stopPropagation()}
        className="flex w-[min(32rem,100%)] flex-col rounded-xl border border-border bg-popover p-4 shadow-2xl"
      >
        <header className="flex items-center">
          <h2 id={titleId} className="text-ui font-medium">
            {title}
          </h2>
          <div data-pane-chrome="" className="ml-auto">
            <PaneAction ghost icon={X} onClick={onClose}>
              {t("settings.close")}
            </PaneAction>
          </div>
        </header>
        <div className="mt-3 flex flex-col gap-3">{children}</div>
      </div>
    </div>
  );
}

function SetUpDialog({ option, onClose }: { option: SchemaAccessOption; onClose: () => void }) {
  const title = t("aiAccess.setUpOne", { title: option.title });
  return (
    <Modal title={title} onClose={onClose}>
      {option.setup === "sign-in" ? <SignInSetup option={option} /> : <FormSetup option={option} />}
    </Modal>
  );
}

/** Model and gateway settings, and for the Model API its key, which goes only to the credentials store. */
function FormSetup({ option }: { option: SchemaAccessOption }) {
  const listId = useId();
  const save = useSetSetting();
  const check = useTestAiAccess();
  const kind = option.setup === "gateway" ? "gateway" : "model";
  const fields = FIELDS[kind];
  const [values, setValues] = useState<Record<string, string>>({});
  const [saved, setSaved] = useState(false);
  const [secret, setSecret] = useState("");
  const keyState = useAiKey();
  const storeKey = useSetAiKey();
  const removeKey = useRemoveAiKey();
  const store = async () => {
    for (const field of fields) {
      const value = values[field.key]?.trim();
      if (value) await save.mutateAsync({ key: field.key, value });
    }
    if (kind === "model" && secret.trim()) {
      await storeKey.mutateAsync(secret.trim());
      setSecret(""); // gone from the page as soon as it is kept
    }
    setSaved(true);
  };
  const error = save.error ?? storeKey.error ?? removeKey.error;
  return (
    <form
      className="flex flex-col gap-3"
      onSubmit={(event) => {
        event.preventDefault();
        void store().catch(() => undefined);
      }}
    >
      {fields.map((field, index) => (
        <label key={field.key} className="flex flex-col gap-1 text-sm">
          {t(field.label)}
          <input
            // biome-ignore lint/a11y/noAutofocus: the dialog opens to type its first field
            autoFocus={index === 0}
            list={field.list ? listId : undefined}
            value={values[field.key] ?? ""}
            placeholder={field.placeholder}
            onChange={(event) => setValues({ ...values, [field.key]: event.target.value })}
            className="rounded-md border border-border bg-background px-2 py-1 font-mono text-ui-sm"
          />
        </label>
      ))}
      {kind === "model" && (
        <>
          <datalist id={listId}>
            {SUGGESTED_MODELS.map((model) => (
              <option key={model} value={model} />
            ))}
          </datalist>
          <label className="flex flex-col gap-1 text-sm">
            {t("aiAccess.setup.key")}
            <input
              type="password"
              autoComplete="off"
              spellCheck={false}
              value={secret}
              placeholder={keyState.data?.stored ? t("aiAccess.setup.keyStored") : ""}
              onChange={(event) => setSecret(event.target.value)}
              className="rounded-md border border-border bg-background px-2 py-1 font-mono text-ui-sm"
            />
          </label>
          <p className="flex items-center gap-2 text-ui-sm text-muted-foreground">
            <span data-key-state="">
              {keyState.data?.stored ? t("aiAccess.setup.keyKept") : t("aiAccess.setup.keyNote")}
            </span>
            {keyState.data?.stored && (
              <span data-pane-chrome="">
                <PaneAction ghost icon={Trash2} type="button" onClick={() => removeKey.mutate()}>
                  {t("aiAccess.setup.removeKey")}
                </PaneAction>
              </span>
            )}
          </p>
        </>
      )}
      {error && <p className="text-sm text-bad">{error.message}</p>}
      {saved && !error && <p className="text-sm text-ok">{t("aiAccess.setup.saved")}</p>}
      {check.data && <TestResult result={check.data} />}
      {check.error && <p className="text-sm text-bad">{check.error.message}</p>}
      <PaneActions align="end" words="all">
        <PaneAction
          key="test"
          icon={FlaskConical}
          type="button"
          disabled={!saved || check.isPending}
          onClick={() => check.mutate(option.name)}
        >
          {check.isPending ? t("aiAccess.testing") : t("aiAccess.test")}
        </PaneAction>
        <PaneAction key="save" icon={Settings2} primary type="submit" disabled={save.isPending || storeKey.isPending}>
          {t("aiAccess.setup.save")}
        </PaneAction>
      </PaneActions>
    </form>
  );
}

const CLIENT_INSTALL_PENDING = "hz.ai-client-install-pending.v1";
// The receipt survives reloads. Preserve the server's error too while this page is open.
let clientInstallError: string | null = null;

function clientInstallUncertain(): boolean {
  try {
    return window.sessionStorage.getItem(CLIENT_INSTALL_PENDING) !== null;
  } catch {
    return true;
  }
}

function clientInstallRecovery(message?: string): OperationRecoveryRequired {
  const unknown = t("plugins.operationUnknown");
  return new OperationRecoveryRequired(message ?? unknown, {
    message: unknown,
    help: t("plugins.operationUnknownHelp"),
    action: t("plugins.operationResolve"),
    confirm: t("plugins.operationResolveConfirm"),
    clear: () => {
      try {
        window.sessionStorage.removeItem(CLIENT_INSTALL_PENDING);
        clientInstallError = null;
      } catch {
        throw new Error(t("plugins.operationUnavailable"));
      }
    },
  });
}

/** Consent and progress for adding a client of hz itself, even after Settings unmounts. */
function requestClientInstall(option: SchemaAccessOption, client: QueryClient): void {
  const extra = option.client;
  if (extra !== "openai" && extra !== "anthropic") return;
  const call: SchemaOperationCall = {
    method: "POST",
    path: "/system/plugin/add-extra",
    query: { extra },
    command: `hz system plugin add-extra ${extra}`,
    summary: t("aiAccess.setUpOne", { title: option.title }),
    read_only: false,
  };
  let restart: RestartObservation | undefined;
  let phases: Phase[] = [];
  requestDrivenOperation(
    call,
    {
      consent: [t("aiAccess.client.confirm", { needs: option.needs }), t("aiAccess.client.why")],
      action: t("aiAccess.client.add"),
      runningTitle: t("aiAccess.client.runningTitle", { title: option.title }),
      doneTitle: t("lifecycle.phase.applied"),
      running: t("aiAccess.client.running"),
      run: async (report) => {
        if (!restart && clientInstallUncertain()) throw clientInstallRecovery(clientInstallError ?? undefined);
        // Reserve the environment from the first mutation through verification of the restarted server.
        const release = acquirePluginChange(restart);
        if (!release) throw new Error(t("pluginsPending.restartRequired"));
        const steps = new ApplySteps(
          (next) => {
            phases = next;
            report(next);
          },
          { write: t("aiAccess.client.write"), check: t("aiAccess.client.check") },
          restart ? phases : undefined,
          undefined,
          [...PLANS["ai.client.install"]],
        );
        try {
          await applyWithSteps(
            {
              resume: restart,
              change: async (signal) => {
                try {
                  window.sessionStorage.setItem(CLIENT_INSTALL_PENDING, "pending");
                } catch {
                  throw new Error(t("plugins.receiptStorageUnavailable"));
                }
                const answer = await unwrap(
                  api.POST("/system/plugin/add-extra", { params: { query: { extra } }, signal }),
                );
                if (
                  answer?.extra !== extra ||
                  typeof answer.done !== "boolean" ||
                  typeof answer.restart_required !== "boolean"
                )
                  throw new Error(t("plugins.notCompleted"));
                // An older server can return a receipt-only no-op before this process has loaded the client.
                return true;
              },
              options: {
                onObservation: (observation) => {
                  retainPluginRestart(observation);
                  restart = observation;
                },
                onAcknowledged: acknowledgePluginRestart,
                preserveTimeout: 10 * 60_000,
              },
            },
            steps,
          );
          completePluginRestart();
          window.sessionStorage.removeItem(CLIENT_INSTALL_PENDING);
          steps.mark("reload", "skipped");
          steps.mark("check", "running");
          await client.invalidateQueries({ queryKey: ["ai"] });
          steps.mark("check", "done");
        } catch (error) {
          if (restart && error instanceof RestartApplyError && error.kind === "stale") {
            forgetPluginRestart();
            restart = undefined;
          }
          // Once the install answered, a retry can observe its recorded restart without replaying either POST.
          if (restart) throw error;
          if (!clientInstallUncertain()) throw error;
          clientInstallError = error instanceof Error ? error.message : String(error);
          throw clientInstallRecovery(clientInstallError);
        } finally {
          steps.stop();
          release();
        }
      },
    },
    clientInstallUncertain() ? clientInstallRecovery(clientInstallError ?? undefined) : undefined,
  );
}

/** A sign-in is the person's own, in their browser: hz never types credentials; it checks again when asked. */
function SignInSetup({ option }: { option: SchemaAccessOption }) {
  const access = useAiAccess();
  const current = access.data?.options.find((item) => item.name === option.name) ?? option;
  return (
    <>
      <p className="text-sm">{t("aiAccess.signIn.step", { title: option.title })}</p>
      <p data-status-line="" className="text-ui-sm text-muted-foreground">
        {statusLine(current)}
      </p>
      {current.fix && (
        <details className="text-ui-sm text-muted-foreground">
          <summary className="cursor-pointer select-none">{t("aiAccess.details")}</summary>
          <p className="mt-1">{t("aiAccess.signIn.details")}</p>
          <CopyCommand command={current.fix} />
        </details>
      )}
      <PaneActions align="end" words="all">
        <PaneAction
          key="again"
          icon={RotateCw}
          type="button"
          disabled={access.isFetching}
          onClick={() => access.refetch()}
        >
          {t("lifecycle.checkAgain")}
        </PaneAction>
      </PaneActions>
    </>
  );
}
