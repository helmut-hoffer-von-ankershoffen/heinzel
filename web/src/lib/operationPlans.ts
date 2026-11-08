// The step plan of every kind of write that restarts hz (design 3), so a new kind is one table entry: the ordered
// steps the operation dialog shows from the start, and `useOperation` to ask for it and run it there.
import { useCallback } from "react";
import type { SchemaOperationCall } from "@/api/client";
import { type ApplyStepId, ApplySteps, applyWithSteps } from "@/lifecycle/applySteps";
import type { ApplyOptions } from "@/lifecycle/controller";
import { type Phase, requestDrivenOperation } from "./operationRun";

/** Every kind and its plan, in order. Each plan with `restart` has `wait` and `reload` after it and ends with `check`. */
export const PLANS = {
  "plugin.install": ["preserve", "write", "restart", "wait", "reload", "check"],
  "plugin.remove": ["preserve", "write", "restart", "wait", "reload", "check"],
  "plugin.enable": ["preserve", "write", "restart", "wait", "reload", "check"],
  "plugin.disable": ["preserve", "write", "restart", "wait", "reload", "check"],
  "restart.batch": ["preserve", "restart", "wait", "reload", "check"],
  "restart.web": ["preserve", "restart", "wait", "reload", "check"],
  "setting.apply": ["preserve", "write", "restart", "wait", "reload", "check"],
} as const satisfies Record<string, readonly ApplyStepId[]>;
export type OperationKind = keyof typeof PLANS;

export type OperationParams = {
  call: SchemaOperationCall;
  consent: string[];
  action: string;
  runningTitle: string;
  doneTitle: string;
  running: string;
  destructive?: boolean;
  /** The words of `write` and `check` for this subject, e.g. Installing heinzel-x. */
  labels?: Partial<Record<ApplyStepId, string>>;
  /** The kind's own write, run after open work is saved; false: no restart is needed. */
  change?: (signal: AbortSignal, heard: (phase: Phase) => void) => Promise<boolean | undefined>;
  /** The final check: throws with what the reloaded hz does not show. */
  check?: () => Promise<void>;
  /** Reload the page once hz is back; omitted, the plan's reload is skipped. */
  reload?: () => void;
  options?: ApplyOptions;
};

/** Ask for a `kind` write in the operation dialog and run its plan there once the person confirms it. */
export function useOperation(kind: OperationKind, params: OperationParams): () => void {
  return useCallback(() => {
    const plan = [...PLANS[kind]] as ApplyStepId[];
    requestDrivenOperation(params.call, {
      consent: params.consent,
      action: params.action,
      runningTitle: params.runningTitle,
      doneTitle: params.doneTitle,
      running: params.running,
      destructive: params.destructive,
      run: async (report) => {
        const steps = new ApplySteps(report, params.labels ?? {}, undefined, undefined, plan);
        await applyWithSteps({ change: params.change ?? (async () => true), options: params.options }, steps);
        if (plan.includes("reload") && params.reload && steps.state("restart") === "done") {
          steps.mark("reload", "running");
          params.reload();
          return;
        }
        if (steps.state("reload") === "pending") steps.mark("reload", "skipped");
        steps.mark("check", "running");
        await params.check?.();
        steps.mark("check", "done");
      },
    });
  }, [kind, params]);
}
