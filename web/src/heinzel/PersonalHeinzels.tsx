// Personal identities use the durable private session API; they are distinct from the existing workflow list and chat.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router";
import { api, problem, type SchemaHeinzelAppearance, type SchemaSessionSnapshot } from "@/api/client";
import { HeinzelAvatar } from "@/components/HeinzelAvatar";
import { ACCESSORIES, AGES, BEARDS, BUILDS, HATS, SHIRTS } from "@/components/heinzel";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n";

const sessionsKey = ["heinzel", "sessions"] as const;
const pendingCreation = "hz.personal-heinzel.creation";
const profileDraftKey = (sessionId: string) => `hz.personal-heinzel.profile-draft.${sessionId}`;
const profileSaveKey = (sessionId: string) => `hz.personal-heinzel.profile-save.${sessionId}`;
const field = "block w-full rounded border border-border bg-background px-2 py-1 text-sm";

type ProfileDraft = { name: string; appearance: SchemaHeinzelAppearance };
type ProfileSave = ProfileDraft & { request_id: string; expected_revision: number };

function readProfileSave(sessionId: string): { pending: ProfileSave | null; invalid: boolean } {
  try {
    const raw = sessionStorage.getItem(profileSaveKey(sessionId));
    if (raw === null) return { pending: null, invalid: false };
    const stored: unknown = JSON.parse(raw);
    if (!stored || typeof stored !== "object") return { pending: null, invalid: true };
    const value = stored as Record<string, unknown>;
    const appearance = value.appearance;
    if (
      typeof value.request_id !== "string" ||
      !/^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/.test(value.request_id) ||
      typeof value.name !== "string" ||
      typeof value.expected_revision !== "number" ||
      !Number.isInteger(value.expected_revision) ||
      value.expected_revision < 1 ||
      !appearance ||
      typeof appearance !== "object"
    )
      return { pending: null, invalid: true };
    const look = appearance as Record<string, unknown>;
    if (!AGES.includes(look.age as (typeof AGES)[number]) || !BUILDS.includes(look.build as (typeof BUILDS)[number]))
      return { pending: null, invalid: true };
    const beard = look.beard;
    const hat = look.hat;
    const shirt = look.shirt;
    const accessories = look.accessories ?? [];
    if (
      (beard !== undefined && beard !== null && !BEARDS.includes(beard as (typeof BEARDS)[number])) ||
      (hat !== undefined && hat !== null && !Object.hasOwn(HATS, String(hat))) ||
      (shirt !== undefined && shirt !== null && !Object.hasOwn(SHIRTS, String(shirt))) ||
      !Array.isArray(accessories) ||
      !accessories.every(
        (item) => typeof item === "string" && ACCESSORIES.includes(item as (typeof ACCESSORIES)[number]),
      )
    )
      return { pending: null, invalid: true };
    return {
      pending: {
        request_id: value.request_id,
        name: value.name,
        expected_revision: value.expected_revision,
        appearance: {
          age: look.age as SchemaHeinzelAppearance["age"],
          build: look.build as SchemaHeinzelAppearance["build"],
          ...(beard === undefined ? {} : { beard: beard as SchemaHeinzelAppearance["beard"] }),
          ...(hat === undefined ? {} : { hat: hat as SchemaHeinzelAppearance["hat"] }),
          ...(shirt === undefined ? {} : { shirt: shirt as SchemaHeinzelAppearance["shirt"] }),
          accessories: accessories as SchemaHeinzelAppearance["accessories"],
        },
      },
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

function readProfileDraft(sessionId: string): ProfileDraft | null {
  try {
    const stored: unknown = JSON.parse(sessionStorage.getItem(profileDraftKey(sessionId)) ?? "null");
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
      !accessories.every(
        (item) => typeof item === "string" && ACCESSORIES.includes(item as (typeof ACCESSORIES)[number]),
      )
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

function creationId(): string {
  const saved = localStorage.getItem(pendingCreation);
  if (saved) return saved;
  const id = crypto.randomUUID();
  localStorage.setItem(pendingCreation, id);
  return id;
}

export function PersonalHeinzels() {
  const client = useQueryClient();
  const [selected, setSelected] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const creatingNow = useRef(false);
  const [creationError, setCreationError] = useState(false);
  const [locked, setLocked] = useState(false);
  const sessions = useQuery({
    queryKey: sessionsKey,
    queryFn: () => personalResult(api.GET("/heinzel/sessions")),
    retry: false,
  });
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

  const create = async () => {
    if (creatingNow.current) return;
    creatingNow.current = true;
    setCreating(true);
    setCreationError(false);
    try {
      // Retain the id across reloads and unknown acknowledgements so retry never creates a second session.
      const request_id = creationId();
      const saved = await personalResult(api.POST("/heinzel/new-chat", { body: { request_id, name: "New Heinzel" } }));
      if (!/^hz-[0-9A-HJKMNP-TV-Z]{26}$/.test(saved.session_id) || saved.heinzel_id !== saved.session_id) {
        throw new Error("Invalid creation acknowledgement");
      }
      localStorage.removeItem(pendingCreation);
      client.setQueryData(["heinzel", "session", saved.session_id], saved);
      setSelected(saved.session_id);
      await client.invalidateQueries({ queryKey: sessionsKey });
    } catch {
      setCreationError(true);
    } finally {
      creatingNow.current = false;
      setCreating(false);
    }
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
        <label className="block space-y-1 text-sm">
          <span>{t("personal.choose")}</span>
          <select
            className={field}
            value={selected ?? ""}
            disabled={locked || creating}
            onChange={(event) => setSelected(event.target.value || null)}
          >
            <option value="">{t("personal.select")}</option>
            {(sessions.data.sessions ?? []).map((item) => (
              <option key={item.session_id} value={item.session_id}>
                {item.name}
              </option>
            ))}
          </select>
        </label>
      )}
      <Button type="button" variant="outline" disabled={creating || locked} onClick={() => void create()}>
        {creationError ? t("personal.retryCreate") : t("personal.create")}
      </Button>
      {creationError && <p role="alert">{t("personal.createError")}</p>}
      {selected && session.isPending && <p role="status">{t("state.loading")}</p>}
      {selected && session.isError && <p role="alert">{t("personal.loadError")}</p>}
      {selected && session.data && <ProfileEditor key={selected} initial={session.data} onLock={setLocked} />}
    </section>
  );
}

export function ProfileEditor({
  initial,
  onLock,
  inChat = false,
}: {
  initial: SchemaSessionSnapshot;
  onLock: (locked: boolean) => void;
  inChat?: boolean;
}) {
  const client = useQueryClient();
  const navigate = useNavigate();
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
  useEffect(() => {
    onLock(hasUncommittedChanges || pending !== null || pendingStorageInvalid || busy);
  }, [hasUncommittedChanges, pending, pendingStorageInvalid, busy, onLock]);
  return (
    <form
      className="space-y-3 border-t border-border pt-4"
      onSubmit={(event) => {
        event.preventDefault();
        void save();
      }}
    >
      <div className="flex items-center gap-4">
        <HeinzelAvatar
          label={t("personal.preview")}
          age={look.age}
          beard={look.beard ?? undefined}
          build={look.build}
          hat={look.hat ?? undefined}
          shirt={look.shirt ?? undefined}
          accessories={look.accessories}
          className="h-24 w-16"
        />
        <p className="text-sm text-muted-foreground">{t("personal.previewHelp")}</p>
      </div>
      <fieldset disabled={disabled} className="grid gap-3 sm:grid-cols-2">
        <label className="space-y-1 text-sm">
          <span>{t("personal.name")}</span>
          <input
            className={field}
            value={name}
            required
            maxLength={100}
            onChange={(event) => setName(event.target.value)}
          />
        </label>
        <label className="space-y-1 text-sm">
          <span>{t("personal.age")}</span>
          <select
            className={field}
            value={look.age}
            onChange={(event) => setLook({ ...look, age: event.target.value as SchemaHeinzelAppearance["age"] })}
          >
            {AGES.map((item) => (
              <option key={item} value={item}>
                {item}
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
              setLook({ ...look, beard: (event.target.value || null) as SchemaHeinzelAppearance["beard"] })
            }
          >
            <option value="">{t("personal.ageDefault")}</option>
            {BEARDS.map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
        </label>
        <label className="space-y-1 text-sm">
          <span>{t("personal.build")}</span>
          <select
            className={field}
            value={look.build}
            onChange={(event) => setLook({ ...look, build: event.target.value as SchemaHeinzelAppearance["build"] })}
          >
            {BUILDS.map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
        </label>
        <label className="space-y-1 text-sm">
          <span>{t("personal.hat")}</span>
          <select
            className={field}
            value={look.hat ?? ""}
            onChange={(event) =>
              setLook({ ...look, hat: (event.target.value || null) as SchemaHeinzelAppearance["hat"] })
            }
          >
            <option value="">{t("personal.themeDefault")}</option>
            {Object.keys(HATS).map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
        </label>
        <label className="space-y-1 text-sm">
          <span>{t("personal.shirt")}</span>
          <select
            className={field}
            value={look.shirt ?? ""}
            onChange={(event) =>
              setLook({ ...look, shirt: (event.target.value || null) as SchemaHeinzelAppearance["shirt"] })
            }
          >
            <option value="">{t("personal.themeDefault")}</option>
            {Object.keys(SHIRTS).map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
        </label>
        <fieldset className="space-y-1 text-sm">
          <legend>{t("personal.accessories")}</legend>
          {ACCESSORIES.map((accessory) => (
            <label key={accessory} className="mr-3 inline-flex items-center gap-1">
              <input
                type="checkbox"
                checked={(look.accessories ?? []).includes(accessory)}
                onChange={(event) =>
                  setLook({
                    ...look,
                    accessories: event.target.checked
                      ? [...(look.accessories ?? []), accessory]
                      : (look.accessories ?? []).filter((item) => item !== accessory),
                  })
                }
              />
              {accessory}
            </label>
          ))}
        </fieldset>
      </fieldset>
      {error && <p role="alert">{t("personal.saveError")}</p>}
      {pendingNeedsReload && <p role="alert">{t("personal.saveOutcomeChanged")}</p>}
      {draftStorageFailed && <p role="status">{t("personal.draftStorageFailed")}</p>}
      <div className="flex flex-wrap gap-2">
        <Button type="submit" disabled={busy || pendingStorageInvalid || (!pending && !name.trim())}>
          {pending ? t("personal.retrySave") : t("personal.save")}
        </Button>
        {(pending || pendingStorageInvalid) && (
          <Button type="button" variant="outline" disabled={busy} onClick={() => void reload()}>
            {t("personal.reload")}
          </Button>
        )}
      </div>
      {hasUncommittedChanges && <p className="text-sm text-muted-foreground">{t("personal.saveBeforeSchedule")}</p>}
      {!inChat && (
        <Button
          type="button"
          variant="outline"
          disabled={busy || pending !== null || hasUncommittedChanges}
          onClick={() => navigate(`/heinzel?chat=${encodeURIComponent(snapshot.session_id)}`)}
        >
          {t("personal.openSchedule")}
        </Button>
      )}
    </form>
  );
}
