// Look and name on the Heinzel's page (canvas board Profile): the look editor of Meet your first Heinzelmännchen and the
// name. A save applies at once and offers Undo, as a rename suggested in chat does ("say it" with Undo): both are the
// person's own decision about their own Heinzel, so nothing waits for an approval.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Undo2 } from "lucide-react";
import { type FormEvent, useId, useState } from "react";
import { api, problem } from "@/api/client";
import type { components } from "@/api/schema";
import { Button } from "@/components/ui/button";
import { LookFigure, LookParts, type PartId } from "@/heinzel/LookEditor";
import type { Look } from "@/heinzel/surprise";
import { t } from "@/i18n";

type Snapshot = components["schemas"]["SessionSnapshot"];
type Profile = { name: string; appearance: Look };

const DEFAULT_LOOK: Look = { age: "grown", build: "regular" };
const same = (a: Profile, b: Profile) =>
  a.name === b.name && JSON.stringify(a.appearance) === JSON.stringify(b.appearance);
const stored = (snapshot: Snapshot): Profile => ({
  name: snapshot.name,
  appearance: snapshot.appearance ?? DEFAULT_LOOK,
});

export function LookSection({ snapshot }: { snapshot: Snapshot }) {
  const client = useQueryClient();
  const sessionId = snapshot.session_id;
  const field = useId();
  const heading = useId();
  const saved = stored(snapshot);
  const revision = snapshot.profile_revision;
  // The stored profile the person's draft starts from, and its revision: a save names that revision, so a change made
  // elsewhere meanwhile (the chat, the CLI) meets hz's revision check instead of being overwritten.
  const [base, setBase] = useState({ profile: saved, revision });
  const [draft, setDraft] = useState<Profile>(saved);
  const [part, setPart] = useState<PartId>("hat");
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  // What the last change replaced, so Undo puts it back; and what the status line says about it.
  const [before, setBefore] = useState<Profile | null>(null);
  const [status, setStatus] = useState("");
  const dirty = !same(draft, base.profile);
  // A change from elsewhere shows here, unless the person is in the middle of their own edit: that edit stays, and a
  // line says the saved profile changed.
  if (base.revision !== revision && !busy && !dirty) {
    setBase({ profile: saved, revision });
    setDraft(saved);
  }
  const outdated = dirty && base.revision !== revision;
  const suggestion = useQuery({
    queryKey: ["heinzel", "profile-proposal", sessionId],
    queryFn: async () => {
      const { data, error, response } = await api.GET("/heinzel/latest-profile-proposal", {
        params: { query: { session_id: sessionId } },
      });
      if (!response.ok || data === undefined) throw problem(response.status, error);
      if (data && (data.session_id !== sessionId || data.status !== "proposed")) return null;
      return data;
    },
    retry: false,
  });
  const offer = suggestion.data;
  const offered =
    offer && offer.expected_revision === revision && !same({ name: offer.name, appearance: offer.appearance }, saved)
      ? offer
      : null;

  const apply = async (next: Profile, undoing: boolean) => {
    if (busy) return;
    setBusy(true);
    setFailed(false);
    try {
      const { data, error, response } = await api.POST("/heinzel/profile", {
        body: {
          session_id: sessionId,
          request_id: crypto.randomUUID(),
          name: next.name.trim(),
          appearance: next.appearance,
          expected_revision: undoing ? revision : base.revision,
        },
      });
      if (!response.ok || !data) throw problem(response.status, error);
      if (data.session_id !== sessionId) throw new Error("Invalid profile acknowledgement");
      client.setQueryData(["heinzel", "session", sessionId], data);
      setBase({ profile: stored(data), revision: data.profile_revision });
      setDraft(stored(data));
      setBefore(undoing ? null : base.profile);
      setStatus(undoing ? t("page.undone") : t("page.saved", { name: data.name }));
      await client.invalidateQueries({ queryKey: ["heinzel", "sessions"] });
    } catch {
      setFailed(true);
      void client.invalidateQueries({ queryKey: ["heinzel", "session", sessionId] });
    } finally {
      setBusy(false);
    }
  };
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (dirty && draft.name.trim()) void apply(draft, false);
  };
  return (
    <section aria-labelledby={heading} className="flex flex-col gap-3">
      <h2 id={heading} className="text-base font-semibold">
        {t("page.look")}
      </h2>
      <form onSubmit={submit} className="flex flex-col gap-4 sm:flex-row sm:items-start">
        <LookFigure name={draft.name.trim() || saved.name} look={draft.appearance} part={part} onPart={setPart} />
        <div className="flex min-w-0 flex-1 flex-col gap-4">
          <div className="flex flex-col gap-1">
            <label htmlFor={field} className="text-sm font-medium">
              {t("page.nameLabel")}
            </label>
            <input
              id={field}
              value={draft.name}
              maxLength={100}
              autoComplete="off"
              onChange={(event) => setDraft({ ...draft, name: event.target.value })}
              className="h-11 rounded-lg border border-border bg-card px-3"
            />
          </div>
          <LookParts
            look={draft.appearance}
            part={part}
            onPart={setPart}
            onLook={(appearance) => setDraft({ ...draft, appearance })}
          />
          <div className="flex flex-wrap items-center gap-2">
            <Button type="submit" className="min-h-11" disabled={busy || !dirty || !draft.name.trim()}>
              {busy ? t("page.saving") : t("page.save")}
            </Button>
            {before && !dirty && (
              <Button
                type="button"
                variant="outline"
                className="min-h-11"
                disabled={busy}
                onClick={() => void apply(before, true)}
              >
                <Undo2 aria-hidden />
                {t("page.undo")}
              </Button>
            )}
            <p role="status" className="text-sm text-muted-foreground">
              {!dirty && status}
            </p>
          </div>
          {failed && (
            <p role="alert" className="text-sm">
              {outdated ? t("page.saveOutdated") : t("page.saveError")}
            </p>
          )}
          {outdated && (
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <p role="status" className="flex-1">
                {t("page.outdated", { name: saved.name })}
              </p>
              <Button
                type="button"
                variant="outline"
                className="min-h-11"
                disabled={busy}
                onClick={() => {
                  setBase({ profile: saved, revision });
                  setDraft(saved);
                  setFailed(false);
                }}
              >
                {t("page.discard")}
              </Button>
            </div>
          )}
          {offered && (
            <div className="flex flex-wrap items-center gap-2 rounded-lg border border-border p-3 text-sm">
              <span className="flex-1">{t("page.suggested", { name: offered.name })}</span>
              <Button
                type="button"
                variant="outline"
                className="min-h-11"
                // Taking it would replace an edit in progress: save or discard that first.
                disabled={busy || dirty}
                onClick={() => void apply({ name: offered.name, appearance: offered.appearance }, false)}
              >
                {t("page.useSuggestion")}
              </Button>
            </div>
          )}
        </div>
      </form>
    </section>
  );
}
