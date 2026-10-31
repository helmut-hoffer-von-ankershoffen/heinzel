// The one place a button may hand an `hz` command to the terminal (U3): never as a side effect of a click that says
// something else, and never for what the app can do itself. A command whose operation the web head has runs in the app
// with a confirmation and its real steps; any other shows a button that says "Run in terminal", pressed by the person.
import { Play } from "lucide-react";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n";
import { appCallFor } from "@/lib/operationCalls";
import { requestOperation } from "@/lib/operationRun";
import { runInTerminal } from "@/lib/terminal";

/** Do what the person asked with `command`: the app's own operation when there is one, else the terminal. */
export function runCommand(command: string): void {
  const call = appCallFor(command);
  if (call) requestOperation(call);
  else runInTerminal(command);
}

/** The labeled button beside a command: "Run it here" for an operation the app has, "Run in terminal" otherwise. */
export function RunCommandButton({ command }: Readonly<{ command: string }>) {
  const here = appCallFor(command) !== undefined;
  return (
    <Button
      variant="ghost"
      size="sm"
      className="h-6 px-1.5"
      onClick={() => runCommand(command)}
      aria-label={here ? t("run.hereAria", { command }) : t("run.aria", { command })}
      title={here ? t("run.here") : t("run.label")}
    >
      <Play aria-hidden />
      {!here && <span className="text-xs">{t("run.terminal")}</span>}
    </Button>
  );
}
