// Personal identities use the durable private session API; they are distinct from the existing workflow list and chat.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Archive, MessageCircle, Pencil, RotateCcw, Trash2 } from "lucide-react";
import { type ReactNode, useEffect, useId, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router";
import {
  api,
  problem,
  type SchemaHeinzelAppearance,
  type SchemaProfileProposal,
  type SchemaSessionList,
  type SchemaSessionSnapshot,
} from "@/api/client";
import { usePreviewFeature } from "@/api/queries";
import { HeinzelAvatar } from "@/components/HeinzelAvatar";
import { ACCESSORIES, AGES, BEARDS, BUILDS, HATS, SHIRTS } from "@/components/heinzel";
import { PaneAction, PaneActions } from "@/components/PaneActions";
import { TypeToConfirmDialog } from "@/components/TypeToConfirmDialog";
import { Button } from "@/components/ui/button";
import { appearanceLook, stateLabel } from "@/heinzel/look";
import { StartHelper } from "@/heinzel/StartHelper";
import { type MessageKey, t } from "@/i18n";
import {
  clearChatLink,
  clearDraftMessage,
  editInChatPath,
  OPEN_CHAT,
  readDraftMessage,
  takeDraftMessage,
} from "@/lib/chatIntent";
import { cn } from "@/lib/utils";

const sessionsKey = ["heinzel", "sessions"] as const;
const newDraftKey = "hz.personal-heinzel.new-draft";
const defaultLook: SchemaHeinzelAppearance = { age: "grown", build: "regular" };
const profileDraftKey = (sessionId: string) => `hz.personal-heinzel.profile-draft.${sessionId}`;
const profileSaveKey = (sessionId: string) => `hz.personal-heinzel.profile-save.${sessionId}`;

/** Drop every draft this tab keeps for a deleted Heinzel: profile, composer and proposal keys all name its id. */
function forgetStored(sessionId: string) {
  for (const storage of [sessionStorage, localStorage]) {
    const keys = Array.from({ length: storage.length }, (_, index) => storage.key(index));
    for (const key of keys) if (key?.includes(sessionId)) storage.removeItem(key);
  }
}
const field = "block w-full rounded border border-border bg-background px-2 py-1 text-sm";

type ProfileDraft = { name: string; appearance: SchemaHeinzelAppearance };
type ProfileSave = ProfileDraft & { request_id: string; expected_revision: number };
type NewDraft = ProfileDraft & { request_id?: string };
const REQUEST_ID = /^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/;

function readProfileSave(sessionId: string): { pending: ProfileSave | null; invalid: boolean } {
  try {
    const raw = sessionStorage.getItem(profileSaveKey(sessionId));
    if (raw === null) return { pending: null, invalid: false };
    const stored: unknown = JSON.parse(raw);
    const draft = parseDraft(stored);
    const value = stored as { request_id?: unknown; expected_revision?: unknown };
    if (
      !draft ||
      typeof value.request_id !== "string" ||
      !REQUEST_ID.test(value.request_id) ||
      typeof value.expected_revision !== "number" ||
      !Number.isInteger(value.expected_revision) ||
      value.expected_revision < 1
    )
      return { pending: null, invalid: true };
    return {
      pending: { ...draft, request_id: value.request_id, expected_revision: value.expected_revision },
      invalid: false,
    };
  } catch {
    return { pending: null, invalid: true };
  }
}

function profileSaveApplied(pending: ProfileSave, snapshot: SchemaSessionSnapshot): boolean {
  const saved = snapshot.appearance ?? { age: "grown", build: "regular" };
  const requested = pending.appearance;
  return (
    snapshot.profile_revision > pending.expected_revision &&
    snapshot.name === pending.name &&
    saved.age === requested.age &&
    saved.build === requested.build &&
    (saved.beard ?? null) === (requested.beard ?? null) &&
    (saved.hat ?? null) === (requested.hat ?? null) &&
    (saved.shirt ?? null) === (requested.shirt ?? null) &&
    JSON.stringify(saved.accessories ?? []) === JSON.stringify(requested.accessories ?? [])
  );
}

function parseDraft(stored: unknown): ProfileDraft | null {
  if (!stored || typeof stored !== "object" || !("name" in stored) || typeof stored.name !== "string") return null;
  if (!("appearance" in stored) || !stored.appearance || typeof stored.appearance !== "object") return null;
  const appearance = stored.appearance as Record<string, unknown>;
  const age = appearance.age;
  const build = appearance.build;
  if (!AGES.includes(age as (typeof AGES)[number]) || !BUILDS.includes(build as (typeof BUILDS)[number])) return null;
  const beard = appearance.beard;
  const hat = appearance.hat;
  const shirt = appearance.shirt;
  const accessories = appearance.accessories ?? [];
  if (
    (beard !== undefined && beard !== null && !BEARDS.includes(beard as (typeof BEARDS)[number])) ||
    (hat !== undefined && hat !== null && !Object.hasOwn(HATS, String(hat))) ||
    (shirt !== undefined && shirt !== null && !Object.hasOwn(SHIRTS, String(shirt))) ||
    !Array.isArray(accessories) ||
    !accessories.every((item) => typeof item === "string" && ACCESSORIES.includes(item as (typeof ACCESSORIES)[number]))
  )
    return null;
  return {
    name: stored.name,
    appearance: {
      age: age as SchemaHeinzelAppearance["age"],
      build: build as SchemaHeinzelAppearance["build"],
      ...(beard === undefined ? {} : { beard: beard as SchemaHeinzelAppearance["beard"] }),
      ...(hat === undefined ? {} : { hat: hat as SchemaHeinzelAppearance["hat"] }),
      ...(shirt === undefined ? {} : { shirt: shirt as SchemaHeinzelAppearance["shirt"] }),
      accessories: accessories as SchemaHeinzelAppearance["accessories"],
    },
  };
}

function readProfileDraft(sessionId: string): ProfileDraft | null {
  try {
    return parseDraft(JSON.parse(sessionStorage.getItem(profileDraftKey(sessionId)) ?? "null"));
  } catch {
    return null;
  }
}

/** The not yet created Heinzel of this tab; `request_id` is set once its creation was sent and may have happened. */
function readNewDraft(): NewDraft | null {
  try {
    const stored: unknown = JSON.parse(sessionStorage.getItem(newDraftKey) ?? "null");
    const draft = parseDraft(stored);
    if (!draft) return null;
    const request = (stored as { request_id?: unknown }).request_id;
    if (request === undefined) return draft;
    if (typeof request !== "string" || !REQUEST_ID.test(request)) return null;
    return { ...draft, request_id: request };
  } catch {
    return null;
  }
}

// These private mutations do not use the API's 503-as-result convention: a non-2xx response is always uncertain.
async function personalResult<T>(call: Promise<{ data?: T; error?: unknown; response: Response }>): Promise<T> {
  const { data, error, response } = await call;
  if (response.ok && data !== undefined) return data;
  throw problem(response.status, error);
}

/** The person's own Heinzels; the Heinzelmännchen page reads the same list to know whether it has any. */
export function usePersonalSessions() {
  return useQuery({
    queryKey: sessionsKey,
    queryFn: () => personalResult(api.GET("/heinzel/sessions")),
    retry: false,
  });
}

export function PersonalHeinzels() {
  const connectedFrontdoor = usePreviewFeature("connected_frontdoor");
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const [selected, setSelected] = useState<string | null>(null);
  // `?create=1` (the chat switcher, the footer) opens the draft; a draft left in this tab comes back with the page.
  const wanted = params.has("create");
  const [drafting, setDrafting] = useState(() => wanted || readNewDraft() !== null || readDraftMessage() !== null);
  useEffect(() => {
    if (wanted) setDrafting(true);
  }, [wanted]);
  const [locked, setLocked] = useState(false);
  const [retiredName, setRetiredName] = useState<string | null>(null);
  const retiredStatus = useRef<HTMLParagraphElement>(null);
  useEffect(() => {
    if (retiredName) retiredStatus.current?.focus();
  }, [retiredName]);
  const [restoreError, setRestoreError] = useState(false);
  const [deleting, setDeleting] = useState<{ session_id: string; name: string } | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [deletedNote, setDeletedNote] = useState<string | null>(null);
  const client = useQueryClient();
  const sessions = usePersonalSessions();
  const session = useQuery({
    queryKey: ["heinzel", "session", selected],
    queryFn: async () => {
      const result = await personalResult(
        api.GET("/heinzel/session", { params: { query: { session_id: selected ?? "" } } }),
      );
      if (!selected || result.session_id !== selected) throw new Error("Invalid profile response");
      return result;
    },
    enabled: selected !== null,
    retry: false,
  });
  const closeDraft = () => {
    setDrafting(false);
    setLocked(false);
    if (wanted)
      setParams(
        (current) => {
          const next = new URLSearchParams(current);
          next.delete("create");
          return next;
        },
        { replace: true },
      );
  };

  const all = sessions.data?.sessions ?? [];
  const live = all.filter((item) => item.state !== "retired");
  const retired = all.filter((item) => item.state === "retired");
  // A retired Heinzel leaves the list and its conversation closes; the Retired list below brings it back.
  const onRetired = (gone: SchemaSessionSnapshot) => {
    setSelected(null);
    setRetiredName(gone.name);
    setRestoreError(false);
    if (params.get("chat") === gone.session_id)
      setParams((current) => {
        const next = new URLSearchParams(current);
        clearChatLink(next);
        return next;
      });
  };
  const restore = async (sessionId: string) => {
    setRestoreError(false);
    try {
      const back = await personalResult(
        api.POST("/heinzel/restore", { body: { session_id: sessionId, request_id: crypto.randomUUID() } }),
      );
      if (back.session_id !== sessionId) throw new Error("Invalid restore response");
      client.setQueryData(["heinzel", "session", sessionId], back);
      await client.invalidateQueries({ queryKey: sessionsKey });
      setRetiredName(null);
      setSelected(sessionId);
    } catch {
      setRestoreError(true);
    }
  };

  // Delete for good (ADR 0148): only from the Retired list, after the typed name; nothing of it stays in this tab.
  const deleteForGood = async (target: { session_id: string; name: string }) => {
    setDeleteError(null);
    let waiting: string[] = [];
    try {
      const gone = await personalResult(
        api.POST("/heinzel/delete", { body: { session_id: target.session_id, name: target.name } }),
      );
      if (gone.session_id !== target.session_id) throw new Error("Invalid delete response");
      waiting = gone.state === "forgetting" ? (gone.waiting_for ?? []) : [];
    } catch (error) {
      // The server's own words say why (a turn still finishing, an unavailable channel); a bare status does not.
      const said = error instanceof Error && error.message && !error.message.startsWith("HTTP ");
      setDeleteError(said ? (error as Error).message : t("personal.deleteError"));
      return;
    }
    forgetStored(target.session_id);
    client.removeQueries({ queryKey: ["heinzel", "session", target.session_id] });
    await client.invalidateQueries({ queryKey: sessionsKey });
    setDeleting(null);
    setRetiredName(null);
    setDeletedNote(
      waiting.length
        ? t("personal.deletedWaiting", { name: target.name, plugins: waiting.join(", ") })
        : t("personal.deletedDone", { name: target.name }),
    );
  };

  return (
    <section aria-labelledby="personal-heinzels" className="mb-6 space-y-3 rounded-lg border border-border p-4">
      <h2 id="personal-heinzels" className="text-base font-semibold">
        {t("personal.title")}
      </h2>
      <p className="text-sm text-muted-foreground">{t("personal.intro")}</p>
      {sessions.isPending ? (
        <p role="status">{t("state.loading")}</p>
      ) : sessions.isError ? (
        <p role="alert">{t("personal.loadError")}</p>
      ) : (
        <div
          role="radiogroup"
          aria-label={t("personal.choose")}
          className="grid min-w-0 gap-2 sm:grid-cols-2 lg:grid-cols-3"
        >
          {live.length === 0 && (
            <p className="text-sm text-muted-foreground sm:col-span-2 lg:col-span-3">{t("personal.none")}</p>
          )}
          {live.map((item) => (
            // A card of a radio group: the shared button, holding the figure a radio input can't.
            <Button
              key={item.session_id}
              type="button"
              variant="outline"
              role="radio"
              aria-checked={!drafting && item.session_id === selected}
              disabled={locked || drafting}
              onClick={() => setSelected(item.session_id)}
              className={cn(
                // The shared button sizes an icon; the card holds a figure, so it names the figure's size itself.
                "h-auto min-h-14 min-w-0 justify-start gap-3 p-2 text-left font-normal [&_svg]:size-10",
                !drafting && item.session_id === selected && "border-primary bg-accent text-accent-foreground",
              )}
            >
              <HeinzelAvatar
                view="bust"
                {...appearanceLook(item.appearance)}
                className="size-10 shrink-0 overflow-hidden"
              />
              <span className="min-w-0">
                <span className="block truncate text-sm font-medium">{item.name}</span>
                <span className="block text-xs text-muted-foreground">{stateLabel(item.state)}</span>
              </span>
            </Button>
          ))}
        </div>
      )}
      {!drafting && (
        <Button type="button" variant="outline" disabled={locked} onClick={() => setDrafting(true)}>
          {t("personal.create")}
        </Button>
      )}
      {connectedFrontdoor && <StartHelper />}
      {drafting && (
        <NewHeinzel
          onLock={setLocked}
          onCancel={closeDraft}
          onCreated={(created) => {
            setDrafting(false);
            setLocked(false);
            setSelected(created.session_id);
            // Replaces `?create=1`: the new Heinzel's conversation opens beside (on a phone: instead of) the page.
            navigate(`/heinzel?chat=${encodeURIComponent(created.session_id)}`);
          }}
        />
      )}
      {!drafting && selected && session.isPending && <p role="status">{t("state.loading")}</p>}
      {!drafting && selected && session.isError && <p role="alert">{t("personal.loadError")}</p>}
      {!drafting && selected && session.data?.state !== "retired" && session.data && (
        <HeinzelView key={selected} initial={session.data} onRetired={onRetired} />
      )}
      {retiredName && !selected && (
        // The view that held Retire is gone: focus moves here, so a keyboard or screen reader user hears where it went.
        <p role="status" ref={retiredStatus} tabIndex={-1}>
          {t("personal.retiredDone", { name: retiredName })}
        </p>
      )}
      {retired.length > 0 && (
        <details className="rounded-lg border border-border p-3" open={retiredName !== null || undefined}>
          <summary className="cursor-pointer text-sm font-medium">
            {t("personal.retiredList", { count: retired.length })}
          </summary>
          <ul aria-label={t("personal.state.retired")} className="mt-2 space-y-1">
            {retired.map((item) => (
              <li key={item.session_id} className="flex min-w-0 items-center gap-2">
                <HeinzelAvatar
                  view="bust"
                  {...appearanceLook(item.appearance)}
                  className="size-8 shrink-0 overflow-hidden"
                />
                <span className="min-w-0 flex-1 truncate text-sm">{item.name}</span>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  aria-label={t("personal.restore", { name: item.name })}
                  title={t("personal.restore", { name: item.name })}
                  onClick={() => void restore(item.session_id)}
                >
                  <RotateCcw aria-hidden />
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="text-bad"
                  aria-label={t("personal.delete", { name: item.name })}
                  title={t("personal.delete", { name: item.name })}
                  onClick={() => {
                    setDeleteError(null);
                    setDeleting({ session_id: item.session_id, name: item.name });
                  }}
                >
                  <Trash2 aria-hidden />
                </Button>
              </li>
            ))}
          </ul>
          {restoreError && <p role="alert">{t("personal.restoreError")}</p>}
        </details>
      )}
      {deletedNote && (
        <p role="status" className="text-sm">
          {deletedNote}
        </p>
      )}
      {deleting && (
        <TypeToConfirmDialog
          title={t("personal.deleteTitle", { name: deleting.name })}
          lines={[t("personal.deleteWhat"), t("personal.deleteOutside"), t("personal.deleteNoUndo")]}
          expected={deleting.name}
          action={t("personal.deleteAction")}
          error={deleteError}
          onConfirm={() => deleteForGood(deleting)}
          onClose={() => setDeleting(null)}
        />
      )}
    </section>
  );
}

/** The chosen Heinzel, read-only: its name and look are edited in its chat's Details, the one editor. */
function HeinzelView({
  initial,
  onRetired,
}: {
  initial: SchemaSessionSnapshot;
  onRetired: (gone: SchemaSessionSnapshot) => void;
}) {
  const client = useQueryClient();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(false);
  const retire = async () => {
    setBusy(true);
    setError(false);
    try {
      const gone = await personalResult(
        api.POST("/heinzel/retire", { body: { session_id: initial.session_id, request_id: crypto.randomUUID() } }),
      );
      if (gone.session_id !== initial.session_id || gone.state !== "retired")
        throw new Error("Invalid retire response");
      client.setQueryData(["heinzel", "session", gone.session_id], gone);
      client.setQueryData<SchemaSessionList>(sessionsKey, (list) =>
        list
          ? {
              ...list,
              sessions: list.sessions?.map((item) =>
                item.session_id === gone.session_id ? { ...item, state: "retired" } : item,
              ),
            }
          : list,
      );
      void client.invalidateQueries({ queryKey: sessionsKey });
      onRetired(gone);
    } catch {
      setError(true);
    } finally {
      setBusy(false);
    }
  };
  return (
    // Not a heading: the chat beside it carries the Heinzel's name as its heading.
    <div className="space-y-3 pt-2">
      <div className="flex min-w-0 items-center gap-3">
        <HeinzelAvatar
          label={t("personal.preview")}
          {...appearanceLook(initial.appearance)}
          className="h-24 w-16 shrink-0"
        />
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold" data-testid="heinzel-view-name">
            {initial.name}
          </p>
          <p className="text-xs text-muted-foreground">{stateLabel(initial.state ?? "proposed")}</p>
          <p className="text-sm text-muted-foreground">{t("personal.readOnlyHelp")}</p>
        </div>
      </div>
      {error && <p role="alert">{t("personal.retireError")}</p>}
      <PaneActions>
        <PaneAction
          key="edit"
          icon={Pencil}
          onClick={() => {
            navigate(editInChatPath(initial.session_id));
            // Its chat may be linked already: the address changes only by the editor, so show the Chat tab too.
            window.dispatchEvent(new Event(OPEN_CHAT));
          }}
        >
          {t("personal.editInChat")}
        </PaneAction>
        <PaneAction
          key="chat"
          icon={MessageCircle}
          onClick={() => {
            // Already linked: the address would not change, so ask the shell to show the conversation (a phone's tab).
            if (params.get("chat") === initial.session_id) window.dispatchEvent(new Event(OPEN_CHAT));
            else navigate(`/heinzel?chat=${encodeURIComponent(initial.session_id)}`);
          }}
        >
          {t("personal.chatWith", { name: initial.name })}
        </PaneAction>
        <PaneAction key="retire" icon={Archive} disabled={busy} onClick={() => void retire()}>
          {t("personal.retire")}
        </PaneAction>
      </PaneActions>
    </div>
  );
}

/** The one editor below the picker, a draft or a saved profile: set off from the cards by the section's one rule. */
function EditorForm({
  labelledBy,
  onSubmit,
  children,
}: {
  labelledBy?: string;
  onSubmit: () => void;
  children: ReactNode;
}) {
  return (
    <form
      aria-labelledby={labelledBy}
      className="space-y-3 border-t border-border pt-4"
      onSubmit={(event) => {
        event.preventDefault();
        onSubmit();
      }}
    >
      {children}
    </form>
  );
}

/** A Heinzel that does not exist yet: its name and look are a draft in this tab until Save creates it (ADR 0049). */
function NewHeinzel({
  onLock,
  onCancel,
  onCreated,
}: {
  onLock: (locked: boolean) => void;
  onCancel: () => void;
  onCreated: (created: SchemaSessionSnapshot) => void;
}) {
  const client = useQueryClient();
  const [stored] = useState(readNewDraft);
  // A message handed over by the chat composer's "+": it waits in the new chat's composer after Save.
  const [message] = useState(readDraftMessage);
  const [name, setName] = useState(stored?.name ?? "");
  const [look, setLook] = useState<SchemaHeinzelAppearance>(stored?.appearance ?? defaultLook);
  // A sent creation keeps its id and exact body, so a retry recovers the same Heinzel instead of making a second.
  const [pending, setPending] = useState<Required<NewDraft> | null>(
    stored?.request_id ? { ...stored, request_id: stored.request_id } : null,
  );
  const [busy, setBusy] = useState(false);
  const running = useRef(false);
  const [error, setError] = useState(false);
  const [storageFailed, setStorageFailed] = useState(false);

  useEffect(() => {
    if (pending) return;
    try {
      if (!name && JSON.stringify(look) === JSON.stringify(defaultLook)) sessionStorage.removeItem(newDraftKey);
      else sessionStorage.setItem(newDraftKey, JSON.stringify({ name, appearance: look }));
      setStorageFailed(false);
    } catch {
      setStorageFailed(true);
    }
  }, [look, name, pending]);
  useEffect(() => {
    onLock(busy);
  }, [busy, onLock]);

  const create = async () => {
    if (running.current || (!pending && !name.trim())) return;
    running.current = true;
    const request = pending ?? { request_id: crypto.randomUUID(), name: name.trim(), appearance: look };
    try {
      const serialized = JSON.stringify(request);
      sessionStorage.setItem(newDraftKey, serialized);
      if (sessionStorage.getItem(newDraftKey) !== serialized) throw new Error("Creation recovery unavailable");
    } catch {
      running.current = false;
      setStorageFailed(true);
      setError(true);
      return;
    }
    setPending(request);
    setBusy(true);
    setError(false);
    try {
      const created = await personalResult(api.POST("/heinzel/new-chat", { body: request }));
      if (!/^hz-[0-9A-HJKMNP-TV-Z]{26}$/.test(created.session_id) || created.heinzel_id !== created.session_id) {
        throw new Error("Invalid creation acknowledgement");
      }
      try {
        sessionStorage.removeItem(newDraftKey);
      } catch {
        // The Heinzel exists; a stale draft would only offer the same idempotent request again.
      }
      client.setQueryData(["heinzel", "session", created.session_id], created);
      // Its card joins the list at once: the page keeps its height while a phone scrolls to the conversation.
      client.setQueryData<SchemaSessionList>(sessionsKey, (list) =>
        list && !list.sessions?.some((item) => item.session_id === created.session_id)
          ? {
              ...list,
              sessions: [
                {
                  session_id: created.session_id,
                  name: created.name,
                  state: "proposed",
                  last_seq: 0,
                  updated_at: new Date().toISOString(),
                  appearance: created.appearance,
                },
                ...(list.sessions ?? []),
              ],
            }
          : list,
      );
      void client.invalidateQueries({ queryKey: sessionsKey });
      // The Heinzel exists either way; a message this tab cannot place stays here for the next draft.
      if (message) takeDraftMessage(created.session_id, message);
      onCreated(created);
    } catch {
      setError(true);
    } finally {
      running.current = false;
      setBusy(false);
    }
  };
  const discard = () => {
    try {
      sessionStorage.removeItem(newDraftKey);
    } catch {
      // Nothing was created by a draft; an unreadable store leaves nothing to clean.
    }
    clearDraftMessage();
    onCancel();
  };

  return (
    <EditorForm labelledBy="new-heinzel" onSubmit={() => void create()}>
      <h3 id="new-heinzel" className="text-sm font-semibold">
        {t("personal.draftTitle")}
      </h3>
      <p className="text-sm text-muted-foreground">{t("personal.draftHelp")}</p>
      <LookFields
        name={name}
        look={look}
        disabled={busy || pending !== null}
        onName={setName}
        onLook={setLook}
        autoFocus
      />
      {message && (
        <figure className="space-y-1 text-sm">
          <figcaption className="text-muted-foreground">{t("personal.firstMessage")}</figcaption>
          <blockquote className="max-h-24 overflow-y-auto whitespace-pre-wrap rounded border border-border p-2">
            {message}
          </blockquote>
        </figure>
      )}
      {error && <p role="alert">{t("personal.createError")}</p>}
      {storageFailed && <p role="status">{t("personal.draftStorageFailed")}</p>}
      <div className="flex flex-wrap gap-2">
        <Button type="submit" disabled={busy || (!pending && !name.trim())}>
          {pending && !busy ? t("personal.retryCreate") : t("personal.createSubmit")}
        </Button>
        <Button type="button" variant="outline" disabled={busy} onClick={discard}>
          {t("personal.discardDraft")}
        </Button>
      </div>
      {pending && !busy && <p className="text-sm text-muted-foreground">{t("personal.discardPendingHelp")}</p>}
    </EditorForm>
  );
}

const AGE_LABEL: Record<(typeof AGES)[number], MessageKey> = {
  baby: "personal.age.baby",
  young: "personal.age.young",
  grown: "personal.age.grown",
  old: "personal.age.old",
};
const BEARD_LABEL: Record<(typeof BEARDS)[number], MessageKey> = {
  none: "personal.beard.none",
  short: "personal.beard.short",
  medium: "personal.beard.medium",
  long: "personal.beard.long",
};
const BUILD_LABEL: Record<(typeof BUILDS)[number], MessageKey> = {
  slim: "personal.build.slim",
  regular: "personal.build.regular",
  round: "personal.build.round",
};
const COLOUR_LABEL: Record<keyof typeof HATS | keyof typeof SHIRTS, MessageKey> = {
  red: "personal.colour.red",
  rust: "personal.colour.rust",
  ochre: "personal.colour.ochre",
  green: "personal.colour.green",
  teal: "personal.colour.teal",
  blue: "personal.colour.blue",
  purple: "personal.colour.purple",
  slate: "personal.colour.slate",
  "red-brown": "personal.colour.redBrown",
  rose: "personal.colour.rose",
};

/** One colour choice as swatches: the theme's own colour first, then the closed palette, each named for a reader. */
function Swatches<Colour extends keyof typeof COLOUR_LABEL>({
  label,
  palette,
  value,
  onChange,
}: {
  label: string;
  palette: Record<Colour, string>;
  value: Colour | null;
  onChange: (value: Colour | null) => void;
}) {
  const choices: [Colour | null, string][] = [
    [null, t("personal.themeDefault")],
    ...(Object.keys(palette) as Colour[]).map((item): [Colour, string] => [item, t(COLOUR_LABEL[item])]),
  ];
  const id = useId();
  return (
    <div className="space-y-1 text-sm">
      <span id={id}>{label}</span>
      <div role="radiogroup" aria-labelledby={id} className="flex flex-wrap gap-1.5">
        {choices.map(([item, name]) => (
          // A swatch of a radio group: the shared icon button, painted in the colour it chooses.
          <Button
            key={item ?? "theme"}
            type="button"
            variant="ghost"
            size="icon"
            role="radio"
            aria-checked={value === item}
            aria-label={name}
            title={name}
            onClick={() => onChange(item)}
            style={item ? { backgroundColor: palette[item] } : undefined}
            className={cn(
              "size-8 shrink-0 rounded-full border border-border pointer-coarse:size-11",
              // The theme's own colour is no colour of the palette: a split disc, half surface and half foreground.
              !item && "bg-linear-to-br from-muted from-50% to-foreground/40 to-50%",
              value === item && "ring-2 ring-primary ring-offset-2 ring-offset-background",
            )}
          />
        ))}
      </div>
    </div>
  );
}

/** A Heinzel's name and its choices, with the figure they make; shared by a draft and a saved profile. */
function LookFields({
  name,
  look,
  disabled,
  onName,
  onLook,
  autoFocus = false,
}: {
  name: string;
  look: SchemaHeinzelAppearance;
  disabled: boolean;
  onName: (name: string) => void;
  onLook: (look: SchemaHeinzelAppearance) => void;
  autoFocus?: boolean;
}) {
  return (
    <>
      <div className="flex items-center gap-4">
        <HeinzelAvatar label={t("personal.preview")} {...appearanceLook(look)} className="h-24 w-16" />
        <p className="text-sm text-muted-foreground">{t("personal.previewHelp")}</p>
      </div>
      <fieldset disabled={disabled} className="grid min-w-0 gap-3 sm:grid-cols-2">
        <label className="space-y-1 text-sm">
          <span>{t("personal.name")}</span>
          <input
            className={field}
            value={name}
            required
            maxLength={100}
            // biome-ignore lint/a11y/noAutofocus: the draft opens on the person's request and its first step is the name
            autoFocus={autoFocus}
            onChange={(event) => onName(event.target.value)}
          />
        </label>
        <label className="space-y-1 text-sm">
          <span>{t("personal.age")}</span>
          <select
            className={field}
            value={look.age}
            onChange={(event) => onLook({ ...look, age: event.target.value as SchemaHeinzelAppearance["age"] })}
          >
            {AGES.map((item) => (
              <option key={item} value={item}>
                {t(AGE_LABEL[item])}
              </option>
            ))}
          </select>
        </label>
        <label className="space-y-1 text-sm">
          <span>{t("personal.beard")}</span>
          <select
            className={field}
            value={look.beard ?? ""}
            onChange={(event) =>
              onLook({ ...look, beard: (event.target.value || null) as SchemaHeinzelAppearance["beard"] })
            }
          >
            <option value="">{t("personal.ageDefault")}</option>
            {BEARDS.map((item) => (
              <option key={item} value={item}>
                {t(BEARD_LABEL[item])}
              </option>
            ))}
          </select>
        </label>
        <label className="space-y-1 text-sm">
          <span>{t("personal.build")}</span>
          <select
            className={field}
            value={look.build}
            onChange={(event) => onLook({ ...look, build: event.target.value as SchemaHeinzelAppearance["build"] })}
          >
            {BUILDS.map((item) => (
              <option key={item} value={item}>
                {t(BUILD_LABEL[item])}
              </option>
            ))}
          </select>
        </label>
        <Swatches
          label={t("personal.hat")}
          palette={HATS}
          value={look.hat ?? null}
          onChange={(hat) => onLook({ ...look, hat })}
        />
        <Swatches
          label={t("personal.shirt")}
          palette={SHIRTS}
          value={look.shirt ?? null}
          onChange={(shirt) => onLook({ ...look, shirt })}
        />
        <fieldset className="space-y-1 text-sm">
          <legend>{t("personal.accessories")}</legend>
          {ACCESSORIES.map((accessory) => (
            <label key={accessory} className="mr-3 inline-flex min-h-8 items-center gap-1">
              <input
                type="checkbox"
                checked={(look.accessories ?? []).includes(accessory)}
                onChange={(event) =>
                  onLook({
                    ...look,
                    accessories: event.target.checked
                      ? [...(look.accessories ?? []), accessory]
                      : (look.accessories ?? []).filter((item) => item !== accessory),
                  })
                }
              />
              {t(`personal.accessory.${accessory}`)}
            </label>
          ))}
        </fieldset>
      </fieldset>
    </>
  );
}

/** The one editor of a saved Heinzel's name and look, in its chat's Details. */
export function ProfileEditor({
  initial,
  onLock,
  suggestion = null,
}: {
  initial: SchemaSessionSnapshot;
  onLock: (locked: boolean) => void;
  suggestion?: SchemaProfileProposal | null;
}) {
  const client = useQueryClient();
  const [draftStorageFailed, setDraftStorageFailed] = useState(false);
  const [draft] = useState(() => readProfileDraft(initial.session_id));
  const [snapshot, setSnapshot] = useState(initial);
  const [savedRequest] = useState(() => readProfileSave(initial.session_id));
  const [name, setName] = useState(savedRequest.pending?.name ?? draft?.name ?? initial.name);
  const [look, setLook] = useState<SchemaHeinzelAppearance>(
    savedRequest.pending?.appearance ?? draft?.appearance ?? initial.appearance ?? { age: "grown", build: "regular" },
  );
  const [pending, setPending] = useState<ProfileSave | null>(savedRequest.pending);
  const [pendingStorageInvalid, setPendingStorageInvalid] = useState(savedRequest.invalid);
  const [busy, setBusy] = useState(false);
  const savingNow = useRef(false);
  const [error, setError] = useState(false);
  const [justSaved, setJustSaved] = useState(false);

  useEffect(() => {
    const key = profileDraftKey(snapshot.session_id);
    const savedLook = snapshot.appearance ?? { age: "grown", build: "regular" };
    try {
      if (name === snapshot.name && JSON.stringify(look) === JSON.stringify(savedLook)) sessionStorage.removeItem(key);
      else sessionStorage.setItem(key, JSON.stringify({ name, appearance: look }));
      setDraftStorageFailed(false);
    } catch {
      setDraftStorageFailed(true);
    }
  }, [look, name, snapshot]);

  const save = async () => {
    if (savingNow.current || pendingStorageInvalid) return;
    savingNow.current = true;
    const request: ProfileSave = pending ?? {
      request_id: crypto.randomUUID(),
      name,
      appearance: look,
      expected_revision: snapshot.profile_revision,
    };
    try {
      // Persist the id and exact body before sending. A reload can then retry idempotently or reconcile a lost ACK.
      const serialized = JSON.stringify(request);
      sessionStorage.setItem(profileSaveKey(snapshot.session_id), serialized);
      if (sessionStorage.getItem(profileSaveKey(snapshot.session_id)) !== serialized)
        throw new Error("Profile recovery unavailable");
    } catch {
      savingNow.current = false;
      setDraftStorageFailed(true);
      setError(true);
      return;
    }
    setPending(request);
    setPendingStorageInvalid(false);
    onLock(true);
    setBusy(true);
    setError(false);
    try {
      const saved = await personalResult(
        api.POST("/heinzel/profile", { body: { session_id: snapshot.session_id, ...request } }),
      );
      if (
        saved.session_id !== snapshot.session_id ||
        !Number.isInteger(saved.profile_revision) ||
        !profileSaveApplied(request, saved)
      ) {
        throw new Error("Invalid profile acknowledgement");
      }
      setSnapshot(saved);
      setName(saved.name);
      setLook(saved.appearance ?? { age: "grown", build: "regular" });
      setPending(null);
      try {
        sessionStorage.removeItem(profileSaveKey(snapshot.session_id));
      } catch {
        setDraftStorageFailed(true);
      }
      onLock(false);
      setJustSaved(true);
      client.setQueryData(["heinzel", "session", saved.session_id], saved);
      await client.invalidateQueries({ queryKey: sessionsKey });
    } catch {
      setError(true);
    } finally {
      savingNow.current = false;
      setBusy(false);
    }
  };
  const reload = async () => {
    if (savingNow.current) return;
    savingNow.current = true;
    setBusy(true);
    try {
      const saved = await personalResult(
        api.GET("/heinzel/session", { params: { query: { session_id: snapshot.session_id } } }),
      );
      if (saved.session_id !== snapshot.session_id) throw new Error("Invalid profile response");
      // A read at the original revision cannot rule out a delayed POST committing later.
      // Once the revision advances, the old compare-and-swap can no longer newly apply.
      if (
        pendingStorageInvalid ||
        !pending ||
        !Number.isInteger(saved.profile_revision) ||
        saved.profile_revision <= pending.expected_revision
      )
        throw new Error("Profile save remains uncertain");
      setSnapshot(saved);
      setName(saved.name);
      setLook(saved.appearance ?? { age: "grown", build: "regular" });
      client.setQueryData(["heinzel", "session", saved.session_id], saved);
      setPending(null);
      setPendingStorageInvalid(false);
      try {
        sessionStorage.removeItem(profileSaveKey(snapshot.session_id));
      } catch {
        setDraftStorageFailed(true);
        setError(true);
        return;
      }
      setError(false);
      onLock(false);
      await client.invalidateQueries({ queryKey: sessionsKey });
    } catch {
      setError(true);
    } finally {
      savingNow.current = false;
      setBusy(false);
    }
  };
  const pendingNeedsReload =
    pendingStorageInvalid || (pending !== null && snapshot.profile_revision !== pending.expected_revision);
  const disabled = busy || pending !== null || pendingStorageInvalid;
  const savedLook = snapshot.appearance ?? { age: "grown" as const, build: "regular" as const };
  const hasUncommittedChanges = name !== snapshot.name || JSON.stringify(look) !== JSON.stringify(savedLook);
  const suggestionSelected =
    suggestion !== null && name === suggestion.name && JSON.stringify(look) === JSON.stringify(suggestion.appearance);
  useEffect(() => {
    onLock(hasUncommittedChanges || pending !== null || pendingStorageInvalid || busy);
  }, [hasUncommittedChanges, pending, pendingStorageInvalid, busy, onLock]);
  return (
    <EditorForm onSubmit={() => void save()}>
      {suggestion?.session_id === snapshot.session_id && suggestion.status === "proposed" && (
        <section aria-label={t("personal.suggested")} className="space-y-2 rounded border border-border p-2 text-sm">
          <h3 className="font-medium">{t("personal.suggested")}</h3>
          <p>{t("personal.suggestedName", { name: suggestion.name })}</p>
          <HeinzelAvatar
            label={t("personal.suggestedLook")}
            age={suggestion.appearance.age}
            build={suggestion.appearance.build}
            beard={suggestion.appearance.beard ?? undefined}
            hat={suggestion.appearance.hat ?? undefined}
            shirt={suggestion.appearance.shirt ?? undefined}
            accessories={suggestion.appearance.accessories}
            className="h-24 w-16"
          />
          <p>{t("personal.suggestedHelp")}</p>
          {suggestion.expected_revision !== snapshot.profile_revision && (
            <p role="status">{t("personal.suggestedStale")}</p>
          )}
          {hasUncommittedChanges && !pending && suggestionSelected && (
            <p role="status">{t("personal.suggestedReady")}</p>
          )}
          {hasUncommittedChanges && !pending && !suggestionSelected && (
            <p role="status">{t("personal.suggestedBlocked")}</p>
          )}
          <Button
            type="button"
            variant="outline"
            disabled={disabled || hasUncommittedChanges || suggestion.expected_revision !== snapshot.profile_revision}
            onClick={() => {
              setName(suggestion.name);
              setLook(suggestion.appearance);
            }}
          >
            {t("personal.suggestedUse")}
          </Button>
        </section>
      )}
      <LookFields
        name={name}
        look={look}
        disabled={disabled}
        onName={(value) => {
          setName(value);
          setJustSaved(false);
        }}
        onLook={(value) => {
          setLook(value);
          setJustSaved(false);
        }}
      />
      {error && <p role="alert">{t("personal.saveError")}</p>}
      {pendingNeedsReload && <p role="alert">{t("personal.saveOutcomeChanged")}</p>}
      {draftStorageFailed && <p role="status">{t("personal.draftStorageFailed")}</p>}
      {justSaved && !hasUncommittedChanges && !pending && <p role="status">{t("personal.saved")}</p>}
      <div className="flex flex-wrap gap-2">
        <Button type="submit" variant="default" disabled={busy || pendingStorageInvalid || (!pending && !name.trim())}>
          {pending ? t("personal.retrySave") : t("personal.save")}
        </Button>
        {hasUncommittedChanges && !pending && !pendingStorageInvalid && (
          <Button
            type="button"
            variant="outline"
            disabled={busy}
            onClick={() => {
              setName(snapshot.name);
              setLook(savedLook);
            }}
          >
            {t("personal.discardEdits")}
          </Button>
        )}
        {(pending || pendingStorageInvalid) && (
          <Button type="button" variant="outline" disabled={busy} onClick={() => void reload()}>
            {t("personal.reload")}
          </Button>
        )}
      </div>
      {hasUncommittedChanges && <p className="text-sm text-muted-foreground">{t("personal.saveFirst")}</p>}
    </EditorForm>
  );
}
