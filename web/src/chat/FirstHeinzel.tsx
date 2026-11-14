// The first start when every chat is with a Heinzel (flag `one_chat`, canvas board 1, left frame): with no Heinzel yet
// the chat asks for one name and starts. The request id is kept until hz confirms, so a lost answer recovers the same
// Heinzel instead of making a second one. The look editor and Surprise me come later (boards 17 and 17b).
import { useQueryClient } from "@tanstack/react-query";
import { PanelRightClose } from "lucide-react";
import { type FormEvent, useId, useRef, useState } from "react";
import { api, problem } from "@/api/client";
import { HeinzelAvatar } from "@/components/HeinzelAvatar";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n";
import { rememberHeinzel } from "./defaultHeinzel";

const REQUEST = "hz.chat.first-heinzel";
const SESSION = /^hz-[0-9A-HJKMNP-TV-Z]{26}$/;

/** The request id for this name: kept until hz confirms, so a retry with the same name recovers the same Heinzel. */
function requestId(name: string): string {
  try {
    const stored: unknown = JSON.parse(localStorage.getItem(REQUEST) ?? "null");
    if (
      stored &&
      typeof stored === "object" &&
      "id" in stored &&
      "name" in stored &&
      typeof stored.id === "string" &&
      stored.name === name
    )
      return stored.id;
    const fresh = crypto.randomUUID();
    localStorage.setItem(REQUEST, JSON.stringify({ id: fresh, name }));
    return fresh;
  } catch {
    return crypto.randomUUID();
  }
}

/** Forget the first-start request: hz confirmed it, or the list already shows a Heinzel. */
export function settleFirstHeinzel() {
  try {
    localStorage.removeItem(REQUEST);
  } catch {
    // Nothing to forget without storage.
  }
}

export function FirstHeinzel({ onCreated, onClose }: { onCreated: (sessionId: string) => void; onClose: () => void }) {
  const client = useQueryClient();
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const creating = useRef(false);
  const field = useId();
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const chosen = name.trim() || t("oneChat.namePlaceholder");
    if (creating.current) return;
    creating.current = true;
    setBusy(true);
    setFailed(false);
    try {
      const { data, error, response } = await api.POST("/heinzel/new-chat", {
        body: { request_id: requestId(chosen), name: chosen },
      });
      if (!response.ok || !data) throw problem(response.status, error);
      if (!SESSION.test(data.session_id)) throw new Error("Invalid creation acknowledgement");
      settleFirstHeinzel();
      rememberHeinzel(data.session_id);
      client.setQueryData(["heinzel", "session", data.session_id], data);
      await client.invalidateQueries({ queryKey: ["heinzel", "sessions"] });
      onCreated(data.session_id);
    } catch {
      setFailed(true);
      // A lost answer may still have made the Heinzel: the panel opens it as soon as the list shows it.
      void client.invalidateQueries({ queryKey: ["heinzel", "sessions"] });
    } finally {
      creating.current = false;
      setBusy(false);
    }
  };
  return (
    <div className="flex h-full flex-col">
      <div className="chat-panel-header flex h-12 shrink-0 items-center justify-end border-b border-border px-2">
        <Button
          type="button"
          variant="ghost"
          size="icon"
          data-pane-action
          onClick={onClose}
          aria-label={t("chat.collapse")}
          title={t("chat.collapse")}
        >
          <PanelRightClose aria-hidden />
        </Button>
      </div>
      <form
        onSubmit={(event) => void submit(event)}
        className="flex min-h-0 flex-1 flex-col items-center justify-center gap-4 overflow-y-auto p-6 text-center"
      >
        <HeinzelAvatar className="h-36 w-24" />
        <h2 className="text-xl font-semibold">{t("oneChat.meet")}</h2>
        <p className="text-muted-foreground">{t("oneChat.motto")}</p>
        <div className="flex w-full max-w-72 flex-col gap-2 text-left">
          <label htmlFor={field} className="text-sm font-medium">
            {t("oneChat.nameLabel")}
          </label>
          <input
            id={field}
            value={name}
            maxLength={100}
            autoComplete="off"
            placeholder={t("oneChat.namePlaceholder")}
            onChange={(event) => setName(event.target.value)}
            className="h-11 rounded-lg border border-border bg-card px-3"
          />
          <Button type="submit" disabled={busy} className="h-11">
            {t("oneChat.start")}
          </Button>
        </div>
        {failed && (
          <p role="alert" className="text-sm">
            {t("oneChat.createError")}
          </p>
        )}
        <p className="text-sm text-muted-foreground">{t("oneChat.later")}</p>
      </form>
    </div>
  );
}
