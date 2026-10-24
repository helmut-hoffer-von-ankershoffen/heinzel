// `hz mail`: unread mail (the important first) and one conversation at a time, as plain text. Nothing here renders
// HTML or loads an image: a message is text in a <pre>, so no remote content or tracking pixel reaches the browser.
// Archiving, a reply draft and sending ask the server what they would do first and run only after Confirm; a send
// shows the whole draft with every recipient and sends only that version (`seen`).
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useSearchParams } from "react-router";
import { api, type SchemaMailChange, type SchemaMailMessage, unwrap } from "@/api/client";
import { Loaded, Page } from "@/components/common";
import { Button } from "@/components/ui/button";
import { Badge, Card } from "@/components/ui/card";
import { t } from "@/i18n";
import { cn } from "@/lib/utils";

const when = new Intl.DateTimeFormat("en", { dateStyle: "medium", timeStyle: "short" });
/** The longest reply typed here: it travels in the request's query, which servers cap at a few kilobytes. */
export const MAX_REPLY = 8000;

/** A change the server described, and how to carry it out once the person confirms. */
type Step = { change: SchemaMailChange; run: () => Promise<SchemaMailChange> };
/** Ask the server what something would do; the answer becomes the step to confirm. */
type Ask = (preview: () => Promise<Step>) => void;

const sender = (message: SchemaMailMessage) => message.sender.name || message.sender.email || "";

function Confirmation({
  step,
  busy,
  onConfirm,
  onCancel,
}: {
  step: Step;
  busy: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <Card role="alertdialog" aria-label={t("mail.confirmTitle")}>
      <pre className="mb-3 font-sans whitespace-pre-wrap">{step.change.summary}</pre>
      <div className="flex gap-2">
        <Button onClick={onConfirm} disabled={busy} autoFocus>
          {t("mail.confirm")}
        </Button>
        <Button variant="outline" onClick={onCancel} disabled={busy}>
          {t("mail.cancel")}
        </Button>
      </div>
    </Card>
  );
}

function Conversation({ thread, busy, onAsk }: { thread: string; busy: boolean; onAsk: Ask }) {
  const found = useQuery({
    queryKey: ["mail", "show", thread],
    queryFn: () => unwrap(api.GET("/mail/show", { params: { query: { thread } } })),
  });
  const [text, setText] = useState("");
  const archive = (confirm: boolean) => unwrap(api.POST("/mail/archive", { params: { query: { thread, confirm } } }));
  return (
    <Loaded query={found}>
      {(conversation) => {
        const messages = conversation.messages ?? [];
        const last = messages.at(-1);
        const askReply = () => {
          const words = text; // what is confirmed is what was shown, whatever is typed meanwhile
          const reply = (confirm: boolean) =>
            unwrap(
              api.POST("/mail/reply", { params: { query: { message: last?.message.id ?? "", text: words, confirm } } }),
            );
          onAsk(async () => ({
            change: await reply(false),
            run: async () => {
              const done = await reply(true);
              setText("");
              return done;
            },
          }));
        };
        return (
          <section aria-label={conversation.subject || t("mail.noSubject")} className="space-y-4">
            <div className="flex items-center gap-2">
              <h2 className="text-lg font-medium">{conversation.subject || t("mail.noSubject")}</h2>
              <Button
                variant="outline"
                size="sm"
                className="ml-auto"
                disabled={busy}
                onClick={() => onAsk(async () => ({ change: await archive(false), run: () => archive(true) }))}
              >
                {t("mail.archive")}
              </Button>
            </div>
            {messages.map((content) => (
              <article key={content.message.id} className="rounded-lg border border-border p-3">
                <p className="text-sm text-muted-foreground">
                  {sender(content.message)} · {when.format(new Date(content.message.received_at))}
                </p>
                <pre className="mt-2 font-sans text-sm whitespace-pre-wrap">{content.text}</pre>
              </article>
            ))}
            {last ? (
              <div className="space-y-2">
                <label htmlFor="mail-reply" className="text-sm font-medium">
                  {t("mail.replyTo", { name: sender(last.message) })}
                </label>
                <textarea
                  id="mail-reply"
                  value={text}
                  maxLength={MAX_REPLY}
                  disabled={busy}
                  onChange={(event) => setText(event.target.value)}
                  className="min-h-24 w-full rounded-lg border border-border bg-card p-2 text-sm"
                />
                <Button disabled={busy || !text.trim()} onClick={askReply}>
                  {t("mail.draftReply")}
                </Button>
              </div>
            ) : null}
          </section>
        );
      }}
    </Loaded>
  );
}

function Row({
  message,
  important,
  open,
  onOpen,
}: {
  message: SchemaMailMessage;
  important: boolean;
  open: boolean;
  onOpen: () => void;
}) {
  return (
    <li>
      <button
        type="button"
        onClick={onOpen}
        aria-current={open ? "true" : undefined}
        className={cn(
          "flex w-full items-baseline gap-3 rounded-lg px-2 py-2 text-left hover:bg-accent",
          open && "bg-accent",
        )}
      >
        <span className="w-36 shrink-0 text-sm text-muted-foreground">
          {when.format(new Date(message.received_at))}
        </span>
        <span className="w-40 shrink-0 truncate text-sm">{sender(message)}</span>
        <span className="min-w-0 flex-1 truncate">{message.subject || t("mail.noSubject")}</span>
        {important ? <Badge tone="warn">{t("mail.important")}</Badge> : null}
      </button>
    </li>
  );
}

export function Mail() {
  const [params, setParams] = useSearchParams();
  const open = params.get("thread");
  const client = useQueryClient();
  const inbox = useQuery({ queryKey: ["mail", "inbox"], queryFn: () => unwrap(api.GET("/mail")) });
  const [step, setStep] = useState<Step | null>(null);
  const [kept, setKept] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const ask = useMutation({
    mutationFn: (preview: () => Promise<Step>) => preview(),
    onMutate: () => setDone(null),
    onSuccess: setStep,
  });
  const run = useMutation({
    mutationFn: (pending: Step) => pending.run(),
    onSuccess: (change) => {
      setStep(null);
      setDone(change.summary);
      // A kept reply is sent only after its own confirmation, of the draft as it stands then.
      setKept(change.done && change.draft && !change.sent ? change.draft.id : null);
      void client.invalidateQueries({ queryKey: ["mail"] });
    },
  });
  const askToSend = (draft: string) =>
    ask.mutate(async () => {
      const shown = await unwrap(api.POST("/mail/send", { params: { query: { draft } } }));
      const seen = shown.draft?.content.message.id;
      return {
        change: shown,
        run: () => unwrap(api.POST("/mail/send", { params: { query: { draft, seen, confirm: true } } })),
      };
    });
  const busy = ask.isPending || run.isPending || step !== null;
  const error = ask.error ?? run.error;
  return (
    <Page title={t("mail.title")} intro={t("mail.intro")}>
      {step ? (
        <Confirmation
          step={step}
          busy={run.isPending}
          onConfirm={() => run.mutate(step)}
          onCancel={() => setStep(null)}
        />
      ) : null}
      {error ? (
        <p role="alert" className="text-bad">
          {error.message}
        </p>
      ) : null}
      {done && !step ? <p role="status">{done}</p> : null}
      {kept && !step ? (
        <Card>
          <p className="mb-3">{t("mail.kept")}</p>
          <Button disabled={busy} onClick={() => askToSend(kept)}>
            {t("mail.send")}
          </Button>
        </Card>
      ) : null}
      <div className="grid gap-6 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
        <Loaded query={inbox}>
          {(found) =>
            (found.messages ?? []).length === 0 ? (
              <p className="text-muted-foreground">{t("mail.empty")}</p>
            ) : (
              <ul aria-label={t("mail.title")}>
                {(found.messages ?? []).map((message) => (
                  <Row
                    key={message.id}
                    message={message}
                    important={(found.important ?? []).includes(message.id)}
                    open={message.thread === open}
                    onOpen={() => setParams(message.thread ? { thread: message.thread } : {})}
                  />
                ))}
              </ul>
            )
          }
        </Loaded>
        {/* A key per conversation: a reply typed for one never becomes a reply to another. */}
        {open ? <Conversation key={open} thread={open} busy={busy} onAsk={(preview) => ask.mutate(preview)} /> : null}
      </div>
    </Page>
  );
}
