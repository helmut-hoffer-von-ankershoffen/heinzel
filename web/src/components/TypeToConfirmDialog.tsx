// A confirmation for something that cannot be undone: the person types the exact name before the red button works
// (ADR 0148). One shared dialog, so every destructive flow asks the same way on a phone and on a desktop.
import { Trash2, X } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { PaneAction, PaneActions } from "@/components/PaneActions";
import { t } from "@/i18n";

/** Whether `typed` names `expected`, compared as Unicode NFC so a composed and a decomposed letter match. */
export function sameName(typed: string, expected: string): boolean {
  return typed.normalize("NFC") === expected.normalize("NFC");
}

export function TypeToConfirmDialog({
  title,
  lines,
  expected,
  action,
  error,
  onConfirm,
  onClose,
}: Readonly<{
  title: string;
  lines: readonly string[];
  expected: string;
  action: string;
  error: string | null;
  onConfirm: () => Promise<void>;
  onClose: () => void;
}>) {
  const heading = useId();
  const field = useId();
  const input = useRef<HTMLInputElement>(null);
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => input.current?.focus(), []);
  const ready = sameName(typed, expected) && !busy;
  const confirm = async () => {
    if (!ready) return;
    setBusy(true);
    try {
      await onConfirm();
    } finally {
      setBusy(false);
    }
  };
  return createPortal(
    <div
      role="alertdialog"
      aria-modal="true"
      aria-labelledby={heading}
      onKeyDown={(event) => {
        event.stopPropagation();
        if (event.key === "Escape" && !busy) onClose();
      }}
      className="confirmation-dialog fixed z-(--layer-modal) max-h-[calc(var(--hz-viewport-height,100dvh)-2rem)] w-[min(28rem,calc(var(--hz-viewport-width,100vw)-2rem))] overflow-y-auto rounded-xl border border-border bg-popover p-4 text-sm shadow-xl"
    >
      <p id={heading} className="text-base font-medium">
        {title}
      </p>
      {lines.map((line) => (
        <p key={line} className="mt-1 text-muted-foreground">
          {line}
        </p>
      ))}
      <form
        className="mt-3"
        onSubmit={(event) => {
          event.preventDefault();
          void confirm();
        }}
      >
        <label htmlFor={field} className="block font-medium">
          {t("confirm.typeName", { name: expected })}
        </label>
        <input
          id={field}
          ref={input}
          value={typed}
          autoComplete="off"
          autoCapitalize="off"
          autoCorrect="off"
          spellCheck={false}
          disabled={busy}
          onChange={(event) => setTyped(event.target.value)}
          className="mt-1 h-9 w-full rounded-md border border-border bg-background px-2 text-base pointer-coarse:h-11 sm:text-sm"
        />
        {error && (
          <p role="alert" className="mt-2 text-bad">
            {error}
          </p>
        )}
        <PaneActions align="end" words="all">
          <PaneAction key="cancel" icon={X} type="button" disabled={busy} onClick={onClose}>
            {t("contrib.cancel")}
          </PaneAction>
          <PaneAction key="go" icon={Trash2} type="submit" primary destructive disabled={!ready}>
            {action}
          </PaneAction>
        </PaneActions>
      </form>
    </div>,
    document.body,
  );
}
