// The full confirmation the Heinzel's page opens before anything that widens what a Heinzel may do or retires it
// (FEASIBILITY 3): the whole item in words, one confirming button and one way out. A native modal <dialog>, as
// AboutDialog: the page behind it is inert, Tab stays inside, and Escape, the way out and a press on the backdrop close
// it. Focus starts on the way out (the safe choice) and returns to the button that opened it.

import { Check, X } from "lucide-react";
import { type ReactNode, type RefObject, useId, useLayoutEffect, useRef } from "react";
import { createPortal } from "react-dom";
import { PaneAction, PaneActions } from "@/components/PaneActions";

type Props = {
  title: string;
  children: ReactNode;
  confirm: string;
  cancel: string;
  /** Where focus goes when the dialog closes: the button that opened it, unless the page points it elsewhere. */
  opener: RefObject<HTMLElement | null>;
  busy?: boolean;
  /** The confirming button waits until the item it confirms is ready. */
  ready?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
};

export function ConfirmDialog({
  title,
  children,
  confirm,
  cancel,
  opener,
  busy = false,
  ready = true,
  onConfirm,
  onCancel,
}: Props) {
  const titleId = useId();
  const bodyId = useId();
  const dialog = useRef<HTMLDialogElement>(null);
  const safe = useRef<HTMLButtonElement>(null);
  useLayoutEffect(() => {
    const element = dialog.current;
    if (!element) return;
    if (!element.open) element.showModal();
    safe.current?.focus();
    // Read at closing time: the page may point it at what changed (the status line) once the action is done.
    return () => {
      if (element.open) element.close();
      opener.current?.focus();
    };
  }, [opener]);
  const leave = () => {
    if (!busy) onCancel();
  };
  return createPortal(
    <dialog
      ref={dialog}
      role="alertdialog"
      aria-modal="true"
      aria-labelledby={titleId}
      aria-describedby={bodyId}
      className="viewport-overlay fixed z-(--layer-modal) m-0 flex max-h-none max-w-none items-center justify-center border-0 bg-transparent p-4 text-foreground backdrop:bg-black/50"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) leave();
      }}
      onCancel={(event) => {
        event.preventDefault();
        leave();
      }}
      onKeyDown={(event) => {
        if (event.key === "Escape") {
          event.preventDefault();
          event.stopPropagation();
          leave();
          return;
        }
        if (event.key !== "Tab") return;
        const controls = [...event.currentTarget.querySelectorAll<HTMLElement>("button:not(:disabled)")];
        const first = controls[0];
        const last = controls.at(-1);
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last?.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first?.focus();
        }
      }}
    >
      <div className="flex w-[min(28rem,100%)] flex-col gap-3 rounded-xl border border-border bg-popover p-5 shadow-2xl">
        <h2 id={titleId} className="text-ui font-medium">
          {title}
        </h2>
        <div id={bodyId} className="flex flex-col gap-2 text-sm">
          {children}
        </div>
        <PaneActions align="end" words="all" className="mt-2">
          <PaneAction ref={safe} icon={X} disabled={busy} onClick={onCancel}>
            {cancel}
          </PaneAction>
          <PaneAction icon={Check} primary disabled={busy || !ready} onClick={onConfirm}>
            {confirm}
          </PaneAction>
        </PaneActions>
      </div>
    </dialog>,
    document.body,
  );
}
