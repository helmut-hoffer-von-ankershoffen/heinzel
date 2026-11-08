// Owner 2026-11-08: the app never sends a person to a terminal. A choice made in the app applies by itself (a picked
// agent runner is taken up by the running hz), and what needs a restart or an install runs as an operation with its
// progress. A command may appear only where a person asked for one: a Details disclosure, a copy button, the terminal
// panel itself. This test reads the app's copy (i18n.ts) for instructions to go to a terminal, run a command or restart
// hz by hand. The allowlist only shrinks; each entry says why it is still there.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const SOURCE = readFileSync(join(__dirname, "..", "i18n.ts"), "utf8");

/** A backticked hz command, an installer command, "in a terminal", or "restart `hz …`" (a button saying Restart is fine). */
const TERMINAL =
  /`hz [a-z]|uv tool|\b(in|into) (a|the) terminal\b|\bhz (system|ui|ai|daemon|org|update) [a-z-]+ [a-z-]+|restart `hz/i;

const ALLOWED: Record<string, string> = {
  "durable.daemonCadence": "expert diagnostic of the daemon cadence; follow-up: a Fix button that reinstalls it",
  "lifecycle.unsupported": "a foreground hz ui serve cannot restart itself; only shown when no supervisor owns it",
  "lifecycle.timeout": "the last resort when hz does not come back after an automatic restart",
  "onboarding.confirm": "the onboarding confirms a command the person chose to run in the app's terminal panel",
  "onboarding.notifications.notLoaded": "follow-up: replace with a Check again button",
  "help.commandsIntro": "the help page lists commands on purpose",
  "help.stuckBody": "the help page's last resort; follow-up: a Run doctor button",
  "token.missingTitle": "no session yet: the app cannot run anything for a page without its token",
  "token.missing": "no session yet: the app cannot run anything for a page without its token",
  "token.refused": "no session yet: the app cannot run anything for a page without its token",
  "splash.unreachableCommand": "hz is not running, so no button can start it",
  "splash.pasteLabel": "hz is not running in this page yet; it names the link the server printed",
  "splash.pasteProblem": "hz is not running in this page yet; it names the link the server printed",
  "splash.tokenCommand": "hz is not running in this page yet, so no button can start it",
  "notify.confirmTitle": "the terminal panel runs a command the person picked",
  "notify.confirmRun": "the terminal panel runs a command the person picked",
  "run.label": "the terminal panel's own controls",
  "run.dialogTitle": "the terminal panel's own controls",
  "run.review": "the terminal panel's own controls",
  "run.aria": "the terminal panel's own controls",
  "device.paired": "names the server whose link paired this browser",
  "device.forgetHint": "names the server whose link pairs this browser again",
};

function messages(): [string, string][] {
  return [...SOURCE.matchAll(/"([a-zA-Z0-9_.]+)":\s*("(?:[^"\\]|\\.)*"|`[^`]*`)/g)].map((match) => [
    match[1] ?? "",
    match[2] ?? "",
  ]);
}

describe("terminal instructions in UI copy", () => {
  it("sends nobody to a terminal outside the allowlist", () => {
    const found = messages()
      .filter(([key, value]) => TERMINAL.test(value) && !(key in ALLOWED))
      .map(([key]) => key);
    expect(found).toEqual([]);
  });

  it("keeps the allowlist to messages that still need it, so it only shrinks", () => {
    const hits = new Set(
      messages()
        .filter(([, value]) => TERMINAL.test(value))
        .map(([key]) => key),
    );
    expect(Object.keys(ALLOWED).filter((key) => !hits.has(key))).toEqual([]);
  });

  it("never asks for a restart of hz ui serve to apply a choice", () => {
    expect(SOURCE).not.toMatch(/restart `?hz ui serve`? or the MCP server/);
  });
});
