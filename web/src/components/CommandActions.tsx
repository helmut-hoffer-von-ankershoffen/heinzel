// The one place a button acts on an `hz` command (U3). The app never types a command into the terminal for the person:
// a command whose operation the web head has runs in the app with a confirmation and its real steps; a command that sets
// something up opens that module's Settings page; any other shows the command to copy.
import { Play, Settings } from "lucide-react";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n";
import { showCommand } from "@/lib/commandIntent";
import { settingsSectionFor } from "@/lib/commandRoute";
import { appCallFor } from "@/lib/operationCalls";
import { requestOperation } from "@/lib/operationRun";

/** Whether the app does `command` itself, in a dialog with its steps or on a Settings page. */
export const runsInApp = (command: string): boolean =>
  appCallFor(command) !== undefined || settingsSectionFor(command) !== undefined;

/** Do what the person asked with `command`: the app's own operation, its Settings page, or the command to copy. */
export function runCommand(command: string): void {
  const call = appCallFor(command);
  if (call) requestOperation(call);
  else showCommand(command);
}

/** The labeled button beside a command; only when the app can do it, otherwise the copy button is all there is. */
export function RunCommandButton({ command }: Readonly<{ command: string }>) {
  const settings = appCallFor(command) === undefined && settingsSectionFor(command) !== undefined;
  if (!runsInApp(command)) return null;
  return (
    <Button
      variant="ghost"
      size="sm"
      className="h-6 px-1.5"
      onClick={() => runCommand(command)}
      aria-label={settings ? t("run.settingsAria", { command }) : t("run.hereAria", { command })}
      title={settings ? t("run.settings") : t("run.here")}
    >
      {settings ? <Settings aria-hidden /> : <Play aria-hidden />}
    </Button>
  );
}
