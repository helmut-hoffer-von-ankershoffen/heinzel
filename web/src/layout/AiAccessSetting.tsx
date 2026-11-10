// `hz ai access use` as one visible choice next to the theme (ADR 0057): one card per option with its title, a status
// dot and one status line, an "In use" marker on the chosen one, a Set up button where hz can set it up here, and a
// test button that runs `hz ai access test` only when pressed, since a live check may cost a little. Picking an option
// applies at once: the running hz takes up the agent runner before its next call, so nothing asks for a restart.
// Commands are for people who want them: only inside each card's Details.
import { useQueryClient } from "@tanstack/react-query";
import { Download, FlaskConical, RotateCw, Settings2, Trash2, X } from "lucide-react";
import { type ReactNode, useId, useState } from "react";
import type { SchemaAccessOption, SchemaAccessTestReport, SchemaExtraChange } from "@/api/client";
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
import { cn } from "@/lib/utils";
import { type ApplyPhase, restartAndApply } from "@/lifecycle/controller";

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
            onSetUp={() => setSetting(option)}
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
        onKeyDown={(event) => event.key === "Escape" && onClose()}
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
      {option.setup === "client" ? (
        <ClientSetup option={option} onClose={onClose} />
      ) : option.setup === "sign-in" ? (
        <SignInSetup option={option} />
      ) : (
        <FormSetup option={option} />
      )}
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

const PHASE: Record<ApplyPhase, MessageKey> = {
  checking: "lifecycle.phase.checking",
  preserving: "lifecycle.phase.preserving",
  restarting: "lifecycle.phase.restarting",
  reconnecting: "lifecycle.phase.reconnecting",
  verifying: "lifecycle.phase.verifying",
  applied: "lifecycle.phase.applied",
};

/** Adding an optional client of hz itself: confirm, install it through hz's own pinned path, restart, reload, check. */
function ClientSetup({ option, onClose }: { option: SchemaAccessOption; onClose: () => void }) {
  const client = useQueryClient();
  const [phase, setPhase] = useState<ApplyPhase | null>(null);
  const [error, setError] = useState<string | null>(null);
  const extra = option.client ?? "";
  const run = async () => {
    setError(null);
    try {
      await restartAndApply(
        {
          confirmed: true,
          // The install runs before the restart, so the restarted hz loads the new client.
          preserve: async () => {
            const result = await unwrap(
              api.POST("/system/plugin/add-extra", {
                params: { query: { extra: extra as SchemaExtraChange["extra"] } },
              }),
            );
            return result.restart_required;
          },
        },
        { onPhase: setPhase, preserveTimeout: 10 * 60_000 },
      );
      // A concurrent install can make this request a successful no-op, with no restart phases emitted.
      setPhase("applied");
      await client.invalidateQueries({ queryKey: ["ai"] });
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : String(failure));
    }
  };
  const running = phase !== null && phase !== "applied" && error === null;
  return (
    <>
      <p className="text-sm">{t("aiAccess.client.confirm", { needs: option.needs })}</p>
      <p className="text-ui-sm text-muted-foreground">{t("aiAccess.client.why")}</p>
      {phase && (
        <p role="status" className={cn("text-sm", phase === "applied" ? "text-ok" : "text-muted-foreground")}>
          {t(PHASE[phase])}
        </p>
      )}
      {error && <p className="text-sm text-bad">{error}</p>}
      <PaneActions align="end" words="all">
        <PaneAction key="cancel" icon={X} type="button" disabled={running} onClick={onClose}>
          {t("lifecycle.cancel")}
        </PaneAction>
        <PaneAction
          key="add"
          icon={Download}
          primary
          type="button"
          disabled={running || phase === "applied" || !extra}
          onClick={() => void run()}
        >
          {t("aiAccess.client.add")}
        </PaneAction>
      </PaneActions>
    </>
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
