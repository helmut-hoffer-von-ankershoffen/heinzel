// The writes a setup button starts in the app, as the call the server would describe (`system.ui` builds the same ones
// for status items). A button that needs one of these asks `requestOperation`; it never types a command (U3).
import type { SchemaOperationCall } from "@/api/client";

const daemon = (action: "install" | "uninstall", summary: string): SchemaOperationCall => ({
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
