// The writes a setup button starts in the app, as the call the server would describe (`system.ui` builds the same ones
// for status items). A button that needs one of these asks `requestOperation`; it never types a command (U3).
import type { SchemaOperationCall } from "@/api/client";

type DaemonAction = "install" | "uninstall" | "pause" | "resume" | "restart";

const daemon = (action: DaemonAction, summary: string): SchemaOperationCall => ({
  method: "POST",
  path: `/system/daemon/${action}`,
  query: {},
  body: null,
  command: `hz system daemon ${action}`,
  summary,
  read_only: false,
});

export const DAEMON_INSTALL = daemon(
  "install",
  "Install and load the launchd agent or systemd timer that ticks every few minutes.",
);
export const DAEMON_UNINSTALL = daemon("uninstall", "Unload and remove the daemon's unit; receipts and logs stay.");
export const DAEMON_PAUSE = daemon("pause", "Pause the daemon: ticks run no job until resumed.");
export const DAEMON_RESUME = daemon("resume", "Resume a paused daemon.");
export const DAEMON_RESTART = daemon("restart", "Restart the daemon so it runs with the installed hz.");

const IN_APP = new Map(
  [DAEMON_INSTALL, DAEMON_UNINSTALL, DAEMON_PAUSE, DAEMON_RESUME, DAEMON_RESTART].map((call) => [call.command, call]),
);

/** The call that does what `command` says inside the app, if the web head has that operation; else undefined. */
export const appCallFor = (command: string): SchemaOperationCall | undefined => IN_APP.get(command.trim());
