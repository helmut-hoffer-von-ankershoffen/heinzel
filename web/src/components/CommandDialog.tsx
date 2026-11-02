import { createPortal } from "react-dom";
import { runCommand } from "@/components/CommandActions";
import { CopyCommand } from "@/components/common";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n";

/** What a contributed command button opens: the command to copy, and a button the person presses to run it. */
export function CommandDialog({ command, onClose }: Readonly<{ command: string; onClose: () => void }>) {
  return createPortal(
    <div
      role="alertdialog"
      aria-modal="true"
      aria-labelledby="command-dialog"
      onKeyDown={(event) => event.key === "Escape" && onClose()}
      className="confirmation-dialog fixed z-(--layer-modal) max-h-[calc(var(--hz-viewport-height,100dvh)-2rem)] w-[min(28rem,calc(var(--hz-viewport-width,100vw)-2rem))] overflow-y-auto rounded-xl border border-border bg-popover p-4 text-sm shadow-xl"
    >
      <p id="command-dialog" className="font-medium">
        {t("run.dialogTitle")}
      </p>
      <p className="mt-1 text-muted-foreground">{t("run.dialogBody")}</p>
      <div className="mt-2">
        <CopyCommand command={command} />
      </div>
      <div className="mt-3 flex justify-end gap-2">
        <Button size="sm" variant="outline" autoFocus onClick={onClose}>
          {t("op.close")}
        </Button>
        <Button
          size="sm"
          onClick={() => {
            runCommand(command);
            onClose();
          }}
        >
          {t("run.terminal")}
        </Button>
      </div>
    </div>,
    document.body,
  );
}
