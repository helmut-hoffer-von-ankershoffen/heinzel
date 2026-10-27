// A durable personal session has a different id and service from the legacy runner chat.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { api, problem } from "@/api/client";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n";

async function result<T>(call: Promise<{ data?: T; error?: unknown; response: Response }>): Promise<T> {
  const { data, error, response } = await call;
  if (response.ok && data !== undefined) return data;
  throw problem(response.status, error);
}

export function DurableSession({ sessionId, onClose }: { sessionId: string; onClose: () => void }) {
  const client = useQueryClient();
  const [text, setText] = useState("");
  const [pending, setPending] = useState<{ request_id: string; message: string } | null>(null);
  const [error, setError] = useState(false);
  const [busy, setBusy] = useState(false);
  const running = useRef(false);
  const query = useQuery({
    queryKey: ["heinzel", "session", sessionId],
    queryFn: () => result(api.GET("/heinzel/session", { params: { query: { session_id: sessionId } } })),
    retry: false,
  });

  const send = async () => {
    if (running.current || (!pending && !text.trim())) return;
    running.current = true;
    const request = pending ?? { request_id: crypto.randomUUID(), message: text };
    setPending(request);
    setBusy(true);
    setError(false);
    try {
      const accepted = await result(
        api.POST("/heinzel/chat", { body: { ...request, session_id: sessionId, when_busy: "follow_up" } }),
      );
      if (accepted.session_id !== sessionId || accepted.request_id !== request.request_id)
        throw new Error("Invalid acknowledgement");
      setPending(null);
      setText("");
      await client.invalidateQueries({ queryKey: ["heinzel", "session", sessionId] });
    } catch {
      setError(true);
    } finally {
      running.current = false;
      setBusy(false);
    }
  };

  return (
    <div className="flex h-full flex-col">
      <div className="flex h-12 shrink-0 items-center justify-between gap-2 border-b border-border px-3">
        <h2 className="min-w-0 truncate font-medium">{query.data?.name ?? t("durable.title")}</h2>
        <Button type="button" variant="ghost" size="sm" disabled={busy} onClick={onClose}>
          {t("durable.close")}
        </Button>
      </div>
      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-3">
        {query.isPending && <p role="status">{t("state.loading")}</p>}
        {query.isError && <p role="alert">{t("durable.unavailable")}</p>}
        {query.data?.messages?.map((message) => (
          <p key={`${message.seq}-${message.id}`} className="whitespace-pre-wrap text-sm">
            <strong>{message.role}:</strong> {message.text}
          </p>
        ))}
      </div>
      <form
        className="space-y-2 border-t border-border p-3"
        onSubmit={(event) => {
          event.preventDefault();
          void send();
        }}
      >
        <p className="text-xs text-muted-foreground">{t("durable.queued")}</p>
        <label className="block space-y-1 text-sm">
          <span>{t("durable.message")}</span>
          <textarea
            className="w-full rounded border border-border bg-background p-2"
            value={text}
            disabled={busy || pending !== null || !query.data}
            onChange={(event) => setText(event.target.value)}
          />
        </label>
        {error && <p role="alert">{t("durable.failed")}</p>}
        <Button type="submit" disabled={busy || !query.data || (!pending && !text.trim())}>
          {pending ? t("durable.retry") : t("durable.send")}
        </Button>
      </form>
    </div>
  );
}
