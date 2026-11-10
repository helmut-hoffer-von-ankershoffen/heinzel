// Pane microcopy standard from design/README.md (research/pane-microcopy-2026-11-08.md):
// - a sentence in a pane, card or popover is at most ~60 characters; longer content belongs in Details or a tooltip;
// - no internal state words reach the person (requested, applied, verified, owning, ...);
// - a control has at most one status line.
// ALLOW only shrinks: every entry needs a reason, and an entry whose string now passes must be deleted.
import { describe, expect, it } from "vitest";
import { en } from "@/i18n";

const MAX = 60;
const PLACEHOLDER = 12;
const PANE =
  /^(?:(contrib\.toggle|daemon|lifecycle|durable|pluginsPending|updates|autoUpdate|notify|plugins\.operation|op)\.|calendar\.(?:worker\w+|remindersOff|status\w*|reminderStatusFailed|loading|unavailable)$)/;
const BANNED = /\b(requested|applied|verified|owning|apply|intent|mapping|origin|handler|instance|readiness)\b/i;

const ALLOW: Record<string, string> = {
  "durable.failed": "sweep backlog: durable and operation copy, rewrite when the pane is next touched",
  "durable.preservationFailed": "sweep backlog: durable and operation copy, rewrite when the pane is next touched",
  "durable.draftUnreadable": "sweep backlog: durable and operation copy, rewrite when the pane is next touched",
  "durable.scheduleEmpty": "sweep backlog: durable and operation copy, rewrite when the pane is next touched",
  "durable.daemonAbsent": "sweep backlog: durable and operation copy, rewrite when the pane is next touched",
  "durable.daemonPaused": "sweep backlog: durable and operation copy, rewrite when the pane is next touched",
  "durable.daemonCadence": "sweep backlog: durable and operation copy, rewrite when the pane is next touched",
  "durable.daemonVersionNeedsCheck": "sweep backlog: durable and operation copy, rewrite when the pane is next touched",
  "durable.daemonNeedsCheck": "sweep backlog: durable and operation copy, rewrite when the pane is next touched",
  "durable.daemonStatusUnavailable": "sweep backlog: durable and operation copy, rewrite when the pane is next touched",
  "durable.scheduleIntentPrepared": "sweep backlog: durable and operation copy, rewrite when the pane is next touched",
  "durable.scheduleReviewStale": "sweep backlog: durable and operation copy, rewrite when the pane is next touched",
  "durable.scheduleReviewFailed": "sweep backlog: durable and operation copy, rewrite when the pane is next touched",
  "durable.scheduleApprovalInterrupted":
    "sweep backlog: durable and operation copy, rewrite when the pane is next touched",
  "durable.scheduleOutcomeUnknown": "sweep backlog: durable and operation copy, rewrite when the pane is next touched",
  "durable.scheduleFailed": "sweep backlog: durable and operation copy, rewrite when the pane is next touched",
  "durable.agentUnavailable": "sweep backlog: durable and operation copy, rewrite when the pane is next touched",
  "durable.proposalInterrupted": "sweep backlog: durable and operation copy, rewrite when the pane is next touched",
  "durable.proposalOutcomeUnknown": "sweep backlog: durable and operation copy, rewrite when the pane is next touched",
  "durable.createFailed": "sweep backlog: durable and operation copy, rewrite when the pane is next touched",
  "daemon.mismatch": "sweep backlog row in research/pane-microcopy-2026-11-08.md",
  "daemon.persistenceHint": "sweep backlog row in research/pane-microcopy-2026-11-08.md",
  "lifecycle.action": "sweep backlog row in research/pane-microcopy-2026-11-08.md",
  "lifecycle.targetAction": "sweep backlog row in research/pane-microcopy-2026-11-08.md",
  "lifecycle.question": "sweep backlog row in research/pane-microcopy-2026-11-08.md",
  "lifecycle.phase.applied": "sweep backlog row in research/pane-microcopy-2026-11-08.md",
  "lifecycle.confirmFirst": "sweep backlog row in research/pane-microcopy-2026-11-08.md",
  "lifecycle.unsupported": "sweep backlog row in research/pane-microcopy-2026-11-08.md",
  "lifecycle.timeout": "sweep backlog row in research/pane-microcopy-2026-11-08.md",
  "lifecycle.wrongVersion": "sweep backlog row in research/pane-microcopy-2026-11-08.md",
  "lifecycle.wrongSettings": "sweep backlog row in research/pane-microcopy-2026-11-08.md",
  "lifecycle.stopped": "sweep backlog row in research/pane-microcopy-2026-11-08.md",
  "lifecycle.missingIdentity": "sweep backlog row in research/pane-microcopy-2026-11-08.md",
  "lifecycle.restartOutcomeUnknown": "sweep backlog row in research/pane-microcopy-2026-11-08.md",
  "lifecycle.notRestarted": "sweep backlog row in research/pane-microcopy-2026-11-08.md",
  "lifecycle.beforeChangeTimeout": "sweep backlog row in research/pane-microcopy-2026-11-08.md",
  "pluginsPending.intro": "sweep backlog row in research/pane-microcopy-2026-11-08.md",
  "pluginsPending.theme": "sweep backlog row in research/pane-microcopy-2026-11-08.md",
  "op.noStop": "sweep backlog: durable and operation copy, rewrite when the pane is next touched",
  "op.failedNext": "sweep backlog: durable and operation copy, rewrite when the pane is next touched",
  "op.lost": "sweep backlog: durable and operation copy, rewrite when the pane is next touched",
  "op.daemon.install.consent": "sweep backlog: durable and operation copy, rewrite when the pane is next touched",
  "op.daemon.uninstall.consent": "sweep backlog: durable and operation copy, rewrite when the pane is next touched",
  "notify.empty": "sweep backlog row in research/pane-microcopy-2026-11-08.md",
  "notify.result.insideOnly": "sweep backlog row in research/pane-microcopy-2026-11-08.md",
  "notify.browser.failed": "sweep backlog row in research/pane-microcopy-2026-11-08.md",
  "notify.browser.off": "sweep backlog row in research/pane-microcopy-2026-11-08.md",
  "notify.browser.denied": "sweep backlog row in research/pane-microcopy-2026-11-08.md",
  "notify.state.denied": "sweep backlog row in research/pane-microcopy-2026-11-08.md",
  "notify.state.unsupported": "sweep backlog row in research/pane-microcopy-2026-11-08.md",
  "notify.sound.help": "sweep backlog row in research/pane-microcopy-2026-11-08.md",
  "notify.sound.gesture": "sweep backlog row in research/pane-microcopy-2026-11-08.md",
  "updates.bySource": "sweep backlog row in research/pane-microcopy-2026-11-08.md",
  "updates.failed": "sweep backlog row in research/pane-microcopy-2026-11-08.md",
  "autoUpdate.how": "sweep backlog row in research/pane-microcopy-2026-11-08.md",
  "autoUpdate.reloadLater": "sweep backlog row in research/pane-microcopy-2026-11-08.md",
};

const length = (value: string) => value.replace(/\{[^}]*\}/g, "x".repeat(PLACEHOLDER)).length;

function violations(catalogue: Record<string, string> = en): string[] {
  return Object.entries(catalogue)
    .filter(([key, value]) => (PANE.test(key) && length(value) > MAX) || BANNED.test(value))
    .map(([key]) => key);
}

describe("pane copy", () => {
  it("keeps pane strings short and free of internal state words, apart from the shrinking allowlist", () => {
    expect(violations().filter((key) => !(key in ALLOW))).toEqual([]);
  });

  it("drops an allowlist entry as soon as its string passes", () => {
    const found = new Set(violations());
    expect(Object.keys(ALLOW).filter((key) => !found.has(key))).toEqual([]);
  });

  it("gives every allowlist entry a reason", () => {
    expect(Object.entries(ALLOW).filter(([, reason]) => reason.trim().length < 10)).toEqual([]);
  });

  it("detects internal words and long calendar status copy regardless of source formatting", () => {
    expect(
      violations({
        "other.message": "Another instance is running.",
        "calendar.workerRemote": "x".repeat(MAX + 1),
        "calendar.workerNeeded": "x".repeat(MAX),
        "calendar.installSummary": "x".repeat(MAX + 1),
      }),
    ).toEqual(["other.message", "calendar.workerRemote"]);
  });
});
