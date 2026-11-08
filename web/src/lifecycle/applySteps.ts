// The steps of a change that restarts hz, as one list for the operation dialog (ADR 0056): saving open work, the
// change itself (the steps its server reports), restarting, waiting for hz to come back, reloading the page and the
// final check. Each is told when it really starts and ends; nothing is timed or invented.
import { type MessageKey, t } from "@/i18n";
import type { Phase, PhaseState } from "@/lib/operationRun";
import { saveOpenWork } from "@/updates/work";
import {
  type ApplyOptions,
  type ApplyPhase,
  type ExpectedSetting,
  type Lifecycle,
  RestartApplyError,
  type RestartObservation,
  restartAndApply,
  resumeRestartAndApply,
} from "./controller";

export type ApplyStepId = "preserve" | "write" | "restart" | "wait" | "reload" | "check";
const PLAN: ApplyStepId[] = ["preserve", "write", "restart", "wait", "reload", "check"];
/** How long hz may be away before waiting needs the person's attention. */
export const WAIT_LIMIT_MS = 60_000;

/** A step as the dialog keeps it: its state, the one-line detail and when it ran, so finished steps show a time. */
export type TimedPhase = Phase & { startedAt?: number; endedAt?: number };

/** The fixed plan of a change that restarts hz, each step told when it really starts and ends (design 3). */
export class ApplySteps {
  private steps: Record<ApplyStepId, TimedPhase>;
  private notes: string[] = [];
  private ticker: ReturnType<typeof setInterval> | null = null;
  constructor(
    private readonly report: (phases: Phase[]) => void,
    labels: Partial<Record<ApplyStepId, string>>,
    restored?: Phase[],
    private readonly now: () => number = Date.now,
  ) {
    const step = (id: ApplyStepId): TimedPhase => ({
      id,
      label: labels[id] ?? t(`applySteps.${id}` as MessageKey),
      state: "pending",
    });
    this.steps = Object.fromEntries(PLAN.map((id) => [id, step(id)])) as Record<ApplyStepId, TimedPhase>;
    for (const phase of restored ?? []) if (phase.id in this.steps) this.steps[phase.id as ApplyStepId] = phase;
  }
  /** Every step in plan order, as the dialog shows them. */
  list(): TimedPhase[] {
    return PLAN.map((id) => this.steps[id]);
  }
  state(id: ApplyStepId): PhaseState {
    return this.steps[id].state;
  }
  private tell() {
    this.report(this.list());
  }
  /**
   * A step the change's server reported: its `install` is the write itself; its checks, its record and each plugin
   * it needs become detail lines of the write, since the plan is fixed before it starts.
   */
  readonly heard = (phase: Phase) => {
    if (phase.id === "install") {
      if (phase.state === "running" || phase.state === "failed") this.mark("write", phase.state, phase.detail);
      return;
    }
    if (phase.state === "failed") {
      this.mark("write", "failed", phase.detail ?? phase.label);
      return;
    }
    if (phase.state !== "done") return;
    const note = phase.id.startsWith("needs:")
      ? phase.detail
        ? `${phase.label}: ${phase.detail}`
        : phase.label
      : null;
    if (phase.id === "verify") this.notes.push(t("applySteps.checked"));
    else if (note) this.notes.push(note);
    this.steps.write = { ...this.steps.write, detail: this.notes.join(" · ") };
    this.tell();
  };
  mark(id: ApplyStepId, state: PhaseState, detail?: string) {
    const previous = this.steps[id];
    const time = this.now();
    const { detail: _old, ...rest } = previous;
    const kept = detail ?? (id === "write" && this.notes.length ? this.notes.join(" · ") : undefined);
    this.steps[id] = {
      ...rest,
      state,
      ...(kept ? { detail: kept } : {}),
      ...(state === "running" ? { startedAt: previous.startedAt ?? time, endedAt: undefined } : {}),
      ...(state !== "running" && state !== "pending" && previous.startedAt !== undefined ? { endedAt: time } : {}),
    };
    this.watch();
    this.tell();
  }
  /** While a step runs the dialog re-reads the list once a second, so its live time moves. */
  private watch() {
    const running = PLAN.some((id) => this.steps[id].state === "running");
    if (running && !this.ticker) this.ticker = setInterval(() => this.tell(), 1000);
    if (!running && this.ticker) {
      clearInterval(this.ticker);
      this.ticker = null;
    }
  }
  /** The restart lifecycle moved on: the step it left is done, the one it entered runs. */
  readonly onPhase = (phase: ApplyPhase) => {
    if (phase === "preserving" && this.steps.write.state === "pending") this.mark("write", "running");
    else if (phase === "restarting") {
      if (this.steps.write.state === "running") this.mark("write", "done");
      this.mark("restart", "running");
    } else if (phase === "reconnecting" || phase === "verifying") {
      if (this.steps.restart.state === "running") this.mark("restart", "done");
      if (this.steps.wait.state !== "running") this.mark("wait", "running");
    } else if (phase === "applied") this.mark("wait", "done");
  };
  /** The running step failed with `error`; a wait that ran out needs attention instead (design 2). */
  failed(error: unknown) {
    const message = error instanceof Error ? error.message : String(error);
    const running = PLAN.find((id) => this.steps[id].state === "running");
    if (!running) return;
    const attention = error instanceof RestartApplyError && (error.kind === "timeout" || error.kind === "aborted");
    this.mark(running, attention ? "warn" : "failed", attention ? t("applySteps.attention") : message);
  }
  /** The change took effect without replacing the server: the restart steps were not needed. */
  noRestart() {
    if (this.steps.write.state === "running") this.mark("write", "done");
    for (const id of ["restart", "wait", "reload"] as const)
      if (this.steps[id].state === "pending") this.mark(id, "skipped", t("applySteps.noRestart"));
  }
  stop() {
    if (this.ticker) clearInterval(this.ticker);
    this.ticker = null;
  }
}

/** An error the person decides about (hz has not come back yet): the dialog offers Keep waiting, not Try again. */
export class NeedsAttention extends Error {
  readonly attention = true;
}

export type ApplyRequest = {
  /** The confirmed change, run after open work is saved and before the restart; false: no restart is needed. */
  change: (signal: AbortSignal, heard: (phase: Phase) => void) => Promise<boolean | undefined>;
  version?: string;
  settings?: ExpectedSetting[];
  options?: ApplyOptions;
  /** An earlier attempt's restart to observe again instead of changing and restarting once more. */
  resume?: RestartObservation;
};

/** Save open work, make the change, restart hz and wait until it is back: every step told through `steps`. */
export async function applyWithSteps(request: ApplyRequest, steps: ApplySteps): Promise<Lifecycle> {
  const options: ApplyOptions = {
    timeout: WAIT_LIMIT_MS,
    ...request.options,
    onPhase: (phase) => {
      request.options?.onPhase?.(phase);
      steps.onPhase(phase);
    },
  };
  try {
    if (request.resume) {
      steps.mark("wait", "running");
      return await resumeRestartAndApply(request.resume, options);
    }
    steps.mark("preserve", "running");
    saveOpenWork();
    steps.mark("preserve", "done");
    let restarts = true;
    const running = await restartAndApply(
      {
        confirmed: true,
        version: request.version,
        settings: request.settings,
        preserve: async (signal) => {
          restarts = (await request.change(signal, steps.heard)) !== false;
          return restarts;
        },
      },
      options,
    );
    if (!restarts) steps.noRestart();
    return running;
  } catch (error) {
    steps.failed(error);
    if (error instanceof RestartApplyError && (error.kind === "timeout" || error.kind === "aborted"))
      throw new NeedsAttention(t("applySteps.notBack"));
    throw error;
  }
}
